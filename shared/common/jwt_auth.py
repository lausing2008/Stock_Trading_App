"""Shared JWT verification helper — usable by any service that has python-jose."""
from fastapi import Header, HTTPException

from common.config import get_settings

_settings = get_settings()
_ALGORITHM = "HS256"


def get_current_username(authorization: str | None = Header(default=None)) -> str:
    """FastAPI dependency — extracts and verifies JWT, returns username."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Not authenticated")
    token = authorization.removeprefix("Bearer ")
    try:
        from jose import JWTError, jwt
        payload = jwt.decode(token, _settings.jwt_secret, algorithms=[_ALGORITHM])
        username: str = payload.get("sub", "")
        if not username:
            raise HTTPException(401, "Invalid token")
        return username
    except Exception:
        raise HTTPException(401, "Invalid or expired token")
