"""Auth routes — JWT login, user management (admin), password change."""
from datetime import datetime, timedelta

import bcrypt as _bcrypt
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from common.config import get_settings
from db import SessionLocal, User, UserRole, get_session

router = APIRouter(prefix="/auth", tags=["auth"])

_settings = get_settings()
_security = HTTPBearer(auto_error=False)


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
    expire = datetime.utcnow() + timedelta(days=_settings.jwt_expire_days)
    return jwt.encode(
        {"sub": username, "role": role.lower(), "exp": expire},
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
    except JWTError:
        raise HTTPException(401, "Invalid or expired token")
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
    created_at: str

    class Config:
        from_attributes = True


# ── Public endpoints ──────────────────────────────────────────────────────────

@router.post("/login")
def login(body: LoginRequest, session: Session = Depends(get_session)):
    user = session.execute(
        select(User).where(User.username == body.username.lower())
    ).scalar_one_or_none()
    if not user or not user.is_active or not _verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Incorrect username or password")
    token = _make_token(user.username, user.role.value)
    return {"token": token, "username": user.username, "role": user.role.value.lower()}


@router.post("/reset-password")
def reset_password_public(body: ResetPasswordRequest, session: Session = Depends(get_session)):
    """Password reset without JWT — requires old password for verification."""
    user = session.execute(
        select(User).where(User.username == body.username.lower())
    ).scalar_one_or_none()
    if not user:
        raise HTTPException(404, "User not found")
    if not _verify_password(body.old_password, user.password_hash):
        raise HTTPException(401, "Current password is incorrect")
    if len(body.new_password) < 4:
        raise HTTPException(400, "New password must be at least 4 characters")
    user.password_hash = _hash_password(body.new_password)
    session.commit()
    return {"status": "ok"}


# ── Authenticated endpoints ───────────────────────────────────────────────────

@router.get("/me", response_model=UserOut)
def me(current: User = Depends(get_current_user)):
    return UserOut(
        id=current.id, username=current.username, role=current.role.value.lower(),
        is_active=current.is_active, created_at=current.created_at.isoformat(),
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
    if len(body.new_password) < 4:
        raise HTTPException(400, "New password must be at least 4 characters")
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
            is_active=u.is_active, created_at=u.created_at.isoformat(),
        )
        for u in rows
    ]


@router.post("/users", response_model=UserOut)
def create_user(
    body: CreateUserRequest,
    admin: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
):
    if len(body.password) < 4:
        raise HTTPException(400, "Password must be at least 4 characters")
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
        is_active=user.is_active, created_at=user.created_at.isoformat(),
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
    if len(body.new_password) < 4:
        raise HTTPException(400, "New password must be at least 4 characters")
    user = session.execute(
        select(User).where(User.username == username.lower())
    ).scalar_one_or_none()
    if not user:
        raise HTTPException(404, f"User '{username}' not found")
    user.password_hash = _hash_password(body.new_password)
    session.commit()
    return {"status": "ok"}


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
