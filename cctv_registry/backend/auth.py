"""
auth.py
Password hashing, JWT issuance/validation, and RBAC dependencies
(department-scoped access control).
"""
import os
from datetime import datetime, timedelta, timezone
from typing import Optional, List

from fastapi import Depends, HTTPException, status, Security
from fastapi.security import OAuth2PasswordBearer, APIKeyHeader
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from database import get_db
from models import User, UserRoleEnum, DepartmentEnum

SECRET_KEY = os.getenv("SECRET_KEY", "insecure-dev-key-change-me")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# Loaded once at import time: "key1:Department,key2:Department"
_raw_vendor_keys = os.getenv("VENDOR_API_KEYS", "")
VENDOR_API_KEYS = {}
for pair in _raw_vendor_keys.split(","):
    pair = pair.strip()
    if not pair or ":" not in pair:
        continue
    key, dept = pair.split(":", 1)
    VENDOR_API_KEYS[key.strip()] = dept.strip()


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def authenticate_user(db: Session, email: str, password: str) -> Optional[User]:
    user = db.query(User).filter(User.email == email).first()
    if not user or not user.is_active:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    return user


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = db.query(User).filter(User.id == user_id).first()
    if user is None or not user.is_active:
        raise credentials_exception
    return user


# ---------------------------------------------------------------------------
# RBAC dependencies
# ---------------------------------------------------------------------------

def require_roles(*allowed_roles: UserRoleEnum):
    """Dependency factory: restrict an endpoint to specific roles (Admin always allowed)."""

    def dependency(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role == UserRoleEnum.ADMIN:
            return current_user
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{current_user.role.value}' is not permitted to perform this action.",
            )
        return current_user

    return dependency


def department_scope(current_user: User = Depends(get_current_user)) -> Optional[DepartmentEnum]:
    """
    Returns the department a non-admin user is restricted to, or None for
    Admins (meaning: unrestricted, all-department access).
    Use this in routers to filter queries and validate writes.
    """
    if current_user.role == UserRoleEnum.ADMIN or current_user.department == DepartmentEnum.ADMIN:
        return None
    return current_user.department


def enforce_department_write(target_department: DepartmentEnum, current_user: User) -> None:
    """Raise 403 if a non-admin user tries to write to a department that isn't theirs."""
    if current_user.role == UserRoleEnum.ADMIN or current_user.department == DepartmentEnum.ADMIN:
        return
    if current_user.department != target_department:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"User belongs to '{current_user.department.value}' and cannot "
                f"create/modify cameras for '{target_department.value}'."
            ),
        )


# ---------------------------------------------------------------------------
# Vendor API-key auth (for external onboarding webhook)
# ---------------------------------------------------------------------------

class VendorIdentity:
    def __init__(self, api_key: str, department: str):
        self.api_key = api_key
        self.department = department


def get_vendor_identity(api_key: str = Security(api_key_header)) -> "VendorIdentity":
    """Validates X-API-Key header against configured vendor keys and returns
    the vendor identity (key + department it is authorized to register for)."""
    if not api_key or api_key not in VENDOR_API_KEYS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing vendor API key.",
        )
    return VendorIdentity(api_key=api_key, department=VENDOR_API_KEYS[api_key])
