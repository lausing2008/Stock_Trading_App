"""Shared JWT verification helper — usable by any service that has python-jose."""
import time as _time

from fastapi import Header, HTTPException
# Module-level import: missing python-jose causes clear ImportError at startup
# instead of a silent HTTP 401 on every authenticated request (recurring BUG).
from jose import JWTError, jwt as _jwt

from common.config import get_settings
from common.redis_client import get_redis

_settings = get_settings()
_ALGORITHM = "HS256"
_BLACKLIST_PREFIX = "auth:blacklist:"

_BLACKLIST_MEM: dict[str, float] = {}   # jti → expiry unix timestamp
_BLACKLIST_MEM_TTL = 3600               # 1 hour


def _check_blacklist(jti: str) -> bool:
    """Return True if the token JTI has been revoked. Fail-closed for known-revoked JTIs even when Redis is down."""
    if not jti:
        return False
    now = _time.time()
    exp = _BLACKLIST_MEM.get(jti)
    if exp is not None and exp > now:
        return True
    try:
        revoked = bool(get_redis().exists(f"{_BLACKLIST_PREFIX}{jti}"))
        if revoked:
            _BLACKLIST_MEM[jti] = now + _BLACKLIST_MEM_TTL
            if len(_BLACKLIST_MEM) > 2000:
                _BLACKLIST_MEM.clear()
        return revoked
    except Exception:
        # Redis unavailable — rely on in-memory cache; unknown JTIs fail-open
        return exp is not None and exp > now


def get_current_username(authorization: str | None = Header(default=None)) -> str:
    """FastAPI dependency — extracts and verifies JWT, returns username."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Not authenticated")
    token = authorization.removeprefix("Bearer ")
    try:
        payload = _jwt.decode(token, _settings.jwt_secret, algorithms=[_ALGORITHM])
        username: str = payload.get("sub", "")
        if not username:
            raise HTTPException(401, "Invalid token")
        jti: str = payload.get("jti", "")
        if not jti:
            raise HTTPException(401, "Token missing jti claim")
        if _check_blacklist(jti):
            raise HTTPException(401, "Token has been revoked")
        return username
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(401, "Invalid or expired token")
