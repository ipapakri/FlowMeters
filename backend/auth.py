"""
JWT-based session authentication for the web API.

The web login flow:
  1. Client POSTs username + password to /api/auth/login.
  2. Backend calls mqtt_login() on the shared MQTT client.
  3. On success the MQTT token is embedded in a signed JWT stored as an httpOnly cookie.
  4. Subsequent API calls validate the cookie and extract the MQTT token for broker calls.
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Cookie, HTTPException, status
from jose import JWTError, jwt

JWT_SECRET = os.getenv("JWT_SECRET", "change-me-in-production")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = int(os.getenv("JWT_EXPIRE_HOURS", "8"))
COOKIE_NAME = "fm_session"


def create_access_token(username: str, mqtt_token: str) -> str:
    expire = datetime.now(tz=timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS)
    payload = {
        "sub": username,
        "mqtt_token": mqtt_token,
        "exp": expire,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    """Returns the decoded payload or raises HTTPException 401."""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired or invalid. Please log in again.",
        )


def get_current_user(fm_session: Optional[str] = Cookie(default=None)) -> dict:
    """
    FastAPI dependency.
    Reads the httpOnly cookie, validates the JWT, and returns the payload.
    Raises 401 if the cookie is missing or the token is invalid.
    """
    if not fm_session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated.",
        )
    return decode_access_token(fm_session)
