"""
POST /api/auth/login  — authenticates via MQTT, issues a JWT cookie.
POST /api/auth/logout — clears the cookie.
GET  /api/auth/me     — returns the current user info.
"""

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel

from ..auth import COOKIE_NAME, JWT_EXPIRE_HOURS, create_access_token, get_current_user
from ..scheduler import get_mqtt_client

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login")
def login(body: LoginRequest, response: Response):
    client = get_mqtt_client()
    try:
        mqtt_token = client.mqtt_login(body.username, body.password)
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))
    except TimeoutError as exc:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))

    jwt_token = create_access_token(username=body.username, mqtt_token=mqtt_token)
    response.set_cookie(
        key=COOKIE_NAME,
        value=jwt_token,
        httponly=True,
        samesite="lax",
        max_age=JWT_EXPIRE_HOURS * 3600,
    )
    return {"username": body.username}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE_NAME)
    return {"detail": "Logged out."}


@router.get("/me")
def me(current_user: dict = Depends(get_current_user)):
    return {"username": current_user.get("sub")}
