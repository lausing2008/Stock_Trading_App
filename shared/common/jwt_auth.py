"""Shared JWT verification helper — usable by any service that has python-jose."""
from fastapi import Header, HTTPException

from common.config import get_settings

_settings = get_settings()
_ALGORITHM = "HS256"
_BLACKLIST_PREFIX = "auth:blacklist:"


def _check_blacklist(jti: str) -> bool:
    """Return True if the token JTI has been revoked. Fails open if Redis unavailable."""
    if not jti:
        return False
    try:
        import redis as redis_lib
        r = redis_lib.from_url(_settings.redis_url, decode_responses=True, socket_connect_timeout=1)
        return bool(r.exists(f"{_BLACKLIST_PREFIX}{jti}"))
    except Exception:
        return False


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
        jti: str = payload.get("jti", "")
        if _check_blacklist(jti):
            raise HTTPException(401, "Token has been revoked")
        return username
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(401, "Invalid or expired token")
