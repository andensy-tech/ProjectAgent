"""
飞书账号绑定 API

提供三个端点：
- POST /api/feishu/bind/initiate — 生成绑定验证码
- POST /api/feishu/bind/status  — 查询绑定状态
- POST /api/feishu/bind/unbind  — 解绑飞书账号

流程：用户 Web 端发起 → 生成验证码 → 用户将验证码发给飞书机器人
→ FeishuAdapter 验证 → 更新 feishu_open_id → Web 端轮询确认
"""

import random
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, update, delete

from app.db.session import AsyncSessionLocal
from app.models.user import User
from app.models.feishu_bind import FeishuBindCode
from app.api.deps import get_current_db_user as _get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/feishu/bind", tags=["feishu"])

# 验证码有效期（分钟）
_CODE_TTL_MINUTES = 5


def _generate_code() -> str:
    """生成 6 位随机数字验证码."""
    return f"{random.randint(0, 999999):06d}"


@router.post("/initiate")
async def initiate_bind(current_user: User = Depends(_get_current_user)):
    """生成飞书绑定验证码.

    如果用户已绑定 → 返回 400
    如果存在未使用的旧验证码 → 标记为已使用，生成新验证码
    """
    if current_user.feishu_open_id:
        raise HTTPException(
            status_code=400,
            detail="您已经绑定了飞书账号，请先解绑后再重新绑定",
        )

    async with AsyncSessionLocal() as db:
        # 标记该用户所有未使用的旧验证码为已使用
        await db.execute(
            update(FeishuBindCode)
            .where(
                FeishuBindCode.user_id == current_user.id,
                FeishuBindCode.used == False,
            )
            .values(used=True)
        )
        await db.commit()

        # 生成新验证码（去重检测）
        code = _generate_code()
        while True:
            existing = await db.execute(
                select(FeishuBindCode).where(
                    FeishuBindCode.code == code,
                    FeishuBindCode.used == False,
                    FeishuBindCode.expires_at > datetime.now(timezone.utc),
                )
            )
            if not existing.scalar_one_or_none():
                break
            code = _generate_code()

        expires_at = datetime.now(timezone.utc) + timedelta(minutes=_CODE_TTL_MINUTES)
        bind_code = FeishuBindCode(
            user_id=current_user.id,
            code=code,
            expires_at=expires_at,
        )
        db.add(bind_code)
        await db.commit()

        logger.info("Feishu bind code generated for user %s: %s", current_user.id, code)

        return {
            "success": True,
            "code": code,
            "expires_in": _CODE_TTL_MINUTES * 60,
        }


@router.post("/status")
async def bind_status(current_user: User = Depends(_get_current_user)):
    """查询当前用户的飞书绑定状态. """
    return {
        "success": True,
        "bound": bool(current_user.feishu_open_id),
        "feishu_open_id": current_user.feishu_open_id,
    }


@router.post("/unbind")
async def unbind(current_user: User = Depends(_get_current_user)):
    """解绑飞书账号. """
    if not current_user.feishu_open_id:
        raise HTTPException(status_code=400, detail="您还没有绑定飞书账号")

    async with AsyncSessionLocal() as db:
        user = await db.get(User, current_user.id)
        if user:
            user.feishu_open_id = None
            await db.commit()

        # 同时清理该用户未使用的验证码
        await db.execute(
            delete(FeishuBindCode).where(
                FeishuBindCode.user_id == current_user.id,
                FeishuBindCode.used == False,
            )
        )
        await db.commit()

    logger.info("Feishu unbound for user %s", current_user.id)
    return {"success": True, "message": "已成功解绑飞书账号"}
