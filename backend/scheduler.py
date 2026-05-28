"""
Shared MQTT client and on-demand broker fetches.

On app startup a single FlowMetersMQTT instance connects to the broker (no login).
After the user logs in via /api/auth/login, each API request applies that user's
MQTT token and fetches data once from the broker when needed:

  - "All devices" view  → wm/device/dashboard/data/list (hourly DI+ total for all devices)
  - Device dropdown     → wm/realtime/device/list
  - Single device view  → wm/device/cumulative/data/list
"""

import logging
import os
import sys
import json
from datetime import datetime
from typing import Optional

# Allow importing mqtt_client from the project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from mqtt_client import FlowMetersMQTT

from .db import (
    CumulativeDataPoint,
    SessionLocal,
    create_tables,
)

logger = logging.getLogger(__name__)

BROKER_URL = os.getenv("BROKER_URL", "ws://localhost:8083/mqtt")

_mqtt_client: Optional[FlowMetersMQTT] = None


def get_mqtt_client() -> FlowMetersMQTT:
    """Return the shared MQTT client (raises if not initialised)."""
    if _mqtt_client is None:
        raise RuntimeError("MQTT client not initialised. Call startup() first.")
    return _mqtt_client


def apply_user_token(current_user: dict) -> FlowMetersMQTT:
    """Set the broker auth token from the logged-in user's JWT."""
    client = get_mqtt_client()
    token = current_user.get("mqtt_token")
    if not token:
        raise RuntimeError("No MQTT token in session.")
    client.token = token
    return client


# ---------------------------------------------------------------------------
# Helper — safe float conversion
# ---------------------------------------------------------------------------

def _to_float(value) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _extract_items(data: dict | list) -> list[dict]:
    if isinstance(data, list):
        return data
    return data.get("items") or data.get("list") or data.get("records") or []


def _metric_key_for_param(param_name: str) -> str:
    """
    Broker cumulative API sometimes returns generic {createTime, paramValue, paramName}.
    Map the selected paramName onto our response metric field so the frontend chart
    can plot the chosen series without needing to know broker-specific keys.
    """
    p = (param_name or "").upper()
    if p in {"DI+", "DI-", "DIN"}:
        return "flow"
    if p == "DQ":
        return "instantaneousFlow"
    if p == "DV":
        return "instantaneousVelocity"
    if p == "TI":
        return "waterTemperature"
    if p == "TO":
        return "returnWaterTemperature"
    if p == "TH":
        return "accumulatedCooling"
    if p in {"RH", "EQH", "THEAT"}:
        return "heat"
    return "flow"


def _device_from_realtime_item(item: dict) -> Optional[dict]:
    device_id = item.get("deviceId") or item.get("id")
    if device_id is None:
        return None
    return {
        "id": device_id,
        "name": item.get("deviceName") or item.get("name"),
        "serialNo": item.get("serialNo") or item.get("serial"),
        "productName": item.get("productName") or item.get("belongsTo"),
        "groupName": item.get("groupName"),
        "fetchedAt": None,
    }


def _create_time_to_iso(create_time: str, ref: datetime) -> str:
    """
    Convert broker createTime into an ISO datetime string (UTC-ish, with 'Z').

    Observed broker formats vary by dashboard category, e.g.
      - "07:00"           (hour bucket for Today/Yesterday)
      - "2026-05-01"      (date bucket for Past Month/Year)
      - "2026-02"         (month bucket for Past Year)
      - "2026-05-01 07:00:00" / "2026-05-01 07:00"
    """
    raw = str(create_time).strip()
    if not raw:
        return ref.replace(microsecond=0).isoformat()# + "Z"

    # Month bucket: YYYY-MM
    # Normalize to first day of month so ECharts time axis can plot it.
    if "-" in raw and ":" not in raw and len(raw) == 7:
        try:
            dt = datetime.strptime(raw, "%Y-%m").replace(tzinfo=None)
            return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()# + "Z"
        except ValueError:
            pass

    # Date bucket: YYYY-MM-DD
    if "-" in raw and ":" not in raw:
        try:
            dt = datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=None)
            return dt.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()# + "Z"
        except ValueError:
            pass

    # Datetime bucket: YYYY-MM-DD HH:MM[:SS]
    if "-" in raw and ":" in raw:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                dt = datetime.strptime(raw, fmt).replace(tzinfo=None)
                return dt.replace(microsecond=0).isoformat()# + "Z"
            except ValueError:
                continue

    # Hour bucket: HH[:MM[:SS]] combined with ref date
    parts = raw.split(":")
    try:
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
        second = int(parts[2]) if len(parts) > 2 else 0
        dt = ref.replace(hour=hour, minute=minute, second=second, microsecond=0)
        return dt.isoformat()# + "Z"
    except (TypeError, ValueError):
        # Last resort: don't crash the whole request if broker sends an unexpected shape
        return ref.replace(microsecond=0).isoformat()# + "Z"


def _is_aggregate_hourly_row(item: dict) -> bool:
    return "createTime" in item and "paramValue" in item


def _filter_dashboard_items_for_category(items: list[dict], fetched_at: datetime, category: int) -> list[dict]:
    """
    The broker returns fixed-length buckets for some dashboard categories:
      - Past Month (category=3): returns all days in current month, including future days.
      - Past Year  (category=4): returns 12 months, including future months.
    Discard the future buckets so the chart only shows "real" data.
    """
    if category not in (3, 4):
        return items

    out: list[dict] = []
    today = fetched_at.date()

    if category == 3:
        # Expect createTime like "YYYY-MM-DD"
        for it in items:
            raw = str(it.get("createTime", "")).strip()
            try:
                d = datetime.strptime(raw, "%Y-%m-%d").date()
            except Exception:
                continue
            out.append(it)
            if d.year == today.year and d.month == today.month and d == today:
                break
        return out

    # category == 4
    # Expect createTime like "YYYY-MM"
    for it in items:
        raw = str(it.get("createTime", "")).strip()
        try:
            dt = datetime.strptime(raw, "%Y-%m")
        except Exception:
            continue
        out.append(it)
        if dt.year == today.year and dt.month == today.month:
            break
    return out


def _dashboard_point_from_item(item: dict, fetched_at: datetime) -> dict:
    """
    Map wm/device/dashboard/data/list rows.

    The broker returns today's hourly positive-accumulation (DI+) totals
    across all devices: { createTime, paramValue, paramName }.
    """
    if _is_aggregate_hourly_row(item):
        param_name = item.get("paramName") or "DI+"
        issue_date = _create_time_to_iso(item["createTime"], fetched_at)
        return {
            "deviceId": None,
            "deviceName": f"All devices — {param_name}",
            "serialNo": None,
            "paramName": param_name,
            "flow": _to_float(item.get("paramValue")),
            "instantaneousFlow": None,
            "instantaneousVelocity": None,
            "waterTemperature": None,
            "accumulatedCooling": None,
            "heat": None,
            "issueDate": issue_date,
            "fetchedAt": issue_date,
        }

    issue = item.get("issueDate") or item.get("statisticsDate")
    fetched = issue or fetched_at.isoformat() + "Z"
    return {
        "deviceId": item.get("deviceId") or item.get("id"),
        "deviceName": item.get("deviceName"),
        "serialNo": item.get("serialNo"),
        "paramName": item.get("paramName"),
        "flow": _to_float(item.get("flow")),
        "instantaneousFlow": _to_float(item.get("instantaneousFlow")),
        "instantaneousVelocity": _to_float(item.get("instantaneousVelocity")),
        "waterTemperature": _to_float(item.get("waterTemperature")),
        "accumulatedCooling": _to_float(item.get("accumulatedCooling")),
        "heat": _to_float(item.get("heat")),
        "issueDate": issue,
        "fetchedAt": fetched,
    }


def fetch_devices_list(current_user: dict) -> list[dict]:
    """Device dropdown entries from wm/realtime/device/list."""
    client = apply_user_token(current_user)
    page_size = 100
    page_row = 1
    all_items: list[dict] = []

    while True:
        data = client.get_realtime_device_list(page_size=page_size, page_row=page_row)
        items = _extract_items(data)
        all_items.extend(items)
        total = data.get("total", len(all_items)) if isinstance(data, dict) else len(all_items)
        if len(all_items) >= total or not items:
            break
        page_row += 1

    devices_by_id: dict[int, dict] = {}
    for item in all_items:
        device = _device_from_realtime_item(item)
        if device:
            devices_by_id[device["id"]] = device

    return sorted(devices_by_id.values(), key=lambda d: (d.get("name") or "").lower())


# ---------------------------------------------------------------------------
# On-demand fetches
# ---------------------------------------------------------------------------

def fetch_dashboard_data(
    current_user: dict,
    *,
    param_name: str = "DI+",
    category: int = 1,
) -> dict:
    """
    All-devices overview: hourly DI+ totals from wm/device/dashboard/data/list,
    plus the device list from wm/realtime/device/list for the dropdown.
    Returns { "devices": [...], "points": [...] }.
    """
    client = apply_user_token(current_user)
    logger.info("Dashboard broker fetch: paramName=%s category=%s", param_name, category)
    try:
        data = client.get_dashboard_device_list(param_name=param_name, category=category)
        print(f"dashboard data: {json.dumps(data, indent=2)}")
    except Exception as exc:
        # This is where broker/mqtt errors show up (timeout, non-zero code, transport issues).
        logger.exception(
            "Dashboard broker fetch FAILED: paramName=%s category=%s error=%s",
            param_name,
            category,
            str(exc),
        )
        raise

    # Log a tiny bit of shape info for debugging without spamming logs
    try:
        if isinstance(data, dict):
            keys = sorted(list(data.keys()))
            logger.info("Dashboard broker response keys: %s", keys)
            items_preview = data.get("items") or data.get("list") or data.get("records") or []
            if isinstance(items_preview, list):
                logger.info("Dashboard broker response items: %d", len(items_preview))
    except Exception:
        # Never let debug logging break the request.
        pass

    items = _extract_items(data)
    fetched_at = datetime.utcnow()

    original_len = len(items)
    print(f"original items: {json.dumps(items, indent=2)}")
    items = _filter_dashboard_items_for_category(items, fetched_at, category)
    print(f"filtered items: {json.dumps(items, indent=2)}")
    if len(items) != original_len:
        logger.info(
            "Dashboard category filter applied: category=%s kept=%d discarded=%d",
            category,
            len(items),
            original_len - len(items),
        )

    points = [_dashboard_point_from_item(item, fetched_at) for item in items]
    devices = fetch_devices_list(current_user)

    logger.info(
        "Dashboard fetch: %d device(s), %d hourly total(s)",
        len(devices),
        len(points),
    )
    return {"devices": devices, "points": points}


def fetch_cumulative_for_device(
    current_user: dict,
    device_id: int,
    param_name: str = "DI+",
    category: int = 0,
    page_size: int = 200,
) -> list[dict]:
    """
    Fetch wm/device/cumulative/data/list once for a single device and persist to DB.
    Returns the list of points for the API response.
    """
    client = apply_user_token(current_user)
    data = client.get_cumulative_data_list(
        device_id=device_id,
        param_name=param_name,
        category=category,
        page_size=page_size,
    )
    items = _extract_items(data)

    db = SessionLocal()
    try:
        now = datetime.utcnow()
        metric_key = _metric_key_for_param(param_name)
        for item in items:
            issue_date = (
                item.get("issueDate")
                or item.get("statisticsDate")
                or item.get("createTime")
                or item.get("create_date")
            )
            # Some broker responses use {paramValue} instead of typed metric fields.
            param_value = _to_float(item.get("paramValue"))
            flow = _to_float(item.get("flow")) if param_value is None else (param_value if metric_key == "flow" else None)
            instantaneous_flow = (
                _to_float(item.get("instantaneousFlow"))
                if param_value is None
                else (param_value if metric_key == "instantaneousFlow" else None)
            )
            instantaneous_velocity = (
                _to_float(item.get("instantaneousVelocity"))
                if param_value is None
                else (param_value if metric_key == "instantaneousVelocity" else None)
            )
            water_temperature = (
                _to_float(item.get("waterTemperature"))
                if param_value is None
                else (param_value if metric_key == "waterTemperature" else None)
            )
            return_water_temperature = (
                _to_float(item.get("returnWaterTemperature"))
                if param_value is None
                else (param_value if metric_key == "returnWaterTemperature" else None)
            )
            accumulated_cooling = (
                _to_float(item.get("accumulatedCooling"))
                if param_value is None
                else (param_value if metric_key == "accumulatedCooling" else None)
            )
            heat = _to_float(item.get("heat")) if param_value is None else (param_value if metric_key == "heat" else None)
            existing = (
                db.query(CumulativeDataPoint)
                .filter_by(device_id=device_id, issue_date=issue_date, param_name=param_name)
                .first()
            )
            if existing:
                existing.flow = flow
                existing.instantaneous_flow = instantaneous_flow
                existing.instantaneous_velocity = instantaneous_velocity
                existing.water_temperature = water_temperature
                existing.return_water_temperature = return_water_temperature
                existing.accumulated_cooling = accumulated_cooling
                existing.heat = heat
                existing.fetched_at = now
            else:
                db.add(
                    CumulativeDataPoint(
                        device_id=device_id,
                        device_name=item.get("deviceName"),
                        serial_no=item.get("serialNo"),
                        param_name=param_name,
                        issue_date=issue_date,
                        flow=flow,
                        instantaneous_flow=instantaneous_flow,
                        instantaneous_velocity=instantaneous_velocity,
                        water_temperature=water_temperature,
                        return_water_temperature=return_water_temperature,
                        accumulated_cooling=accumulated_cooling,
                        heat=heat,
                        fetched_at=now,
                    )
                )
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error("DB write failed (cumulative device %d): %s", device_id, exc)
    finally:
        db.close()

    fetched_at = datetime.utcnow().isoformat()
    print(f"cumulative data: {json.dumps(items, indent=2)}")
    logger.info(": device %d, %d row(s)", device_id, len(items))
    return [
        {
            "deviceId": device_id,
            "deviceName": item.get("deviceName"),
            "serialNo": item.get("serialNo"),
            "paramName": param_name,
            "issueDate": item.get("issueDate")
            or item.get("statisticsDate")
            or item.get("createTime")
            or item.get("create_date"),
            "flow": (
                _to_float(item.get("flow"))
                if _to_float(item.get("paramValue")) is None
                else (_to_float(item.get("paramValue")) if _metric_key_for_param(param_name) == "flow" else None)
            ),
            "instantaneousFlow": (
                _to_float(item.get("instantaneousFlow"))
                if _to_float(item.get("paramValue")) is None
                else (
                    _to_float(item.get("paramValue"))
                    if _metric_key_for_param(param_name) == "instantaneousFlow"
                    else None
                )
            ),
            "instantaneousVelocity": (
                _to_float(item.get("instantaneousVelocity"))
                if _to_float(item.get("paramValue")) is None
                else (
                    _to_float(item.get("paramValue"))
                    if _metric_key_for_param(param_name) == "instantaneousVelocity"
                    else None
                )
            ),
            "waterTemperature": (
                _to_float(item.get("waterTemperature"))
                if _to_float(item.get("paramValue")) is None
                else (
                    _to_float(item.get("paramValue"))
                    if _metric_key_for_param(param_name) == "waterTemperature"
                    else None
                )
            ),
            "returnWaterTemperature": (
                _to_float(item.get("returnWaterTemperature"))
                if _to_float(item.get("paramValue")) is None
                else (
                    _to_float(item.get("paramValue"))
                    if _metric_key_for_param(param_name) == "returnWaterTemperature"
                    else None
                )
            ),
            "accumulatedCooling": (
                _to_float(item.get("accumulatedCooling"))
                if _to_float(item.get("paramValue")) is None
                else (
                    _to_float(item.get("paramValue"))
                    if _metric_key_for_param(param_name) == "accumulatedCooling"
                    else None
                )
            ),
            "heat": (
                _to_float(item.get("heat"))
                if _to_float(item.get("paramValue")) is None
                else (_to_float(item.get("paramValue")) if _metric_key_for_param(param_name) == "heat" else None)
            ),
            "fetchedAt": fetched_at,
        }
        for item in items
    ]


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

def startup() -> None:
    """Called from FastAPI lifespan on startup."""
    global _mqtt_client

    create_tables()

    logger.info("Connecting to MQTT broker: %s", BROKER_URL)
    _mqtt_client = FlowMetersMQTT(broker_url=BROKER_URL, token="")
    _mqtt_client.connect(timeout=15.0)
    logger.info("MQTT client connected (awaiting user login)")


def shutdown() -> None:
    """Called from FastAPI lifespan on shutdown."""
    global _mqtt_client
    if _mqtt_client:
        try:
            _mqtt_client.disconnect()
        except Exception:
            pass
    logger.info("MQTT client shut down.")
