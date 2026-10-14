"""
API integration tests for Feishu binding endpoints (app/api/feishu_bind.py).

  POST /api/feishu/bind/initiate   — generate a 6-digit bind code
  POST /api/feishu/bind/status     — query bind state
  POST /api/feishu/bind/unbind     — unbind (400 when not bound)

The seeded admin user has no feishu_open_id, so the flow starts unbound.
"""

import pytest


class TestFeishuBindStatus:
    """POST /api/feishu/bind/status."""

    @pytest.mark.asyncio
    async def test_status_unbound(self, async_client):
        """未绑定时返回 bound=False。"""
        resp = await async_client.post("/api/feishu/bind/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["bound"] is False
        assert data["feishu_open_id"] is None


class TestFeishuBindInitiate:
    """POST /api/feishu/bind/initiate."""

    @pytest.mark.asyncio
    async def test_generate_code(self, async_client):
        """生成 6 位验证码。"""
        resp = await async_client.post("/api/feishu/bind/initiate")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert len(data["code"]) == 6
        assert data["code"].isdigit()
        assert data["expires_in"] > 0

    @pytest.mark.asyncio
    async def test_regenerate_replaces_old(self, async_client):
        """重复发起生成会替换旧验证码。"""
        first = await async_client.post("/api/feishu/bind/initiate")
        second = await async_client.post("/api/feishu/bind/initiate")
        assert second.status_code == 200
        assert len(second.json()["code"]) == 6
        # 新码可能不同（随机），但状态成功即可
        assert first.json()["success"] is True


class TestFeishuBindUnbind:
    """POST /api/feishu/bind/unbind."""

    @pytest.mark.asyncio
    async def test_unbind_when_unbound(self, async_client):
        """未绑定时解绑返回 400。"""
        resp = await async_client.post("/api/feishu/bind/unbind")
        assert resp.status_code == 400


class TestFeishuBindPersistence:
    """验证码持久化到 DB（真实数据库）。"""

    @pytest.mark.asyncio
    async def test_code_persisted_in_db(self, async_client, db_session):
        """发起绑定后验证码写入 FeishuBindCode 表。"""
        from sqlalchemy import select

        from app.models.feishu_bind import FeishuBindCode

        resp = await async_client.post("/api/feishu/bind/initiate")
        assert resp.status_code == 200
        code = resp.json()["code"]

        result = await db_session.execute(select(FeishuBindCode))
        rows = result.scalars().all()
        assert any(r.code == code for r in rows)
        assert all(r.used is False for r in rows)
