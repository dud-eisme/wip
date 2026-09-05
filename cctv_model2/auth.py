"""
auth.py
JWT validation + RBAC dependencies. Model 2 does NOT issue new logins —
users authenticate against Model 1's /api/v1/auth/login and reuse that
token here. This file only needs to *validate* tokens (same SECRET_KEY,
same `users` table), not create accounts.
"""
import os
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from database import get_db
from models import User, UserRoleEnum, DepartmentEnum

SECRET_KEY = os.getenv("SECRET_KEY", "insecure-dev-key-change-me")
ALGORITHM = os.getenv("ALGORITHM", "HS256")

# tokenUrl points at Model 1's login endpoint purely so Swagger's "Authorize"
# button knows where to send a password grant — Model 2 doesn't host it.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=True)


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


def require_roles(*allowed_roles: UserRoleEnum):
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
    """Same convention as Model 1: None means unrestricted (Admin)."""
    if current_user.role == UserRoleEnum.ADMIN or current_user.department == DepartmentEnum.ADMIN:
        return None
    return current_user.department
