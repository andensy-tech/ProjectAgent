"""
Authentication API — Multi-user with admin/user role separation.

Endpoints:
  POST /api/auth/login              — Login, returns JWT + force_password_change flag
  POST /api/auth/change-password    — Change own password (force or voluntary)
  POST /api/auth/register           — Create new user (admin only)
  GET  /api/auth/me                 — Current user info
  GET  /api/auth/users              — List all users (admin only)
  DELETE /api/auth/users/{id}       — Delete user (admin only)
  PUT  /api/auth/users/{id}/toggle-active  — Enable/disable user (admin only)
  POST /api/auth/users/{id}/reset-password  — Reset user password (admin only)
"""

import asyncio
import logging
from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.exc import OperationalError
from app.db import get_db
from app.core.security import verify_password, get_password_hash, create_access_token
from app.core.config import settings
from app.api.deps import get_current_active_user
from app.models import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict
    force_password_change: bool = False


class ChangePasswordRequest(BaseModel):
    old_password: str = ""
    new_password: str


class RegisterRequest(BaseModel):
    username: str
    password: str
    email: str = ""
    full_name: str = ""

    class Config:
        json_schema_extra = {
            "example": {
                "username": "newuser",
                "password": "securepass123",
                "email": "user@example.com",
                "full_name": "New User",
            }
        }


class ResetPasswordRequest(BaseModel):
    new_password: str


def _build_token_data(user: User) -> dict:
    """Build JWT payload with is_superuser."""
    return {
        "sub": user.username,
        "user_id": user.id,
        "username": user.username,
        "is_superuser": user.is_superuser,
    }


def _build_login_response(user: User, token: str) -> LoginResponse:
    """Build login response with force_password_change flag."""
    return LoginResponse(
        access_token=token,
        force_password_change=user.force_password_change,
        user={
            "user_id": user.id,
            "username": user.username,
            "is_superuser": user.is_superuser,
        },
    )


@router.post("/login", response_model=LoginResponse)
async def login(request: LoginRequest, db: AsyncSession = Depends(get_db)):
    """Login with username/password. Returns JWT token."""
    result = await db.execute(select(User).where(User.username == request.username))
    user = result.scalar_one_or_none()

    if not user or not verify_password(request.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="用户已被禁用",
        )

    access_token = create_access_token(
        data=_build_token_data(user),
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )

    return _build_login_response(user, access_token)


@router.post("/change-password")
async def change_password(
    request: ChangePasswordRequest,
    current_user: dict = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Change the current user's password.

    Supports two scenarios:
    1. Force password change (first login / admin reset) — old_password not required
    2. Voluntary change — old_password required for verification

    Retries on SQLite locking errors to handle concurrent background writes.
    """
    username = current_user.get("username") or current_user.get("sub", "")
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    # For voluntary password change, verify old password
    if not user.force_password_change:
        if not request.old_password:
            raise HTTPException(status_code=400, detail="请输入当前密码")
        if not verify_password(request.old_password, user.hashed_password):
            raise HTTPException(status_code=400, detail="原密码不正确")

    if len(request.new_password) < 6:
        raise HTTPException(status_code=400, detail="新密码至少6个字符")

    new_hash = get_password_hash(request.new_password)
    user.hashed_password = new_hash
    user.force_password_change = False

    # Retry up to 3 times on SQLite locking errors
    for attempt in range(3):
        try:
            await db.commit()
            return {"message": "密码已修改"}
        except OperationalError as e:
            if "database is locked" not in str(e):
                raise
            if attempt < 2:
                logger.warning("change_password DB locked, retrying (%d/3)...", attempt + 1)
                await db.rollback()
                await asyncio.sleep(0.5 * (attempt + 1))
                # Re-attach the user object to the new transaction
                user.hashed_password = new_hash
                user.force_password_change = False
                continue
            raise HTTPException(status_code=503, detail="数据库繁忙，请稍后重试")


@router.post("/register")
async def register(
    request: RegisterRequest,
    current_user: dict = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Register a new user. Requires admin privileges.

    New user logs in with the initial password set here,
    and is forced to change it on first login.
    """
    if not current_user.get("is_superuser"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="仅管理员可创建用户",
        )

    # Validate input
    if len(request.username) < 2:
        raise HTTPException(status_code=400, detail="用户名至少2个字符")
    if len(request.password) < 6:
        raise HTTPException(status_code=400, detail="密码至少6个字符")
    if request.email and "@" not in request.email:
        raise HTTPException(status_code=400, detail="邮箱格式不正确")

    # Check duplicate
    existing = await db.execute(
        select(User).where(User.username == request.username)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="用户名已存在",
        )

    user = User(
        username=request.username,
        email=request.email or None,
        full_name=request.full_name or None,
        hashed_password=get_password_hash(request.password),
        is_active=True,
        is_superuser=False,
        force_password_change=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "full_name": user.full_name,
        "is_superuser": user.is_superuser,
    }


@router.get("/me")
async def get_current_user_info(
    current_user: dict = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Get current user info from DB."""
    username = current_user.get("username") or current_user.get("sub", "")
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    return {
        "id": user.id,
        "username": user.username,
        "email": user.email or "",
        "full_name": user.full_name or "",
        "is_superuser": user.is_superuser,
        "is_active": user.is_active,
        "force_password_change": user.force_password_change,
    }


@router.get("/users")
async def list_users(
    current_user: dict = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """List all users (admin only)."""
    if not current_user.get("is_superuser"):
        raise HTTPException(status_code=403, detail="仅管理员可查看用户列表")

    result = await db.execute(select(User))
    users = result.scalars().all()
    return [
        {
            "id": u.id,
            "username": u.username,
            "email": u.email or "",
            "full_name": u.full_name or "",
            "is_superuser": u.is_superuser,
            "is_active": u.is_active,
            "force_password_change": u.force_password_change,
            "feishu_open_id": u.feishu_open_id,
            "created_at": u.created_at.isoformat() if u.created_at else None,
        }
        for u in users
    ]


@router.delete("/users/{target_id}")
async def delete_user(
    target_id: int,
    current_user: dict = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a user (admin only). Cannot delete self or last admin."""
    if not current_user.get("is_superuser"):
        raise HTTPException(status_code=403, detail="仅管理员可删除用户")

    if current_user.get("user_id") == target_id:
        raise HTTPException(status_code=400, detail="不能删除自己")

    result = await db.execute(select(User).where(User.id == target_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    if user.is_superuser:
        # Check if this is the last remaining admin
        admin_count = await db.execute(
            select(func.count(User.id)).where(User.is_superuser == True)
        )
        if admin_count.scalar() <= 1:
            raise HTTPException(status_code=400, detail="不能删除最后一个管理员")

    # ── Clean up all user-related data before deletion ──
    # Temporarily disable FK checks for cleanup. Some tables have FK
    # constraints referencing non-existent parent tables (e.g., alerts→projects
    # was removed in migration), and messages FK→conversations requires
    # specific deletion order (messages before conversations).
    from sqlalchemy import text as _sql_text
    await db.execute(_sql_text("PRAGMA foreign_keys=OFF"))

    _CHILD_TABLES = [
        # messages has no user_id column — delete via conversation_id subquery
        "DELETE FROM messages WHERE conversation_id IN (SELECT id FROM conversations WHERE user_id = :uid)",
        # user-scoped tables (all have user_id, with FK→users CASCADE after migration)
        "DELETE FROM conversations WHERE user_id = :uid",
        "DELETE FROM user_settings WHERE user_id = :uid",
        "DELETE FROM cl_monitor_snapshots WHERE user_id = :uid",
        "DELETE FROM dashboard_snapshots WHERE user_id = :uid",
        "DELETE FROM metric_snapshots WHERE user_id = :uid",
        "DELETE FROM alerts WHERE user_id = :uid",
        "DELETE FROM skill_configs WHERE user_id = :uid",
        "DELETE FROM learning_records WHERE user_id = :uid",
        "DELETE FROM memory_logs WHERE user_id = :uid",
        "DELETE FROM user_profiles WHERE user_id = :uid",
        "DELETE FROM session_memories WHERE user_id = :uid",
        # project_memories, requirements, requirement_changes don't have user_id
        # — they are project-scoped, not user-scoped. Skip.
    ]
    for _stmt in _CHILD_TABLES:
        try:
            await db.execute(_sql_text(_stmt), {"uid": target_id})
        except Exception:
            pass

    await db.delete(user)

    await db.commit()
    return {"message": "用户已删除", "detail": f"已清除 {len(_CHILD_TABLES)} 张关联表的数据"}


@router.put("/users/{target_id}/toggle-active")
async def toggle_user_active(
    target_id: int,
    current_user: dict = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Enable or disable a user (admin only). Cannot disable self."""
    if not current_user.get("is_superuser"):
        raise HTTPException(status_code=403, detail="仅管理员可操作")

    if current_user.get("user_id") == target_id:
        raise HTTPException(status_code=400, detail="不能禁用自己")

    result = await db.execute(select(User).where(User.id == target_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    user.is_active = not user.is_active
    await db.commit()
    return {
        "id": user.id,
        "username": user.username,
        "is_active": user.is_active,
    }


@router.post("/users/{target_id}/reset-password")
async def admin_reset_password(
    target_id: int,
    request: ResetPasswordRequest,
    current_user: dict = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Admin resets a user's password and forces change on next login."""
    if not current_user.get("is_superuser"):
        raise HTTPException(status_code=403, detail="仅管理员可操作")

    result = await db.execute(select(User).where(User.id == target_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    if len(request.new_password) < 6:
        raise HTTPException(status_code=400, detail="密码至少6个字符")

    user.hashed_password = get_password_hash(request.new_password)
    user.force_password_change = True
    await db.commit()
    return {"message": "密码已重置"}
