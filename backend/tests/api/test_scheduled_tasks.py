"""定时任务管理 API 测试（2026-09-09）—— 走真实 HTTP 端点。

GET /api/scheduled_tasks / POST / PUT/{id} / DELETE/{id}
覆盖：列表/创建（含 config 校验 400）/启停更新/删除/越权 404。
"""

import pytest

GOOD_CONFIG = {
    "source": {"kind": "jira_jql", "jql": "filter = 61364"},
    "conditions": [
        {"kind": "status_not_in", "statuses": ["Verified", "Closed"]},
        {"kind": "stale_days", "days": 3, "basis": "last_comment"},
    ],
    "action": {"kind": "jira_comment", "template": "[~{owner}] {key} 请尽快处理"},
}


class TestScheduledTaskApi:
    """定时任务 CRUD API。"""

    @pytest.mark.asyncio
    async def test_create_and_list(self, async_client, db_session):
        resp = await async_client.post("/api/scheduled_tasks", json={
            "name": "API创建测试",
            "config": GOOD_CONFIG,
            "interval_minutes": 30,
            "run_until": "2026-09-20",
            "description": "测试任务",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "API创建测试"
        assert body["run_until"] == "2026-09-20"
        assert body["active"] is True

        resp2 = await async_client.get("/api/scheduled_tasks")
        assert resp2.status_code == 200
        assert any(t["name"] == "API创建测试" for t in resp2.json())

    @pytest.mark.asyncio
    async def test_create_invalid_config_400(self, async_client, db_session):
        resp = await async_client.post("/api/scheduled_tasks", json={
            "name": "坏配置",
            "config": {"foo": "bar"},
        })
        assert resp.status_code == 400
        assert "config" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_create_bad_run_until_400(self, async_client, db_session):
        resp = await async_client.post("/api/scheduled_tasks", json={
            "name": "坏日期",
            "config": GOOD_CONFIG,
            "run_until": "20-09-2026",
        })
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_update_toggle_and_delete(self, async_client, db_session):
        created = (await async_client.post("/api/scheduled_tasks", json={
            "name": "启停删除测试",
            "config": GOOD_CONFIG,
        })).json()
        tid = created["id"]

        resp = await async_client.put(f"/api/scheduled_tasks/{tid}", json={"active": False})
        assert resp.status_code == 200
        assert resp.json()["active"] is False

        resp2 = await async_client.delete(f"/api/scheduled_tasks/{tid}")
        assert resp2.status_code == 200
        assert resp2.json()["success"] is True

        resp3 = await async_client.get("/api/scheduled_tasks")
        assert all(t["id"] != tid for t in resp3.json())

    @pytest.mark.asyncio
    async def test_update_not_found_404(self, async_client, db_session):
        resp = await async_client.put("/api/scheduled_tasks/999999", json={"active": False})
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_not_found_404(self, async_client, db_session):
        resp = await async_client.delete("/api/scheduled_tasks/999999")
        assert resp.status_code == 404
