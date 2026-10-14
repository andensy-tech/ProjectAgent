"""
API dependencies — authentication, database session, etc.
"""

from typing import Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.core.security import decode_access_token
from app.core.config import settings
from app.db import get_db
from app.models import User

# Security scheme
security = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Optional[dict]:
    """
    Get current user from JWT token.

    Returns a dict with at least "username" and "user_id" keys.
    In DEBUG mode, returns a dev user if no credentials are provided.
    """
    if not credentials:
        if settings.DEBUG:
            return {"username": "developer", "user_id": 1, "sub": "developer", "is_superuser": True}
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    payload = decode_access_token(credentials.credentials)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )

    return payload


async def get_current_active_user(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get current active user (validates token + DB is_active flag)."""
    if not current_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    # Verify user still exists and is active in the database
    # This prevents disabled users from continuing to use valid JWTs
    username = current_user.get("username") or current_user.get("sub", "")
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在或已被禁用",
        )
    return current_user


async def get_current_db_user(
    current_user: dict = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    Get current user as a database User model instance.

    Queries the users table by username. Raises 401 if not found.
    Useful when endpoints need the DB user ID or other User fields.
    """
    username = current_user.get("username") or current_user.get("sub", "")
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在或已被禁用",
        )
    return user
