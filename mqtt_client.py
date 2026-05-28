"""
FlowMeters MQTT Client
======================
Replicates the request/response pattern used by the app.js frontend.

Authentication is done entirely over MQTT (no HTTP login endpoint exists).
The login flow is:
  1. Connect to the broker with username="web" / password="web"
  2. Publish to topic "wm/user/login" with { username, password } in `data`
     and an empty token (not authenticated yet)
  3. The broker responds on "wm/response/<clientId>" with { code: 0, data: { token: "..." } }
  4. Use that token in all subsequent requests

Status codes (from module "59c5" in app.js):
  0  = SUCCESS
  1  = ERROR
  3  = TOKEN_EXPIRE
  -1 = NOT_CONNECT
  -2 = TIMEOUT

Usage
-----
1. Activate the virtual environment:
       source venv/bin/activate
2. Run interactively (prompts for broker URL and credentials):
       python mqtt_client.py
3. Or pass everything via arguments:
       python mqtt_client.py --broker ws://host:8083/mqtt \
                             --username admin --password secret
4. Use an existing token (skip login):
       python mqtt_client.py --broker ws://host:8083/mqtt --token eyJ...
5. Poll realtime sensor/device data (same as the monitor UI, every 5 s):
       python mqtt_client.py --broker ws://host:8083/mqtt \
                             --username admin --password secret \
                             --poll-realtime
6. Fetch realtime device list once:
       python mqtt_client.py --broker ws://host:8083/mqtt --token eyJ... \
                             --realtime-once --group-id 1
7. Fetch cumulative data for a specific device (deviceId is required):
       python mqtt_client.py --broker ws://host:8083/mqtt --token eyJ... \
                             --cumulative-data --device-id 42 \
                             --param-name DI+ \
                             --start-time "2026-01-01 00:00:00" \
                             --end-time "2026-05-26 23:59:59"
8. Fetch cumulative totals across ALL devices (no deviceId needed):
       python mqtt_client.py --broker ws://host:8083/mqtt --token eyJ... \
                             --cumulative-totals --param-name DI+ \
                             --start-time "2026-01-01 00:00:00" \
                             --end-time "2026-05-26 23:59:59"
"""

import argparse
import json
import sys
import threading
import time
import uuid
from datetime import datetime
from typing import Any, Callable, Optional

import paho.mqtt.client as mqtt


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def generate_uuid() -> str:
    """Replicates the f() function in app.js — standard UUID v4."""
    return str(uuid.uuid4())


# User / auth topics (module "c24f" / "0f9a" in app.js)
TOPIC_USER_INFO        = "wm/user/info"
TOPIC_USER_STATISTICS  = "wm/user/statistics"

# Resource / permission topics (module "4ed9" / permission store in app.js)
TOPIC_RESOURCE_TREE    = "wm/resource/tree"
# category values from module "4ed9": CATEGORY_ALL_RESOURCE=1, CATEGORY_USER_RESOURCE=0
CATEGORY_USER_RESOURCE = 0
CATEGORY_ALL_RESOURCE  = 1

# Dashboard topics (dashboard lazy-chunk: chunk-05cd96e2 + chunk-19a9fd10)
TOPIC_DASHBOARD_DEVICE_LIST = "wm/device/dashboard/data/list"

# Cumulative / historical data topics (dataChart lazy-chunks: chunk-5afbaf7e / chunk-324ddeef)
# Lists accumulated meter readings by date (DeviceName, SerialNo, Issuedate/Statistics Date,
# InstantaneousFlow, AccumulatedCoolingCapacity, Heat, Flow, … — from i18n in app.js)
TOPIC_CUMULATIVE_DATA_LIST = "wm/device/cumulative/data/list"

# Realtime monitor topics (module "7cf8" in chunk.js)
TOPIC_REALTIME_DEVICE_LIST = "wm/realtime/device/list"
TOPIC_REALTIME_DEVICE_SETTING = "wm/realtime/device/setting"
TOPIC_REALTIME_DEVICE_PARAM_LIST = "wm/realtime/device/param/list"

# Monitor device list polls every 5 s in app.js (loadLooper, 5e3 ms)
DEFAULT_REALTIME_POLL_INTERVAL = 5.0

# Common flow-meter param codes shown in the monitor UI
REALTIME_PARAM_LABELS = {
    "DQ": "Instantaneous flow",
    "DV": "Instantaneous velocity",
    "TI": "Inlet water temp",
    "TO": "Outlet water temp",
    "RH": "Instantaneous cooling",
    "TH": "Accumulated cooling",
    "EQH": "Instantaneous heat",
    "THEAT": "Cumulative heat",
    "DI+": "Positive accumulation",
    "DI-": "Negative accumulation",
    "DIN": "Net accumulation",
    "DL": "Q value",
}


# ---------------------------------------------------------------------------
# MQTT client wrapper
# ---------------------------------------------------------------------------

class FlowMetersMQTT:
    """
    Mirrors the MQTT module (module "4b80") from app.js.

    Payload format for every published message:
        {
            "clientId":  "<uuid — fixed per session>",
            "requestId": "<uuid — unique per request>",
            "token":     "<auth token from login>",
            "data":      { ... }
        }

    Response topic:  wm/response/<clientId>
    Request topics:  wm/<resource>  (e.g. wm/resource/tree, wm/device/list)
    """

    # Status codes from module "59c5" in app.js
    CODE_SUCCESS      =  0
    CODE_ERROR        =  1
    CODE_TOKEN_EXPIRE =  3
    CODE_NOT_CONNECT  = -1
    CODE_TIMEOUT      = -2

    def __init__(self, broker_url: str, token: str, debug: bool = False):
        """
        Parameters
        ----------
        broker_url : str
            Full URL to the MQTT broker, e.g.
              ws://host:8083/mqtt  (WebSocket)
              mqtt://host:1883     (plain TCP)
        token : str
            Auth token returned by the login endpoint.
        debug : bool
            Print every raw incoming message.
        """
        self.broker_url = broker_url
        self.token = token
        self.debug = debug
        self.client_id = generate_uuid()
        self.response_topic = f"wm/response/{self.client_id}"

        # requestId  ->  callback(response_dict)
        self._pending: dict[str, Callable] = {}
        # Non-request/response callbacks:  topic -> callback(payload_dict)
        self._push_callbacks: dict[str, Callable] = {}

        self._connected = threading.Event()
        self._lock = threading.Lock()

        # Build and configure the paho client
        transport = "websockets" if broker_url.startswith("ws") else "tcp"
        self._client = mqtt.Client(
            client_id=self.client_id,
            transport=transport,
        )
        self._client.username_pw_set("web", "web")   # broker-level credentials
        self._client.on_connect    = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message    = self._on_message
        self._client.reconnect_delay_set(min_delay=4, max_delay=4)

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect(self, timeout: float = 10.0) -> None:
        """Connect to the broker and block until the connection is established."""
        url = self.broker_url

        # Parse host / port / path from the URL
        if "://" in url:
            scheme, rest = url.split("://", 1)
        else:
            scheme, rest = "mqtt", url

        if "/" in rest:
            host_port, path = rest.split("/", 1)
            path = "/" + path
        else:
            host_port = rest
            path = "/mqtt"

        host, port = (host_port.split(":") + ["1883"])[:2]
        port = int(port)

        if scheme in ("ws", "wss"):
            self._client.ws_set_options(path=path)
            if scheme == "wss":
                self._client.tls_set()

        print(f"[mqtt] Connecting to {host}:{port} (clientId={self.client_id})")
        self._client.connect(host, port, keepalive=60)
        self._client.loop_start()

        if not self._connected.wait(timeout=timeout):
            self._client.loop_stop()
            raise TimeoutError(f"Could not connect to broker within {timeout}s")

    def disconnect(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()
        print("[mqtt] Disconnected.")

    # ------------------------------------------------------------------
    # Sending requests
    # ------------------------------------------------------------------

    def mqtt_login(self, username: str, password: str, timeout: float = 30.0) -> str:
        """
        Authenticates over MQTT via topic "wm/user/login".
        This is the real login flow used by the frontend — there is no HTTP login.
        Returns the auth token string and stores it in self.token.
        """
        print(f"[login] Sending credentials to wm/user/login ...")
        response = self.request_sync(
            "wm/user/login",
            {"userName": username, "password": password},
            timeout=timeout,
        )
        if response is None:
            raise TimeoutError("Login request timed out.")

        code = response.get("code")
        if code != self.CODE_SUCCESS:
            print(f"\n[login] Full server response: {json.dumps(response, ensure_ascii=False, indent=2)}")
            raise PermissionError(
                f"Login failed (code={code}): {response.get('msg', '(no message)')}"
            )

        token = response.get("data", {}).get("token")
        if not token:
            print(f"\n[login] Full server response: {json.dumps(response, ensure_ascii=False, indent=2)}")
            raise ValueError(f"No token in login response.")

        self.token = token
        print("[login] Authenticated successfully. Token stored.")
        return token

    def request(
        self,
        topic: str,
        data: dict,
        callback: Callable[[dict], None],
        timeout: float = 30.0,
    ) -> str:
        """
        Publish a request to `topic` and call `callback(response)` when the
        broker replies on wm/response/<clientId>.

        Returns the requestId.
        """
        request_id = generate_uuid()
        # token is null before login (confirmed via Wireshark capture)
        payload = json.dumps({
            "clientId":  self.client_id,
            "requestId": request_id,
            "token":     self.token if self.token else None,
            "data":      data,
        })

        with self._lock:
            self._pending[request_id] = callback

        # Schedule a 30-second timeout (mirrors app.js behaviour)
        def _expire():
            with self._lock:
                if request_id in self._pending:
                    print(f"[mqtt] Request timed out  topic={topic}  requestId={request_id}")
                    del self._pending[request_id]

        timer = threading.Timer(timeout, _expire)
        timer.daemon = True
        timer.start()

        self._client.publish(topic, payload, qos=2, retain=False)
        print(f"[mqtt] Published topic={topic} payload={payload} requestId={request_id}")
        return request_id

    def request_sync(
        self,
        topic: str,
        data: dict,
        timeout: float = 30.0,
    ) -> Optional[dict]:
        """
        Blocking version of request().
        Returns the response dict, or None on timeout.
        """
        event = threading.Event()
        result: list[Optional[dict]] = [None]

        def _cb(response: dict):
            result[0] = response
            event.set()

        self.request(topic, data, _cb, timeout=timeout)
        event.wait(timeout=timeout + 1)
        return result[0]

    def _require_success(self, response: Optional[dict], context: str) -> dict:
        """Return response['data'] or raise on timeout / error code."""
        if response is None:
            raise TimeoutError(f"{context}: no response within timeout (30 s)")
        code = response.get("code")
        if code != self.CODE_SUCCESS:
            print(f"\n[error] Full broker response for {context}:")
            print(json.dumps(response, indent=2, ensure_ascii=False))
            raise RuntimeError(
                f"{context} failed (code={code}): {response.get('msg', '(no message)')}"
            )
        return response.get("data") or {}

    @staticmethod
    def _realtime_list_payload(
        *,
        group_id: Optional[int] = None,
        product_id: Optional[int] = None,
        page_size: int = 20,
        page_row: int = 1,
        search: str = "",
    ) -> dict:
        """Build payload for wm/realtime/device/list (monitor device list in chunk.js)."""
        data: dict[str, Any] = {
            "pageSize": page_size,
            "pageRow": page_row,
            "search": search,
        }
        if group_id is not None:
            data["groupId"] = group_id
        if product_id is not None:
            data["productId"] = product_id
        return data

    def get_realtime_device_list(
        self,
        *,
        group_id: Optional[int] = None,
        product_id: Optional[int] = None,
        page_size: int = 20,
        page_row: int = 1,
        search: str = "",
        timeout: float = 30.0,
    ) -> dict:
        """
        Fetch realtime device/sensor rows (wm/realtime/device/list).
        Returns the ``data`` object: typically ``{ "items": [...], "total": N }``.
        """
        payload = self._realtime_list_payload(
            group_id=group_id,
            product_id=product_id,
            page_size=page_size,
            page_row=page_row,
            search=search,
        )
        response = self.request_sync(
            TOPIC_REALTIME_DEVICE_LIST, payload, timeout=timeout
        )
        return self._require_success(response, TOPIC_REALTIME_DEVICE_LIST)

    def get_realtime_device_setting(
        self, data: dict, timeout: float = 30.0
    ) -> dict:
        """wm/realtime/device/setting"""
        response = self.request_sync(
            TOPIC_REALTIME_DEVICE_SETTING, data, timeout=timeout
        )
        return self._require_success(response, TOPIC_REALTIME_DEVICE_SETTING)

    def get_realtime_device_param_list(
        self, data: dict, timeout: float = 30.0
    ) -> dict:
        """wm/realtime/device/param/list"""
        response = self.request_sync(
            TOPIC_REALTIME_DEVICE_PARAM_LIST, data, timeout=timeout
        )
        return self._require_success(response, TOPIC_REALTIME_DEVICE_PARAM_LIST)

    # ------------------------------------------------------------------
    # User / auth queries
    # ------------------------------------------------------------------

    def get_user_info(self, timeout: float = 30.0) -> dict:
        """
        Fetch the currently authenticated user's profile (wm/user/info).
        Returns data with: userId, userName, roleId, isAdmin, isSubUser, description, …
        Mirrors the Vuex user/getInfo action (app.js line 162).
        """
        response = self.request_sync(TOPIC_USER_INFO, {}, timeout=timeout)
        return self._require_success(response, TOPIC_USER_INFO)

    def get_user_statistics(self, timeout: float = 30.0) -> dict:
        """
        Fetch user/account statistics shown on the Dashboard (wm/user/statistics).
        Returns aggregate counts — e.g. total users, sub-users, devices, etc.
        Mirrors the statistics() call in the dashboard lazy-chunk (app.js line 5007).
        """
        response = self.request_sync(TOPIC_USER_STATISTICS, {}, timeout=timeout)
        return self._require_success(response, TOPIC_USER_STATISTICS)

    # ------------------------------------------------------------------
    # Resource / permission tree
    # ------------------------------------------------------------------

    def get_resource_tree(
        self,
        category: int = CATEGORY_USER_RESOURCE,
        timeout: float = 30.0,
    ) -> dict:
        """
        Fetch the menu/permission resource tree (wm/resource/tree).
        category=0 (CATEGORY_USER_RESOURCE) → routes for the current user (default).
        category=1 (CATEGORY_ALL_RESOURCE)  → full tree (admin use).
        Mirrors the permission/generateRoutes Vuex action (app.js line 567).
        """
        response = self.request_sync(
            TOPIC_RESOURCE_TREE, {"category": category}, timeout=timeout
        )
        return self._require_success(response, TOPIC_RESOURCE_TREE)

    # ------------------------------------------------------------------
    # Dashboard device list
    # ------------------------------------------------------------------

    def get_dashboard_device_list(
        self,
        *,
        param_name: str = "DI+",
        category: int = 2,
        page_size: Optional[int] = None,
        page_row: Optional[int] = None,
        timeout: float = 30.0,
        **extra,
    ) -> dict:
        """
        Fetch today's hourly positive-accumulation (DI+) totals for all devices
        (wm/device/dashboard/data/list).

        Response is a list of ``{createTime, paramValue, paramName}`` — one row per
        hour, not per device. For the device dropdown use get_realtime_device_list().
        Default broker payload: ``{"paramName": "DI+", "category": 2}``.
        """
        data: dict = {
            "paramName": param_name,
            "category": category,
            **extra,
        }
        if page_size is not None:
            data["pageSize"] = page_size
        if page_row is not None:
            data["pageRow"] = page_row
        response = self.request_sync(
            TOPIC_DASHBOARD_DEVICE_LIST, data, timeout=timeout
        )
        return self._require_success(response, TOPIC_DASHBOARD_DEVICE_LIST)

    # ------------------------------------------------------------------
    # Cumulative / historical meter data
    # ------------------------------------------------------------------

    def get_cumulative_data_list(
        self,
        *,
        device_id: int,
        param_name: str = "DI+",
        category: int = 0,
        page_size: int = 20,
        page_row: int = 1,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        group_id: Optional[int] = None,
        product_id: Optional[int] = None,
        search: str = "",
        timeout: float = 30.0,
        **extra,
    ) -> dict:
        """
        Fetch accumulated/historical meter readings for a specific device
        (wm/device/cumulative/data/list).

        Required broker fields:
            device_id : int  — the device to query; the broker rejects requests
                               that omit this field.
            paramName : str  — which parameter to aggregate, e.g. "DI+" (positive
                               accumulation), "DI-" (negative), "DIN" (net).
                               See REALTIME_PARAM_LABELS for the full list of codes.
            category  : int  — 0=Daily, 1=Monthly, 2=Yearly.

        Optional filters:
            start_time / end_time : 'YYYY-MM-DD HH:MM:SS' statistics date range.
            group_id / product_id : further filter by group or product.
            search    : free-text device name / serial number search.

        For totals across ALL devices (no deviceId required) use
        get_cumulative_totals() which queries wm/device/dashboard/data/list.
        """
        data: dict = {
            "deviceId":  device_id,
            "paramName": param_name,
            "category":  category,
            **extra,
        }
        if start_time is not None:
            data["startTime"] = start_time
        if end_time is not None:
            data["endTime"] = end_time
        if group_id is not None:
            data["groupId"] = group_id
        if product_id is not None:
            data["productId"] = product_id
        if search:
            data["search"] = search
        print(f"[cumulative] Payload: {json.dumps(data, ensure_ascii=False)}")
        response = self.request_sync(
            TOPIC_CUMULATIVE_DATA_LIST, data, timeout=timeout
        )
        return self._require_success(response, TOPIC_CUMULATIVE_DATA_LIST)

    def get_cumulative_totals(
        self,
        *,
        param_name: str = "DI+",
        category: int = 2,
        page_size: int = 20,
        page_row: int = 1,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        group_id: Optional[int] = None,
        product_id: Optional[int] = None,
        search: str = "",
        timeout: float = 30.0,
        **extra,
    ) -> dict:
        """
        Fetch cumulative totals across ALL devices (wm/device/dashboard/data/list).

        Unlike get_cumulative_data_list(), this topic does NOT require a deviceId —
        it returns aggregate readings for every device the authenticated user can see.

        Parameters match get_cumulative_data_list() except device_id is absent.
        """
        data: dict = {
            "paramName": param_name,
            "category":  category,
            "pageSize":  page_size,
            "pageRow":   page_row,
            **extra,
        }
        if start_time is not None:
            data["startTime"] = start_time
        if end_time is not None:
            data["endTime"] = end_time
        if group_id is not None:
            data["groupId"] = group_id
        if product_id is not None:
            data["productId"] = product_id
        if search:
            data["search"] = search
        print(f"[cumulative-totals] Payload: {json.dumps(data, ensure_ascii=False)}")
        response = self.request_sync(
            TOPIC_DASHBOARD_DEVICE_LIST, data, timeout=timeout
        )
        return self._require_success(response, TOPIC_DASHBOARD_DEVICE_LIST)

    def poll_realtime_device_list(
        self,
        *,
        interval: float = DEFAULT_REALTIME_POLL_INTERVAL,
        group_id: Optional[int] = None,
        product_id: Optional[int] = None,
        page_size: int = 20,
        page_row: int = 1,
        search: str = "",
        on_update: Optional[Callable[[dict], None]] = None,
        timeout: float = 30.0,
    ) -> None:
        """
        Poll wm/realtime/device/list every ``interval`` seconds (default 5, like app.js).
        Runs until KeyboardInterrupt.
        """
        print(
            f"[poll] Fetching {TOPIC_REALTIME_DEVICE_LIST} every {interval}s "
            f"(Ctrl+C to stop)"
        )
        while True:
            try:
                data = self.get_realtime_device_list(
                    group_id=group_id,
                    product_id=product_id,
                    page_size=page_size,
                    page_row=page_row,
                    search=search,
                    timeout=timeout,
                )
                if on_update:
                    on_update(data)
                else:
                    print_realtime_device_list(data)
            except (TimeoutError, RuntimeError) as exc:
                print(f"[poll] Error: {exc}")
            time.sleep(interval)

    # ------------------------------------------------------------------
    # Push / broadcast subscriptions (non-request/response topics)
    # ------------------------------------------------------------------

    def add_callback(self, topic: str, callback: Callable[[dict], None]) -> None:
        """
        Subscribe to any topic and call callback(payload) on each message.
        Mirrors mqttModule.addCallback() in app.js.
        """
        def _on_sub(client, userdata, mid, granted_qos):
            self._push_callbacks[topic] = callback
            print(f"[mqtt] Subscribed to push topic: {topic}")

        self._client.subscribe(topic)
        self._push_callbacks[topic] = callback

    def remove_callback(self, topic: str) -> None:
        """Mirrors mqttModule.removeCallback() in app.js."""
        if topic in self._push_callbacks:
            del self._push_callbacks[topic]
            self._client.unsubscribe(topic)
            print(f"[mqtt] Unsubscribed from: {topic}")

    # ------------------------------------------------------------------
    # Internal paho callbacks
    # ------------------------------------------------------------------

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            print("[mqtt] Connected successfully.")
            # Subscribe to our private response topic
            client.subscribe(self.response_topic)
            print(f"[mqtt] Listening on response topic: {self.response_topic}")
            self._connected.set()
        else:
            codes = {
                1: "incorrect protocol version",
                2: "invalid client identifier",
                3: "server unavailable",
                4: "bad username or password",
                5: "not authorised",
            }
            print(f"[mqtt] Connection refused: {codes.get(rc, rc)}")

    def _on_disconnect(self, client, userdata, rc):
        self._connected.clear()
        if rc != 0:
            print(f"[mqtt] Unexpected disconnect (rc={rc}), will auto-reconnect…")

    def _on_message(self, client, userdata, msg):
        topic = msg.topic
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            print(f"[mqtt] Could not parse message on {topic}: {exc}")
            return

        if self.debug:
            print(f"[debug] ← {topic}: {json.dumps(payload, ensure_ascii=False)}")

        if topic.startswith("wm"):
            # Check for token expiry
            if payload.get("code") == self.CODE_TOKEN_EXPIRE:
                print("[mqtt] Token expired — please re-login and update self.token")
                request_id = payload.get("requestId")
                if request_id:
                    with self._lock:
                        self._pending.pop(request_id, None)
                return

            # Route to the matching pending callback via requestId
            if topic == self.response_topic:
                request_id = payload.get("requestId")
                with self._lock:
                    cb = self._pending.pop(request_id, None)
                if cb:
                    cb(payload)
                else:
                    print(f"[mqtt] No pending callback for requestId={request_id}")
        else:
            # Push/broadcast message on a non-wm topic
            cb = self._push_callbacks.get(topic)
            if cb:
                cb(payload)


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def _param_value(params: list, code: str) -> str:
    """Read a param from a device's params list (matches getParam() in chunk.js)."""
    code_upper = code.upper()
    for p in params:
        if str(p.get("paramName", "")).upper() == code_upper:
            value = p.get("paramValue", "")
            unit = p.get("paramUnit", "")
            return f"{value} {unit}".strip()
    return ""


def print_user_info(data: dict) -> None:
    """Print a human-readable summary of wm/user/info data."""
    print("\n[user/info]")
    for key in ("userId", "userName", "roleId", "isAdmin", "isSubUser", "description"):
        if key in data:
            print(f"  {key}: {data[key]}")
    extras = {k: v for k, v in data.items()
              if k not in ("userId", "userName", "roleId", "isAdmin", "isSubUser", "description")}
    if extras:
        print(f"  (other fields): {json.dumps(extras, ensure_ascii=False)}")


def print_user_statistics(data: dict) -> None:
    """Print a human-readable summary of wm/user/statistics data."""
    print("\n[user/statistics]")
    if not data:
        print("  (empty response)")
        return
    for key, value in data.items():
        print(f"  {key}: {value}")


def print_resource_tree(data: dict | list, indent: int = 0) -> None:
    """Recursively print the resource/permission tree (wm/resource/tree)."""
    if indent == 0:
        print("\n[resource/tree]")
    items = data if isinstance(data, list) else [data]
    prefix = "  " * (indent + 1)
    for node in items:
        name = node.get("name") or node.get("title") or node.get("resourceName") or "(unnamed)"
        path = node.get("path") or node.get("url") or ""
        icon = node.get("icon") or node.get("meta", {}).get("icon") or ""
        line = f"{prefix}{'  ' * indent}{name}"
        if path:
            line += f"  →  {path}"
        if icon:
            line += f"  [{icon}]"
        print(line)
        children = node.get("children") or node.get("routes") or []
        if children:
            print_resource_tree(children, indent + 1)


def print_cumulative_data_list(data: dict) -> None:
    """Print a human-readable summary of wm/device/cumulative/data/list data."""
    items = data.get("items") or data.get("list") or data.get("records") or []
    if isinstance(data, list):
        items = data
    total = data.get("total", len(items)) if isinstance(data, dict) else len(items)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[{ts}] Cumulative data: {len(items)} row(s), total={total}")

    # Columns known from app.js i18n (line 2267)
    CUMULATIVE_COLS = [
        ("deviceName",                "Device Name"),
        ("serialNo",                  "Serial No"),
        ("belongsTo",                 "Product"),
        ("groupName",                 "Group"),
        ("issueDate",                 "Statistics Date"),
        ("instantaneousFlow",         "Instantaneous Flow"),
        ("instantaneousVelocity",     "Instantaneous Velocity"),
        ("waterTemperature",          "Inlet Water Temp"),
        ("returnWaterTemperature",    "Outlet Water Temp"),
        ("instantaneousCooling",      "Instantaneous Cooling"),
        ("accumulatedCooling",        "Cumulative Cooling"),
        ("coolingCapacity",           "Cooling Volume"),
        ("heat",                      "Heat"),
        ("flow",                      "Flow"),
    ]
    for i, row in enumerate(items, 1):
        name = row.get("deviceName") or row.get("name") or f"row {i}"
        print(f"  [{i}] {name}")
        for field, label in CUMULATIVE_COLS:
            # try camelCase and lower variants
            val = row.get(field) or row.get(field.lower()) or row.get(field[0].upper() + field[1:])
            if val is not None and val != "":
                print(f"       {label}: {val}")


def print_dashboard_device_list(data: dict) -> None:
    """Print a human-readable summary of wm/device/dashboard/data/list data."""
    items = data.get("items") or data.get("list") or data.get("records") or []
    if isinstance(data, list):
        items = data
    total = data.get("total", len(items)) if isinstance(data, dict) else len(items)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[{ts}] Dashboard devices: {len(items)} row(s), total={total}")
    for i, device in enumerate(items, 1):
        name   = device.get("deviceName") or device.get("name") or "(unnamed)"
        serial = device.get("serialNo") or device.get("serial") or ""
        status = device.get("status") or device.get("onlineStatus") or ""
        line   = f"  [{i}] {name}"
        if serial:
            line += f"  serial={serial}"
        if status:
            line += f"  status={status}"
        print(line)


def print_realtime_device_list(data: dict) -> None:
    """Print a human-readable summary of wm/realtime/device/list data."""
    items = data.get("items") or []
    total = data.get("total", len(items))
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[{ts}] Realtime devices: {len(items)} row(s), total={total}")

    if not items:
        return

    codes = list(REALTIME_PARAM_LABELS.keys())
    for i, device in enumerate(items, 1):
        name = device.get("deviceName") or device.get("name") or "(unnamed)"
        serial = device.get("serialNo") or device.get("serial") or ""
        product = device.get("productName") or device.get("belongsTo") or ""
        header = f"  [{i}] {name}"
        if serial:
            header += f"  serial={serial}"
        if product:
            header += f"  product={product}"
        print(header)

        params = device.get("params") or device.get("paramList") or []
        if not params and isinstance(device.get("param"), list):
            params = device["param"]

        if params:
            for code in codes:
                text = _param_value(params, code)
                if text:
                    label = REALTIME_PARAM_LABELS.get(code, code)
                    print(f"       {label} ({code}): {text}")
        else:
            # Fallback: dump scalar fields if the API shape differs
            skip = {"params", "paramList", "param"}
            extras = {k: v for k, v in device.items() if k not in skip and v not in (None, "")}
            if extras:
                print(f"       {json.dumps(extras, ensure_ascii=False)}")


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="FlowMeters MQTT client")
    p.add_argument("--broker",   default="", help="Broker URL, e.g. ws://host:8083/mqtt")
    p.add_argument("--username", default="", help="App username (for MQTT login)")
    p.add_argument("--password", default="", help="App password (for MQTT login)")
    p.add_argument("--token",    default="", help="Existing auth token (skips login)")
    p.add_argument("--topic",    default="wm/resource/tree", help="First topic to query")
    p.add_argument("--data",     default="{}",
                   help='JSON data payload, e.g. \'{"category": 0}\'')
    p.add_argument("--debug",    action="store_true",
                   help="Print every raw incoming MQTT message")

    info = p.add_argument_group("dashboard / user queries (single-shot)")
    info.add_argument(
        "--user-info",
        action="store_true",
        help="Fetch current user profile (wm/user/info)",
    )
    info.add_argument(
        "--user-statistics",
        action="store_true",
        help="Fetch user/account statistics shown on the Dashboard (wm/user/statistics)",
    )
    info.add_argument(
        "--resource-tree",
        action="store_true",
        help="Fetch the permission/menu resource tree (wm/resource/tree)",
    )
    info.add_argument(
        "--resource-tree-all",
        action="store_true",
        help="Fetch the full resource tree (category=1, admin only)",
    )
    info.add_argument(
        "--dashboard-devices",
        action="store_true",
        help="Fetch dashboard device list (wm/device/dashboard/data/list)",
    )
    info.add_argument(
        "--cumulative-data",
        action="store_true",
        help=(
            "Fetch accumulated/historical meter readings for a single device "
            "(wm/device/cumulative/data/list). Requires --device-id."
        ),
    )
    info.add_argument(
        "--cumulative-totals",
        action="store_true",
        help=(
            "Fetch cumulative totals across ALL devices "
            "(wm/device/dashboard/data/list). No --device-id needed."
        ),
    )
    info.add_argument(
        "--start-time",
        default=None,
        help=(
            "Statistics date range start, e.g. '2026-01-01 00:00:00' "
            "(used with --cumulative-data and --cumulative-totals)"
        ),
    )
    info.add_argument(
        "--end-time",
        default=None,
        help=(
            "Statistics date range end, e.g. '2026-05-26 23:59:59' "
            "(used with --cumulative-data and --cumulative-totals)"
        ),
    )
    info.add_argument(
        "--device-id", type=int, default=None,
        help="Device ID — required for --cumulative-data (per-device readings)",
    )
    info.add_argument(
        "--param-name",
        default="DI+",
        help=(
            "Parameter code for --cumulative-data (default: DI+). "
            "Common values: DI+ (positive accum.), DI- (negative accum.), "
            "DIN (net accum.), DQ (instantaneous flow), DV (velocity), "
            "TI (inlet temp), TO (outlet temp), RH (instant. cooling), "
            "TH (accum. cooling), EQH (instant. heat), THEAT (cumul. heat)"
        ),
    )
    info.add_argument(
        "--category",
        type=int,
        default=2,
        help="Category value for --cumulative-data (default: 2)",
    )

    rt = p.add_argument_group("realtime device data (wm/realtime/device/list)")
    rt.add_argument(
        "--poll-realtime",
        action="store_true",
        help="Poll realtime device/sensor list every --interval seconds (like the web UI)",
    )
    rt.add_argument(
        "--realtime-once",
        action="store_true",
        help="Fetch realtime device list once and print it",
    )
    rt.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_REALTIME_POLL_INTERVAL,
        help=f"Poll interval in seconds (default: {DEFAULT_REALTIME_POLL_INTERVAL})",
    )
    rt.add_argument("--group-id", type=int, default=None, help="Filter by groupId")
    rt.add_argument("--product-id", type=int, default=None, help="Filter by productId")
    rt.add_argument("--page-size", type=int, default=20, help="pageSize (default: 20)")
    rt.add_argument("--page-row", type=int, default=1, help="pageRow / page number (default: 1)")
    rt.add_argument("--search", default="", help="Search string")
    return p.parse_args()


def prompt(label: str, secret: bool = False) -> str:
    import getpass
    return getpass.getpass(f"{label}: ") if secret else input(f"{label}: ")


def main():
    args = parse_args()

    # ---- Broker URL ----
    broker = args.broker or prompt("Broker URL (e.g. ws://host:8083/mqtt)")

    # ---- Parse data payload ----
    try:
        data = json.loads(args.data)
    except json.JSONDecodeError as exc:
        print(f"[error] --data is not valid JSON: {exc}")
        sys.exit(1)

    # ---- Connect (token can be empty at this point — login happens over MQTT) ----
    token = args.token
    client = FlowMetersMQTT(broker_url=broker, token=token, debug=args.debug)
    client.connect(timeout=10)

    # ---- Authenticate via MQTT if no token was provided ----
    if not token:
        username = args.username or prompt("App username")
        password = args.password or prompt("App password", secret=True)
        client.mqtt_login(username, password)

    rt_filters = dict(
        group_id=args.group_id,
        product_id=args.product_id,
        page_size=args.page_size,
        page_row=args.page_row,
        search=args.search,
    )

    # ---- Validate mutually exclusive / required argument combos ----
    if args.cumulative_data and args.device_id is None:
        print(
            "[error] --cumulative-data requires --device-id.\n"
            "        To fetch totals across ALL devices use --cumulative-totals instead."
        )
        client.disconnect()
        sys.exit(1)

    # ---- Single-shot dashboard / user queries ----
    single_shot_flags = (
        args.user_info,
        args.user_statistics,
        args.resource_tree,
        args.resource_tree_all,
        args.dashboard_devices,
        args.cumulative_data,
        args.cumulative_totals,
    )
    if any(single_shot_flags):
        try:
            if args.user_info:
                data = client.get_user_info()
                print_user_info(data)
                print("\n[raw]")
                print(json.dumps(data, indent=2, ensure_ascii=False))

            if args.user_statistics:
                data = client.get_user_statistics()
                print_user_statistics(data)
                print("\n[raw]")
                print(json.dumps(data, indent=2, ensure_ascii=False))

            if args.resource_tree:
                data = client.get_resource_tree(category=CATEGORY_USER_RESOURCE)
                print_resource_tree(data)
                print("\n[raw]")
                print(json.dumps(data, indent=2, ensure_ascii=False))

            if args.resource_tree_all:
                data = client.get_resource_tree(category=CATEGORY_ALL_RESOURCE)
                print_resource_tree(data)
                print("\n[raw]")
                print(json.dumps(data, indent=2, ensure_ascii=False))

            if args.dashboard_devices:
                data = client.get_dashboard_device_list(
                    page_size=args.page_size,
                    page_row=args.page_row,
                )
                print_dashboard_device_list(data)
                print("\n[raw]")
                print(json.dumps(data, indent=2, ensure_ascii=False))

            if args.cumulative_data:
                # device_id is guaranteed non-None here (validated above)
                data = client.get_cumulative_data_list(
                    device_id=args.device_id,
                    param_name=args.param_name,
                    category=args.category,
                    page_size=args.page_size,
                    page_row=args.page_row,
                    start_time=args.start_time,
                    end_time=args.end_time,
                    group_id=args.group_id,
                    product_id=args.product_id,
                    search=args.search,
                )
                print_cumulative_data_list(data)
                print("\n[raw]")
                print(json.dumps(data, indent=2, ensure_ascii=False))

            if args.cumulative_totals:
                data = client.get_cumulative_totals(
                    param_name=args.param_name,
                    category=args.category,
                    page_size=args.page_size,
                    page_row=args.page_row,
                    start_time=args.start_time,
                    end_time=args.end_time,
                    group_id=args.group_id,
                    product_id=args.product_id,
                    search=args.search,
                )
                print_cumulative_data_list(data)
                print("\n[raw]")
                print(json.dumps(data, indent=2, ensure_ascii=False))

        except (TimeoutError, RuntimeError) as exc:
            print(f"[error] {exc}")
            sys.exit(1)
        finally:
            client.request_sync("wm/user/logout", {}, timeout=10)
            client.disconnect()
        return

    # ---- Realtime device / sensor data (request/response, not push subscribe) ----
    if args.poll_realtime:
        try:
            client.poll_realtime_device_list(
                interval=args.interval, **rt_filters
            )
        except KeyboardInterrupt:
            print("\n[exit] Stopped polling.")
        finally:
            client.request_sync("wm/user/logout", {}, timeout=10)
            client.disconnect()
        return

    if args.realtime_once:
        try:
            data = client.get_realtime_device_list(**rt_filters)
            print_realtime_device_list(data)
            print("\n[raw] Full data object:")
            print(json.dumps(data, indent=2, ensure_ascii=False))
        except (TimeoutError, RuntimeError) as exc:
            print(f"[error] {exc}")
            sys.exit(1)
        client.request_sync("wm/user/logout", {}, timeout=10)
        client.disconnect()
        return

    # ---- Send the first request and print the response ----
    topic = args.topic
    print(f"\n[request] Sending to topic '{topic}' with data: {data}\n")

    response = client.request_sync(topic, data, timeout=30)

    if response is None:
        print("[result] No response received within 30 seconds.")
    else:
        print("[result] Response received:")
        print(json.dumps(response, indent=2, ensure_ascii=False))

    # ---- Interactive mode: keep sending more requests ----
    print("\n--- Interactive mode (Ctrl+C to quit) ---")
    print("Known topics:")
    print(f"  {TOPIC_USER_INFO}  (data: {{}})")
    print(f"  {TOPIC_USER_STATISTICS}  (data: {{}})")
    print(f"  {TOPIC_RESOURCE_TREE}  (data: {{\"category\": 0}})")
    print(f"  {TOPIC_DASHBOARD_DEVICE_LIST}  (data: {{\"pageSize\": 20, \"pageRow\": 1}})")
    print(f"  {TOPIC_CUMULATIVE_DATA_LIST}  (data: {{\"pageSize\": 20, \"pageRow\": 1, \"startTime\": \"...\", \"endTime\": \"...\"}})")
    print(f"  {TOPIC_REALTIME_DEVICE_LIST}  (data: {{\"pageSize\": 20, \"pageRow\": 1}})")
    print("  wm/user/logout  (data: {})")
    print("Tip: use --user-info / --user-statistics / --resource-tree / --dashboard-devices")
    print("     or --poll-realtime for continuous sensor data.")
    try:
        while True:
            raw_topic = input("\nTopic (or Enter to reuse last): ").strip()
            if raw_topic:
                topic = raw_topic
            raw_data = input("Data JSON (or Enter for {}): ").strip()
            data = json.loads(raw_data) if raw_data else {}

            response = client.request_sync(topic, data, timeout=30)
            if response is None:
                print("[result] Timed out.")
            elif topic == TOPIC_REALTIME_DEVICE_LIST and response.get("code") == 0:
                print_realtime_device_list(response.get("data") or {})
                print("\n[raw]")
                print(json.dumps(response, indent=2, ensure_ascii=False))
            else:
                print(json.dumps(response, indent=2, ensure_ascii=False))
    except KeyboardInterrupt:
        print("\n[exit] Logging out...")
        client.request_sync("wm/user/logout", {}, timeout=10)
        client.disconnect()


if __name__ == "__main__":
    main()
