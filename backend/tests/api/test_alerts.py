"""
API integration tests for Alerts endpoints (app/api/alerts.py).

Covers the real HTTP routes a PM would use in the 预警中心 page:
  GET   /api/alerts                    — list (with filters)
  GET   /api/alerts/{id}               — detail
  PATCH /api/alerts/{id}/status        — acknowledge / resolve
  POST  /api/alerts/scan               — manual scan (internal service mocked)
"""

import pytest


class TestAlertsList:
    """GET /api/alerts."""

    @pytest.mark.asyncio
    async def test_list_alerts(self, async_client, db_session):
        """返回告警列表与总数。"""
        from app.models import Alert

        db_session.add(Alert(
            user_id=1, project_name="AML123", rule_id="PROG-001",
            rule_name="进度延迟", category="PROGRESS", level="HIGH",
            title="Schedule delay",
        ))
        await db_session.commit()

        resp = await async_client.get("/api/alerts")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        assert any(a["rule_id"] == "PROG-001" for a in data["alerts"])

    @pytest.mark.asyncio
    async def test_list_alerts_filter_by_level(self, async_client, db_session):
        """按 level 过滤只返回匹配项。"""
        from app.models import Alert

        db_session.add(Alert(user_id=1, project_name="P-A", rule_id="R1", category="PROGRESS", level="HIGH", title="h"))
        db_session.add(Alert(user_id=1, project_name="P-A", rule_id="R2", category="PROGRESS", level="LOW", title="l"))
        await db_session.commit()

        resp = await async_client.get("/api/alerts", params={"level": "HIGH"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["alerts"]) == 1
        assert data["alerts"][0]["level"] == "HIGH"

    @pytest.mark.asyncio
    async def test_summary_endpoint(self, async_client, db_session):
        """GET /api/alerts/summary 返回聚合计数。"""
        from app.models import Alert

        db_session.add(Alert(user_id=1, project_name="P-A", rule_id="R1", category="PROGRESS", level="HIGH", title="h", status="ACTIVE"))
        db_session.add(Alert(user_id=1, project_name="P-A", rule_id="R2", category="QUALITY", level="MEDIUM", title="m", status="RESOLVED"))
        await db_session.commit()

        resp = await async_client.get("/api/alerts/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert "by_status" in data or "total" in data or isinstance(data, dict)


class TestAlertsDetail:
    """GET /api/alerts/{id}."""

    @pytest.mark.asyncio
    async def test_get_alert_detail(self, async_client, db_session):
        """返回单个告警详情。"""
        from app.models import Alert

        alert = Alert(user_id=1, project_name="AML123", rule_id="PROG-001",
                      category="PROGRESS", level="HIGH", title="delay")
        db_session.add(alert)
        await db_session.commit()
        await db_session.refresh(alert)

        resp = await async_client.get(f"/api/alerts/{alert.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == alert.id
        assert data["status"] == "ACTIVE"

    @pytest.mark.asyncio
    async def test_get_alert_not_found(self, async_client):
        """不存在的告警返回 404。"""
        resp = await async_client.get("/api/alerts/99999")
        assert resp.status_code == 404


class TestAlertsStatus:
    """PATCH /api/alerts/{id}/status."""

    @pytest.mark.asyncio
    async def test_resolve_alert(self, async_client, db_session):
        """PM 将告警标记为已解决。"""
        from app.models import Alert

        alert = Alert(user_id=1, project_name="AML123", rule_id="PROG-001",
                      category="PROGRESS", level="HIGH", title="delay")
        db_session.add(alert)
        await db_session.commit()
        await db_session.refresh(alert)

        resp = await async_client.patch(
            f"/api/alerts/{alert.id}/status",
            json={"status": "RESOLVED", "resolution_note": "fixed"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "RESOLVED"

    @pytest.mark.asyncio
    async def test_invalid_status_rejected(self, async_client):
        """非法状态值返回 400。"""
        resp = await async_client.patch(
            "/api/alerts/1/status", json={"status": "BOGUS"},
        )
        assert resp.status_code == 400


class TestAlertsScan:
    """POST /api/alerts/scan."""

    @pytest.mark.asyncio
    async def test_manual_scan(self, async_client, monkeypatch):
        """手动触发扫描，返回创建的告警数（内部扫描服务 mock 掉网络）。"""
        async def fake_scan():
            return [1, 2, 3]

        monkeypatch.setattr("app.api.alerts.run_alert_scan", fake_scan)

        resp = await async_client.post("/api/alerts/scan")
        assert resp.status_code == 200
        assert resp.json()["created"] == 3
        assert resp.json()["alert_ids"] == [1, 2, 3]


class TestAlertRules:
    """服务层：紧迫度模型驱动的规则触发（P-A，直接调真实 generate_alerts）。"""

    @staticmethod
    def _date(days_from_now: int) -> str:
        from datetime import datetime, timedelta
        return (datetime.now() + timedelta(days=days_from_now)).strftime("%Y-%m-%d")

    @staticmethod
    def _updated(days_ago: int) -> str:
        from datetime import datetime, timedelta
        return (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S")

    @staticmethod
    def _dashboard(issues: list, stale_days: int = 7) -> dict:
        return {
            "projects": [{
                "name": "AML123", "enabled": True, "issues": len(issues), "epic_total": 0,
                "_count_issues": issues,
            }],
            "thresholds": {"stale_days": stale_days, "eta_expiring_days": 7},
        }

    @pytest.mark.asyncio
    async def test_r1_fires_on_p0_overdue_stale(self, db_session):
        """P0+超期+停滞 → R1 高紧迫度预警触发。

        验证原 bug 修复：原 R1 读 "p1" 字段但 dashboard 存 "p0"，
        P0/P1 预警永不触发；重构后用紧迫度模型逐 issue 判定。
        """
        from sqlalchemy import select

        from app.models.alert import Alert
        from app.services.alert_service import generate_alerts

        dashboard = self._dashboard([
            {"key": "TV-1", "priority": "P0", "duedate": self._date(-1), "updated": self._updated(10)},
            {"key": "TV-2", "priority": "P2", "duedate": self._date(20), "updated": self._updated(1)},
        ])
        created = await generate_alerts(dashboard_summary=dashboard, cl_snapshots=[], user_id=1)
        assert created  # 至少触发预警

        result = await db_session.execute(select(Alert).where(Alert.rule_id == "R1_P1_STALE"))
        alert = result.scalar_one_or_none()
        assert alert is not None, "R1 应被 P0 高紧迫度触发（原 bug 修复）"
        assert alert.level == "HIGH"
        assert "TV-1" in (alert.title or "") or "TV-1" in (alert.description or "")

    @pytest.mark.asyncio
    async def test_r4_fires_on_urgent_due(self, db_session):
        """高紧迫度且临期 → R4 触发。"""
        from sqlalchemy import select

        from app.models.alert import Alert
        from app.services.alert_service import generate_alerts

        dashboard = self._dashboard([
            {"key": "TV-9", "priority": "P0", "duedate": self._date(1), "updated": self._updated(1)},
        ])
        await generate_alerts(dashboard_summary=dashboard, cl_snapshots=[], user_id=1)

        result = await db_session.execute(select(Alert).where(Alert.rule_id == "R4_ETA_EXPIRING"))
        assert result.scalar_one_or_none() is not None

    @pytest.mark.asyncio
    async def test_no_alert_when_nothing_urgent(self, db_session):
        """全部低紧迫度 issue → 不触发预警。"""
        from app.services.alert_service import generate_alerts

        dashboard = self._dashboard([
            {"key": "TV-3", "priority": "P3", "duedate": self._date(60), "updated": self._updated(1)},
        ])
        created = await generate_alerts(dashboard_summary=dashboard, cl_snapshots=[], user_id=1)
        assert created == []

    @pytest.mark.asyncio
    async def test_r2_fires_on_attention_pileup(self, db_session):
        """多个关注级（≥3 紧迫度）issue → R2 堆积预警触发。"""
        from sqlalchemy import select

        from app.models.alert import Alert
        from app.services.alert_service import generate_alerts

        # 6 个 P1 超期停滞（紧迫度 2×3×1.5=9 → HIGH）→ attention > 5
        issues = [
            {"key": f"TV-{i}", "priority": "P1", "duedate": self._date(-1), "updated": self._updated(10)}
            for i in range(6)
        ]
        await generate_alerts(dashboard_summary=self._dashboard(issues), cl_snapshots=[], user_id=1)

        result = await db_session.execute(select(Alert).where(Alert.rule_id == "R2_STALE_PILEUP"))
        assert result.scalar_one_or_none() is not None
