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


# ── DA-08: authorization, not merely authentication ──────────────────────────
#
# Every model-mutation route on ml-prediction guarded itself with get_current_username, which
# establishes only that SOMEBODY signed in. The gateway adds a role check for the "/admin"
# prefix alone, so "/ml" never got one. The result: any ordinary authenticated account could
# trigger global retraining, tuning, or `resweep_suppression?dry_run=false` — actions that
# mutate shared model state for every user of the platform.
#
# The scheduler must keep working, and it is not a user: it holds a 365-day service token with
# sub="scheduler" and no role. Granting access by TRUSTING THAT `sub` STRING would be the wrong
# fix — a user account named "scheduler" would then inherit it. Service tokens instead carry an
# explicit `svc` capability claim, which only a holder of the signing secret can mint.
#
# Deliberately NOT a general capability framework. The audit is right that one belongs here
# eventually; this closes the open door without inventing a permissions model in passing.

_SERVICE_CLAIM = "svc"


def _decode_or_401(authorization: str | None) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Not authenticated")
    token = authorization.removeprefix("Bearer ")
    try:
        payload = _jwt.decode(token, _settings.jwt_secret, algorithms=[_ALGORITHM])
    except JWTError:
        raise HTTPException(401, "Invalid or expired token")
    if not payload.get("sub"):
        raise HTTPException(401, "Invalid token")
    jti = payload.get("jti", "")
    if not jti:
        raise HTTPException(401, "Token missing jti claim")
    if _check_blacklist(jti):
        raise HTTPException(401, "Token has been revoked")
    return payload


def require_model_admin(authorization: str | None = Header(default=None)) -> str:
    """Allow an admin user or an internal service principal; reject every ordinary account.

    403, not 404: the route's existence is not the secret, and a caller who IS an admin needs to
    be able to tell "you may not do this" from "this endpoint moved".
    """
    payload = _decode_or_401(authorization)
    if payload.get("role") == "admin":
        return str(payload.get("sub"))
    if payload.get(_SERVICE_CLAIM):
        return str(payload.get("sub"))
    raise HTTPException(403, "Admin or service credentials required for model mutation")
