"""
API integration tests for Dashboard endpoints (app/api/dashboard.py).

Covers the real HTTP routes a PM uses on the 仪表盘 page:
  GET/PUT /api/dashboard/config                — dashboard configuration
  GET/PUT /api/dashboard/cl_monitoring_config  — CL monitoring config
  GET/POST/DELETE /api/dashboard/cl_suppress   — ignore-CL list
  GET  /api/dashboard/summary                  — aggregated metrics (network mocked)
  GET  /api/dashboard/cl_status                — CL status (network mocked)
  GET  /api/dashboard/project-issues           — project issues (network mocked)

Config routes persist through the real settings_service (user DB settings);
the metric routes that would call Jira/Gerrit MCP are mocked to keep tests
off the network while still exercising the real HTTP endpoints.
"""

import pytest


class TestDashboardConfig:
    """GET/PUT /api/dashboard/config."""

    @pytest.mark.asyncio
    async def test_get_default_config(self, async_client):
        """新用户无配置时返回默认配置结构。"""
        resp = await async_client.get("/api/dashboard/config")
        assert resp.status_code == 200
        assert isinstance(resp.json(), dict)

    @pytest.mark.asyncio
    async def test_update_config(self, async_client):
        """PM 保存仪表盘配置。"""
        payload = {
            "projects": ["AML123"],
            "thresholds": {"eta": 7},
            "refresh_interval_seconds": 30,
            "jira_web_url": "https://jira.example.com",
        }
        resp = await async_client.put("/api/dashboard/config", json=payload)
        assert resp.status_code == 200
        assert resp.json().get("projects") == ["AML123"]

    @pytest.mark.asyncio
    async def test_config_roundtrip(self, async_client):
        """保存后重新读取，配置持久化。"""
        await async_client.put("/api/dashboard/config", json={"projects": ["AML123"]})
        resp = await async_client.get("/api/dashboard/config")
        assert resp.status_code == 200
        assert resp.json().get("projects") == ["AML123"]


class TestClMonitoringConfig:
    """GET/PUT /api/dashboard/cl_monitoring_config."""

    @pytest.mark.asyncio
    async def test_get_default(self, async_client):
        """默认 CL 监控配置可读取。"""
        resp = await async_client.get("/api/dashboard/cl_monitoring_config")
        assert resp.status_code == 200
        assert isinstance(resp.json(), dict)

    @pytest.mark.asyncio
    async def test_save_and_read(self, async_client):
        """保存 CL 监控项目后重新读取。"""
        resp = await async_client.put(
            "/api/dashboard/cl_monitoring_config", json={"projects": ["AML123"]},
        )
        assert resp.status_code == 200
        resp2 = await async_client.get("/api/dashboard/cl_monitoring_config")
        assert resp2.status_code == 200


class TestClSuppress:
    """GET/POST/DELETE /api/dashboard/cl_suppress."""

    @pytest.mark.asyncio
    async def test_add_list_remove(self, async_client):
        """PM 标记 CL 不关注 → 列表可见 → 恢复关注。"""
        item = {"instance": "gerrit_gerrit1", "change_id": "12345", "jira_key": "TV-1", "reason": "manual"}
        resp = await async_client.post("/api/dashboard/cl_suppress", json={"items": [item]})
        assert resp.status_code == 200

        resp2 = await async_client.get("/api/dashboard/cl_suppress")
        assert resp2.status_code == 200
        assert any("12345" in str(x) for x in resp2.json())

        resp3 = await async_client.delete(
            "/api/dashboard/cl_suppress",
            params={"instance": "gerrit_gerrit1", "change_id": "12345", "jira_key": "TV-1"},
        )
        assert resp3.status_code == 200


class TestDashboardSummary:
    """GET /api/dashboard/summary (metric refresh mocked)."""

    @pytest.mark.asyncio
    async def test_summary_returns_structure(self, async_client, monkeypatch):
        """仪表盘概览返回指标结构。"""
        async def fake_load(force_live=False, user_id=None):
            return {"projects": [], "summary": {"total_issues": 0, "p0": 0}}

        monkeypatch.setattr("app.api.dashboard._load_or_refresh_dashboard", fake_load)
        resp = await async_client.get("/api/dashboard/summary")
        assert resp.status_code == 200
        assert isinstance(resp.json(), dict)

    @pytest.mark.asyncio
    async def test_cl_status_returns_list(self, async_client, monkeypatch):
        """CL 状态返回列表（快照服务 mock）。"""
        async def fake_load(refresh=False, user_id=None):
            return []

        monkeypatch.setattr("app.api.dashboard._load_or_refresh_snapshots", fake_load)
        resp = await async_client.get("/api/dashboard/cl_status")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    @pytest.mark.asyncio
    async def test_project_issues(self, async_client, monkeypatch):
        """项目问题列表（Jira 查询 mock）。"""
        async def fake_issues(project, max_results=200, filter_type="all", user_id=None):
            return [{"key": "TV-1"}]

        monkeypatch.setattr("app.api.dashboard._load_project_issues", fake_issues)
        resp = await async_client.get("/api/dashboard/project-issues", params={"project": "AML123"})
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


class TestFocusSets:
    """P-B/C：关注集合 API（创建/列表/取消关注）。"""

    @pytest.mark.asyncio
    async def test_create_and_list(self, async_client):
        """创建关注集合后可在列表查询到（含健康度）。"""
        resp = await async_client.post("/api/dashboard/focus_sets", json={
            "type": "filter", "identifier": "60513",
            "name": "本周必清 Bug", "jql": "project = TV",
            "deadline": "2026-08-13", "metric_type": "fix_rate", "metric_target": 0.8,
        })
        assert resp.status_code == 200
        assert resp.json()["identifier"] == "60513"

        resp2 = await async_client.get("/api/dashboard/focus_sets")
        assert resp2.status_code == 200
        sets = resp2.json()
        assert any(s["identifier"] == "60513" for s in sets)
        fs = next(s for s in sets if s["identifier"] == "60513")
        assert "health" in fs
        assert fs["deadline"] == "2026-08-13"
        assert fs["metric"]["target"] == 0.8

    @pytest.mark.asyncio
    async def test_create_upsert_by_identifier(self, async_client):
        """同一 identifier 重复创建 → 更新而非重复。"""
        payload = {"type": "filter", "identifier": "999", "name": "集合A", "jql": "project = TV"}
        first = await async_client.post("/api/dashboard/focus_sets", json=payload)
        assert first.status_code == 200

        # 同 identifier 更新名称
        payload["name"] = "集合A-更新"
        second = await async_client.post("/api/dashboard/focus_sets", json=payload)
        assert second.status_code == 200

        sets = (await async_client.get("/api/dashboard/focus_sets")).json()
        matches = [s for s in sets if s["identifier"] == "999"]
        assert len(matches) == 1  # 不重复创建
        assert matches[0]["name"] == "集合A-更新"

    @pytest.mark.asyncio
    async def test_invalid_deadline(self, async_client):
        """deadline 格式非法 → 400。"""
        resp = await async_client.post("/api/dashboard/focus_sets", json={
            "type": "filter", "identifier": "1", "name": "x",
            "deadline": "not-a-date",
        })
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_dismiss_focus_set(self, async_client):
        """取消关注后列表不再显示。"""
        created = await async_client.post("/api/dashboard/focus_sets", json={
            "type": "filter", "identifier": "77", "name": "要取消的集合", "jql": "project = TV",
        })
        fs_id = created.json()["id"]

        dismiss = await async_client.put(f"/api/dashboard/focus_sets/{fs_id}/dismiss")
        assert dismiss.status_code == 200
        assert dismiss.json()["success"] is True

        sets = (await async_client.get("/api/dashboard/focus_sets")).json()
        assert all(s["identifier"] != "77" for s in sets)

    @pytest.mark.asyncio
    async def test_dismiss_not_found(self, async_client):
        """取消不存在的集合 → 404。"""
        resp = await async_client.put("/api/dashboard/focus_sets/99999/dismiss")
        assert resp.status_code == 404
