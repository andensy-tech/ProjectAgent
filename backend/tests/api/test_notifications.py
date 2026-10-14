"""
API integration tests for 站内消息中心（P-F：monitoring_optimization）。

  GET  /api/notifications               — 消息列表
  GET  /api/notifications/unread-count  — 未读数（顶栏红点）
  POST /api/notifications/{id}/read     — 标记已读
  POST /api/notifications/read-all      — 全部已读
  联动：generate_alerts 生成预警 → 自动写站内消息
"""

import pytest


class TestNotificationList:
    """GET /api/notifications."""

    @pytest.mark.asyncio
    async def test_list_and_unread(self, async_client, db_session):
        """预置消息后列表可见 + 未读数正确。"""
        from app.models.notification import Notification

        db_session.add(Notification(user_id=1, type="alert", title="测试预警", content="有 2 条新风险"))
        await db_session.commit()

        resp = await async_client.get("/api/notifications")
        assert resp.status_code == 200
        assert any(n["title"] == "测试预警" for n in resp.json())

        resp2 = await async_client.get("/api/notifications/unread-count")
        assert resp2.status_code == 200
        assert resp2.json()["count"] >= 1


class TestMarkRead:
    """标记已读。"""

    @pytest.mark.asyncio
    async def test_mark_single_read(self, async_client, db_session):
        from app.models.notification import Notification

        n = Notification(user_id=1, type="alert", title="单条")
        db_session.add(n)
        await db_session.commit()
        await db_session.refresh(n)

        resp = await async_client.post(f"/api/notifications/{n.id}/read")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

        count = (await async_client.get("/api/notifications/unread-count")).json()["count"]
        assert count == 0

    @pytest.mark.asyncio
    async def test_mark_all_read(self, async_client, db_session):
        from app.models.notification import Notification

        db_session.add(Notification(user_id=1, type="alert", title="a"))
        db_session.add(Notification(user_id=1, type="alert", title="b"))
        await db_session.commit()

        resp = await async_client.post("/api/notifications/read-all")
        assert resp.json()["marked"] == 2

        count = (await async_client.get("/api/notifications/unread-count")).json()["count"]
        assert count == 0

    @pytest.mark.asyncio
    async def test_mark_not_found(self, async_client):
        """标记不存在的消息 → 404。"""
        resp = await async_client.post("/api/notifications/99999/read")
        assert resp.status_code == 404


class TestAlertNotificationLink:
    """联动：generate_alerts 生成预警 → 自动写站内消息。"""

    @pytest.mark.asyncio
    async def test_alert_generates_notification(self, db_session):
        """P0 超期触发 R1 预警后，生成站内消息。"""
        from datetime import datetime, timedelta
        from sqlalchemy import select

        from app.models.notification import Notification
        from app.services.alert_service import generate_alerts

        overdue = (datetime.now() + timedelta(days=-1)).strftime("%Y-%m-%d")
        stale = (datetime.now() + timedelta(days=-10)).strftime("%Y-%m-%dT%H:%M:%S")
        dashboard = {
            "projects": [{
                "name": "AML123", "enabled": True, "issues": 1, "epic_total": 0,
                "_count_issues": [
                    {"key": "TV-1", "priority": "P0", "duedate": overdue, "updated": stale},
                ],
            }],
            "thresholds": {"stale_days": 7, "eta_expiring_days": 7},
        }
        created = await generate_alerts(dashboard_summary=dashboard, cl_snapshots=[], user_id=1)
        assert created  # 预警已生成

        result = await db_session.execute(select(Notification).where(Notification.user_id == 1))
        notifs = result.scalars().all()
        assert any("风险预警" in n.title for n in notifs)
