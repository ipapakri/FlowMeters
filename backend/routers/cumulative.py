"""
GET /api/cumulative/{device_id} — fetches historical cumulative data for one device.

Query params:
  paramName  (str,  default "DI+")
  category   (int,  0=Daily, 1=Monthly, 2=Yearly)

Each request triggers a single wm/device/cumulative/data/list fetch for that device.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..auth import get_current_user
from ..scheduler import fetch_cumulative_for_device

router = APIRouter(prefix="/api/cumulative", tags=["cumulative"])


@router.get("/{device_id}")
def get_cumulative(
    device_id: int,
    param_name: str = Query("DI+", alias="paramName"),
    category: int = Query(
        default=0,
        description="0=Daily, 1=Monthly, 2=Yearly",
    ),
    _current_user: dict = Depends(get_current_user),
):
    if category not in (0, 1, 2):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid category. Expected 0 (Daily), 1 (Monthly), 2 (Yearly).",
        )

    try:
        return fetch_cumulative_for_device(
            current_user=_current_user,
            device_id=device_id,
            param_name=param_name,
            category=category,
        )
    except TimeoutError as exc:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
