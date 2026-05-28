"""
GET /api/dashboard — hourly DI+ totals for all devices + device list for dropdown.

points: today's hourly positive accumulation from wm/device/dashboard/data/list
devices: per-device entries from wm/realtime/device/list
"""

import logging
from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..auth import get_current_user
from ..scheduler import fetch_dashboard_data

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])
logger = logging.getLogger(__name__)


@router.get("")
def get_dashboard(
    paramName: str = Query(default="DI+", description="Dashboard quantity paramName, e.g. DI+, THeat, TH"),
    category: int = Query(default=1, description="Time window category: 4=Past Year, 3=Past Month, 1=Today, 2=Yesterday"),
    _current_user: dict = Depends(get_current_user),
):
    if category not in (1, 2, 3, 4):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid category. Expected 1 (Today), 2 (Yesterday), 3 (Past Month), 4 (Past Year).",
        )
    try:
        return fetch_dashboard_data(_current_user, param_name=paramName, category=category)
    except TimeoutError as exc:
        logger.warning("Dashboard request timeout: paramName=%s category=%s error=%s", paramName, category, str(exc))
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=str(exc))
    except Exception as exc:
        logger.exception("Dashboard request failed: paramName=%s category=%s", paramName, category)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
