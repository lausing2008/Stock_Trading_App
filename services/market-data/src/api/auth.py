"""Auth routes — JWT login, user management (admin), password change."""
from datetime import datetime, timedelta, timezone
import time as _time
import uuid

import bcrypt as _bcrypt
import redis as redis_lib
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from common.config import get_settings
from common.logging import get_logger
from db import SessionLocal, SignalAlert, User, UserRole, get_session

log = get_logger("auth")

router = APIRouter(prefix="/auth", tags=["auth"])

_settings = get_settings()
_security = HTTPBearer(auto_error=False)

_BLACKLIST_PREFIX = "auth:blacklist:"
_BLACKLIST_MEM: dict[str, float] = {}   # jti → expiry unix timestamp
_BLACKLIST_MEM_TTL = 3600               # 1 hour in-memory retention


def _get_redis() -> redis_lib.Redis:
    from common.redis_client import get_redis as _pool_redis
    return _pool_redis()


def _prune_blacklist_mem() -> None:
    """Remove expired entries from the in-memory blacklist — never evicts active revocations."""
    now = _time.time()
    expired = [k for k, exp in _BLACKLIST_MEM.items() if exp <= now]
    for k in expired:
        del _BLACKLIST_MEM[k]


def _blacklist_jti(jti: str, exp: int) -> None:
    """Store a token JTI in the Redis blacklist until it expires."""
    _BLACKLIST_MEM[jti] = _time.time() + _BLACKLIST_MEM_TTL
    if len(_BLACKLIST_MEM) > 2000:
        _prune_blacklist_mem()  # only evicts expired entries; active revocations are preserved
    try:
        ttl = max(1, exp - int(datetime.now(timezone.utc).timestamp()))
        _get_redis().setex(f"{_BLACKLIST_PREFIX}{jti}", ttl, "1")
    except Exception:
        pass


def _is_blacklisted(jti: str) -> bool:
    now = _time.time()
    mem_exp = _BLACKLIST_MEM.get(jti)
    if mem_exp is not None and mem_exp > now:
        return True
    try:
        revoked = bool(_get_redis().exists(f"{_BLACKLIST_PREFIX}{jti}"))
        if revoked:
            _BLACKLIST_MEM[jti] = now + _BLACKLIST_MEM_TTL
            if len(_BLACKLIST_MEM) > 2000:
                _prune_blacklist_mem()
        return revoked
    except Exception:
        return mem_exp is not None and mem_exp > now


_RATE_PREFIX = "auth:login_fail:"
_RATE_LIMIT   = 10   # max failures
_RATE_WINDOW  = 300  # seconds (5 min)


def _check_rate_limit(ip: str) -> None:
    """Raise 429 if this IP has exceeded the login failure rate limit."""
    try:
        r = _get_redis()
        key = f"{_RATE_PREFIX}{ip}"
        count = int(r.get(key) or 0)
        if count >= _RATE_LIMIT:
            raise HTTPException(
                status_code=429,
                detail="Too many failed login attempts. Try again in 5 minutes.",
                headers={"Retry-After": str(_RATE_WINDOW)},
            )
    except HTTPException:
        raise
    except Exception:
        pass  # fail-open: Redis down → don't block


def _record_login_failure(ip: str) -> None:
    """Increment the failure counter for this IP; sets TTL on first failure."""
    try:
        r = _get_redis()
        key = f"{_RATE_PREFIX}{ip}"
        pipe = r.pipeline()
        pipe.incr(key)
        pipe.expire(key, _RATE_WINDOW)
        pipe.execute()
    except Exception:
        pass


def _clear_rate_limit(ip: str) -> None:
    """Remove failure counter on successful login."""
    try:
        _get_redis().delete(f"{_RATE_PREFIX}{ip}")
    except Exception:
        pass


def _hash_password(password: str) -> str:
    return _bcrypt.hashpw(password.encode(), _bcrypt.gensalt()).decode()


def _verify_password(password: str, hashed: str) -> bool:
    try:
        return _bcrypt.checkpw(password.encode(), hashed.encode())
    except Exception:
        return False

ALGORITHM = "HS256"


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_token(username: str, role: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=_settings.jwt_expire_days)
    return jwt.encode(
        {"sub": username, "role": role.lower(), "exp": expire, "jti": str(uuid.uuid4())},
        _settings.jwt_secret,
        algorithm=ALGORITHM,
    )


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_security),
    session: Session = Depends(get_session),
) -> User:
    if not creds:
        raise HTTPException(401, "Not authenticated")
    try:
        payload = jwt.decode(creds.credentials, _settings.jwt_secret, algorithms=[ALGORITHM])
        username: str = payload.get("sub", "")
        jti: str = payload.get("jti", "")
    except JWTError:
        raise HTTPException(401, "Invalid or expired token")
    if jti and _is_blacklisted(jti):
        raise HTTPException(401, "Token has been revoked")
    user = session.execute(
        select(User).where(User.username == username)
    ).scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(401, "User not found or disabled")
    return user


def get_admin_user(current: User = Depends(get_current_user)) -> User:
    if current.role != UserRole.ADMIN:
        raise HTTPException(403, "Admin access required")
    return current


# ── Schemas ──────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


class ResetPasswordRequest(BaseModel):
    username: str
    old_password: str
    new_password: str


class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str = "user"


class AdminResetRequest(BaseModel):
    new_password: str


class UserOut(BaseModel):
    id: int
    username: str
    role: str
    is_active: bool
    email: str | None = None
    created_at: str

    class Config:
        from_attributes = True


class UpdateProfileRequest(BaseModel):
    email: str | None = None


# ── Public endpoints ──────────────────────────────────────────────────────────

@router.post("/login")
def login(request: Request, body: LoginRequest, session: Session = Depends(get_session)):
    ip = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip() or (request.client.host if request.client else "unknown")
    _check_rate_limit(ip)
    user = session.execute(
        select(User).where(User.username == body.username.lower())
    ).scalar_one_or_none()
    if not user or not user.is_active or not _verify_password(body.password, user.password_hash):
        _record_login_failure(ip)
        raise HTTPException(401, "Incorrect username or password")
    _clear_rate_limit(ip)
    token = _make_token(user.username, user.role.value)
    return {"token": token, "username": user.username, "role": user.role.value.lower()}


@router.post("/logout")
def logout(creds: HTTPAuthorizationCredentials | None = Depends(_security)):
    """Revoke the caller's JWT by adding its JTI to the Redis blacklist."""
    if not creds:
        return {"status": "ok"}
    try:
        payload = jwt.decode(creds.credentials, _settings.jwt_secret, algorithms=[ALGORITHM])
        jti = payload.get("jti", "")
        exp = payload.get("exp", 0)
        if jti:
            _blacklist_jti(jti, exp)
    except JWTError:
        pass  # expired/invalid token — nothing to revoke
    return {"status": "ok"}


@router.post("/reset-password")
def reset_password_public(request: Request, body: ResetPasswordRequest, session: Session = Depends(get_session)):
    """Password reset without JWT — requires old password for verification."""
    ip = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip() or (request.client.host if request.client else "unknown")
    _check_rate_limit(ip)
    user = session.execute(
        select(User).where(User.username == body.username.lower())
    ).scalar_one_or_none()
    if not user:
        _record_login_failure(ip)
        raise HTTPException(404, "User not found")
    if not _verify_password(body.old_password, user.password_hash):
        _record_login_failure(ip)
        raise HTTPException(401, "Current password is incorrect")
    if len(body.new_password) < 8:
        raise HTTPException(400, "New password must be at least 8 characters")
    user.password_hash = _hash_password(body.new_password)
    session.commit()
    _clear_rate_limit(ip)
    return {"status": "ok"}


# ── Authenticated endpoints ───────────────────────────────────────────────────

@router.get("/me", response_model=UserOut)
def me(current: User = Depends(get_current_user)):
    return UserOut(
        id=current.id, username=current.username, role=current.role.value.lower(),
        is_active=current.is_active, email=current.email,
        created_at=current.created_at.isoformat(),
    )


@router.put("/me", response_model=UserOut)
def update_me(
    body: UpdateProfileRequest,
    current: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    user = session.get(User, current.id)
    if body.email is not None:
        old_email = user.email
        new_email = body.email.strip() or None
        user.email = new_email
        # Cascade: update any signal alert subscriptions that stored the old email
        if old_email and new_email and old_email != new_email:
            session.execute(
                update(SignalAlert)
                .where(SignalAlert.user_id == user.id, SignalAlert.email == old_email)
                .values(email=new_email)
            )
        elif old_email and not new_email:
            # Email cleared — null out stored alert emails so fallback reads user.email (now None)
            session.execute(
                update(SignalAlert)
                .where(SignalAlert.user_id == user.id, SignalAlert.email == old_email)
                .values(email=None)
            )
    session.commit()
    session.refresh(user)
    return UserOut(
        id=user.id, username=user.username, role=user.role.value.lower(),
        is_active=user.is_active, email=user.email,
        created_at=user.created_at.isoformat(),
    )


@router.put("/change-password")
def change_password(
    body: ChangePasswordRequest,
    current: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    user = session.get(User, current.id)
    if not _verify_password(body.old_password, user.password_hash):
        raise HTTPException(401, "Current password is incorrect")
    if len(body.new_password) < 8:
        raise HTTPException(400, "New password must be at least 8 characters")
    user.password_hash = _hash_password(body.new_password)
    session.commit()
    return {"status": "ok"}


# ── Admin endpoints ───────────────────────────────────────────────────────────

@router.get("/users", response_model=list[UserOut])
def list_users(admin: User = Depends(get_admin_user), session: Session = Depends(get_session)):
    rows = session.execute(select(User).order_by(User.created_at)).scalars().all()
    return [
        UserOut(
            id=u.id, username=u.username, role=u.role.value.lower(),
            is_active=u.is_active, email=u.email, created_at=u.created_at.isoformat(),
        )
        for u in rows
    ]


@router.post("/users", response_model=UserOut)
def create_user(
    body: CreateUserRequest,
    admin: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
):
    if len(body.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    existing = session.execute(
        select(User).where(User.username == body.username.lower())
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(409, f"Username '{body.username}' already exists")
    role = UserRole.ADMIN if body.role == "admin" else UserRole.USER
    user = User(
        username=body.username.lower(),
        password_hash=_hash_password(body.password),
        role=role,
        is_active=True,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return UserOut(
        id=user.id, username=user.username, role=user.role.value.lower(),
        is_active=user.is_active, email=user.email, created_at=user.created_at.isoformat(),
    )


@router.delete("/users/{username}")
def delete_user(
    username: str,
    admin: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
):
    if username.lower() == admin.username:
        raise HTTPException(400, "Cannot delete your own account")
    user = session.execute(
        select(User).where(User.username == username.lower())
    ).scalar_one_or_none()
    if not user:
        raise HTTPException(404, f"User '{username}' not found")
    session.delete(user)
    session.commit()
    return {"status": "deleted", "username": username}


@router.put("/users/{username}/reset-password")
def admin_reset_password(
    username: str,
    body: AdminResetRequest,
    admin: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
):
    if len(body.new_password) < 8:
        raise HTTPException(400, "New password must be at least 8 characters")
    user = session.execute(
        select(User).where(User.username == username.lower())
    ).scalar_one_or_none()
    if not user:
        raise HTTPException(404, f"User '{username}' not found")
    user.password_hash = _hash_password(body.new_password)
    session.commit()
    return {"status": "ok"}


@router.post("/impersonate/{username}")
def impersonate(
    username: str,
    admin: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
):
    """Return a short-lived JWT scoped to another user — admin only.  No password required."""
    target = username.lower()
    if target == admin.username:
        raise HTTPException(400, "Cannot impersonate yourself")
    user = session.execute(
        select(User).where(User.username == target)
    ).scalar_one_or_none()
    if not user:
        raise HTTPException(404, f"User '{username}' not found")
    if not user.is_active:
        raise HTTPException(400, "Cannot impersonate a disabled user")
    if user.role.value == "admin":
        raise HTTPException(403, "Cannot impersonate another admin")
    # Short-lived impersonation token (1 hour) with audit claim
    expire = datetime.now(timezone.utc) + timedelta(hours=1)
    token = jwt.encode(
        {"sub": user.username, "role": user.role.value.lower(),
         "exp": expire, "jti": str(uuid.uuid4()), "impersonated_by": admin.username},
        _settings.jwt_secret, algorithm=ALGORITHM,
    )
    log.warning("auth.impersonate", admin=admin.username, target=user.username)
    return {"token": token, "username": user.username, "role": user.role.value.lower()}


@router.put("/users/{username}/toggle")
def toggle_user(
    username: str,
    admin: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
):
    if username.lower() == admin.username:
        raise HTTPException(400, "Cannot disable your own account")
    user = session.execute(
        select(User).where(User.username == username.lower())
    ).scalar_one_or_none()
    if not user:
        raise HTTPException(404, f"User '{username}' not found")
    user.is_active = not user.is_active
    session.commit()
    return {"status": "ok", "is_active": user.is_active}
