"""
GET /api/realtime — snapshot of wm/realtime/device/list for live values.

This is a single fetch per request (frontend can poll it).
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..auth import get_current_user
from ..scheduler import fetch_realtime_snapshot

router = APIRouter(prefix="/api/realtime", tags=["realtime"])


@router.get("")
def get_realtime(
    deviceId: int | None = Query(default=None, description="Optional device id to filter the realtime snapshot"),
    _current_user: dict = Depends(get_current_user),
):
    try:
        return fetch_realtime_snapshot(_current_user, device_id=deviceId)
    except TimeoutError as exc:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

