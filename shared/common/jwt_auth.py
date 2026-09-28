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
        if user_tokens_revoked(payload):
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

# ── R07 (2026-09-24 follow-up audit): account state, not just a valid signature ───────────
#
# DA-08 closed the ordinary-user escalation. It left the account-state half open: a token is
# accepted on its claims alone, so DISABLING or DELETING an administrator does not stop that
# administrator's already-issued token from retraining every model on the platform. Nothing
# read the live row, and `is_active` is the only thing `toggle_user` changes.
#
# Two layers, because one cannot do both jobs:
#
#   REVOCATION (below, cheap, every route). Disabling, deleting, or changing the password on an
#   account stamps a per-user "revoked before T" marker; a token issued before T is rejected.
#   This is the layer that makes disablement take effect before JWT expiry PLATFORM-WIDE, and
#   it costs one Redis read — the same order as the JTI blacklist check already on this path.
#   It needs `iat` on the token, which _make_token now sets.
#
#   LIVE ACCOUNT STATE (require_model_admin, authoritative, privileged routes only). Reads the
#   User row and re-checks existence, is_active and role on every model mutation. This is the
#   layer that catches ROLE DEMOTION, which revocation does not model, and it does not depend
#   on Redis being reachable. It is a per-request DB read, which is why it is scoped to routes
#   that are rare and admin-triggered rather than applied to every authenticated request.
#
# NOT a general capability framework — the audit is right that one belongs here; this closes
# the open door without inventing a permissions model in passing.

_SERVICE_CLAIM = "svc"

_USER_REVOKE_PREFIX = "auth:user_revoked_at:"
# Must outlive the longest USER token, or an expired marker silently re-validates the tokens it
# was set to kill. Service tokens run 365 days but have no user row and are never marked.
_USER_REVOKE_TTL = 400 * 86400


def revoke_user_tokens(username: str) -> bool:
    """Invalidate every token issued for `username` up to now. Returns whether it was recorded.

    The single source of truth for the marker's key and format — auth.py calls this rather than
    writing the key itself, so the two halves cannot drift into disagreeing about the shape of
    a security check.
    """
    if not username:
        return False
    try:
        get_redis().setex(
            f"{_USER_REVOKE_PREFIX}{username.lower()}", _USER_REVOKE_TTL, str(int(_time.time())))
        return True
    except Exception:
        return False


def user_tokens_revoked(payload: dict) -> bool:
    """True if this token predates a revocation of its account.

    AN UNREADABLE MARKER ALLOWS, matching the deliberate fail-open the JTI blacklist above
    already takes for unknown JTIs: a Redis blip must not log the whole platform out, and on
    privileged routes it must not block the nightly scheduler either. This is safe to state
    plainly because it is not the layer doing the heavy lifting — require_model_admin re-reads
    the User row from the DATABASE, which needs no Redis and which catches every disabled,
    deleted or demoted account on exactly the routes where being wrong is expensive. What this
    fail-open leaves exposed is narrow and already-accepted: a still-active, still-admin account
    whose password was reset by someone else, during a Redis outage.
    """
    sub = str(payload.get("sub") or "").lower()
    if not sub or payload.get(_SERVICE_CLAIM):
        return False          # service principals have no user row to revoke
    try:
        marker = get_redis().get(f"{_USER_REVOKE_PREFIX}{sub}")
    except Exception:
        return False
    if not marker:
        return False
    try:
        revoked_at = int(marker)
    except (TypeError, ValueError):
        return True           # a corrupt marker is not a reason to honour the token
    iat = payload.get("iat")
    if iat is None:
        # Issued before R07 added `iat`. A marker EXISTS for this account, so the token cannot
        # be shown to postdate the revocation — deny. These drain within jwt_expire_days, and
        # only accounts someone actually disabled are affected.
        return True
    try:
        # CORRECTED 2026-09-28 (pre-deployment audit): `<` let a token minted in the SAME SECOND
        # as the revocation through. Both values are integer seconds, so "issued at the instant
        # of revocation" is indistinguishable from "issued just before it" — and the whole point
        # of a revocation is that everything up to that moment stops working. `<=` costs a user
        # who logs in within the same second of their own password reset one extra login, which
        # is the correct direction to be wrong in.
        return int(iat) <= revoked_at
    except (TypeError, ValueError):
        return True


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
    if user_tokens_revoked(payload):
        raise HTTPException(401, "Token has been revoked")
    return payload


_MODEL_SCOPE = "model"


def _service_has_scope(claim, scope: str) -> bool:
    """Whether a service principal's `svc` claim covers `scope`.

    R07: the audit asks that a service credential be restricted to its actual job rather than
    every service claim authorizing every model mutation. `svc` may now be a scope list, and
    the minters name what they need. `svc: True` — the DA-08 shape, still held by any token
    minted before this — keeps meaning "all scopes", because rejecting it would break the
    scheduler the moment this deploys, which is a worse outcome than the narrowing it delays.
    Those tokens are re-minted on the next container start.
    """
    if claim is True:
        return True
    if isinstance(claim, str):
        return claim == scope
    if isinstance(claim, (list, tuple, set)):
        return scope in claim
    return False


def require_model_admin(authorization: str | None = Header(default=None)) -> str:
    """Allow a CURRENTLY-ACTIVE admin user or a scoped internal service principal.

    403, not 404: the route's existence is not the secret, and a caller who IS an admin needs to
    be able to tell "you may not do this" from "this endpoint moved".

    R07: the role is re-read from the database, not taken from the token. A token's `role`
    claim is a statement about who the holder was when they signed in; these routes retrain and
    re-suppress models for every user of the platform, so they need who the holder is NOW.
    Disablement, deletion and demotion all take effect immediately here, without waiting for
    the token to expire.
    """
    payload = _decode_or_401(authorization)
    sub = str(payload.get("sub") or "")

    svc = payload.get(_SERVICE_CLAIM)
    if svc:
        if _service_has_scope(svc, _MODEL_SCOPE):
            return sub
        raise HTTPException(403, "Service credential is not scoped for model mutation")

    if payload.get("role") != "admin":
        raise HTTPException(403, "Admin or service credentials required for model mutation")

    _require_live_admin(sub)
    return sub


def _require_live_admin(username: str) -> None:
    """Re-check the account against the database. Raises unless it is an active admin TODAY.

    FAILS CLOSED on every uncertainty — a missing row, a disabled row, a demoted row, and an
    unreachable database all deny. A model mutation is rare and retryable; running one on the
    authority of a token whose account may no longer exist is not.
    """
    try:
        # Imported lazily: this module is imported by services at startup, and a database import
        # at module scope would make an unrelated import-order problem look like an auth outage.
        from db import SessionLocal, User, UserRole
    except Exception:
        raise HTTPException(503, "Cannot verify account state for model mutation")

    try:
        with SessionLocal() as session:
            row = session.query(User).filter(User.username == username.lower()).one_or_none()
    except Exception:
        raise HTTPException(503, "Cannot verify account state for model mutation")

    if row is None:
        raise HTTPException(403, "Account no longer exists")
    if not getattr(row, "is_active", False):
        raise HTTPException(403, "Account is disabled")
    if getattr(row, "role", None) != UserRole.ADMIN:
        raise HTTPException(403, "Admin or service credentials required for model mutation")
