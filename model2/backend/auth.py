"""
auth.py
JWT validation + RBAC dependencies. Model 2 does NOT issue new logins —
users authenticate against Model 1's /api/v1/auth/login and reuse that
token here. This file only needs to *validate* tokens (same SECRET_KEY,
same `users` table), not create accounts.

PATCH NOTE (added): a plain <img src="..."> tag — the normal way to
display an MJPEG stream in a browser — cannot send a custom Authorization
header. The strict get_current_user dependency below is unusable for the
video-stream/snapshot endpoints as a result. get_current_user_flexible
adds a query-param fallback (?token=...) specifically for those two
endpoints, while every other endpoint keeps using the strict
header-only get_current_user unchanged.
"""
import os
from typing import Optional

from fastapi import Depends, HTTPException, Query, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from database import get_db
from models import User, UserRoleEnum, DepartmentEnum

JWT_PUBLIC_KEY = os.getenv("JWT_PUBLIC_KEY", "insecure-dev-key-change-me")
ALGORITHM = os.getenv("ALGORITHM", "HS256")

# tokenUrl points at Model 1's login endpoint purely so Swagger's "Authorize"
# button knows where to send a password grant — Model 2 doesn't host it.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=True)

# Same scheme but non-erroring — lets the flexible dependency below check
# "was there a header at all?" before deciding whether to fall back to the
# query param.
oauth2_scheme_optional = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


def _decode_token_to_user(token: str, db: Session) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, JWT_PUBLIC_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = db.query(User).filter(User.id == user_id).first()
    if user is None or not user.is_active:
        raise credentials_exception
    return user


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Strict, header-only auth — used by every endpoint except the
    <img>-tag-loaded stream/snapshot ones below."""
    return _decode_token_to_user(token, db)


async def get_current_user_flexible(
    header_token: Optional[str] = Depends(oauth2_scheme_optional),
    query_token: Optional[str] = Query(None, alias="token"),
    db: Session = Depends(get_db),
) -> User:
    """
    Same validation as get_current_user, but accepts the token via EITHER
    the Authorization header OR a ?token= query parameter. Use this only
    on endpoints that must be loadable from a plain <img src="..."> tag
    (which cannot send custom headers) — everywhere else, keep using the
    strict get_current_user so tokens aren't encouraged to leak into logs
    or browser history unnecessarily.
    """
    token = header_token or query_token
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated (no Authorization header or ?token= param).",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return _decode_token_to_user(token, db)


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
