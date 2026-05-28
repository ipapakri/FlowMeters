"""
GET /api/devices — returns devices from wm/realtime/device/list.
"""

from fastapi import APIRouter, Depends, HTTPException, status

from ..auth import get_current_user
from ..scheduler import fetch_devices_list

router = APIRouter(prefix="/api/devices", tags=["devices"])


@router.get("")
def list_devices(_current_user: dict = Depends(get_current_user)):
    try:
        return fetch_devices_list(_current_user)
    except TimeoutError as exc:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
