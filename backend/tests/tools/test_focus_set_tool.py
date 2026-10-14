"""
create_focus_set 工具测试（P-B：聊天式关注指令 → 看板状态图）。

验证：解析 filter → 拉成员 → 持久化 FocusSet + 刷新成员 → 看板可见。
jira_resolve_filter 被 mock（避免真实 Jira 网络），FocusSet 持久化走真实 DB。
"""

import pytest

from app.tools.focus_set_tool import create_focus_set


@pytest.fixture
def fake_filter_result():
    """mock jira_resolve_filter 返回的 filter 元信息 + 成员。"""
    return {
        "success": True,
        "name": "本周必清 Bug",
        "jql": "project = TV AND status not in (Closed)",
        "issues": [
            {"key": "TV-1", "summary": "问题1", "status": "Open", "priority": "P1",
             "assignee": "owner",
             "created": "2026-07-01T10:00:00.000+0000",
             "updated": "2026-08-01T10:00:00.000+0000", "duedate": "2026-08-13"},
            {"key": "TV-2", "summary": "问题2", "status": "Resolved", "priority": "P2",
             "assignee": "owner2",
             "created": "2026-07-02T10:00:00.000+0000",
             "updated": "2026-08-09T10:00:00.000+0000", "duedate": ""},
        ],
    }


class TestCreateFocusSet:
    """创建关注集合。"""

    @pytest.mark.asyncio
    async def test_create_from_filter(self, async_client, monkeypatch, fake_filter_result):
        """filter 链接 → 自动解析 name/jql/成员 → 建集合 → 看板可见。"""
        from datetime import date, timedelta
        from app.mcp.capability import capability

        async def _fake(p, c):
            return fake_filter_result
        monkeypatch.setattr("app.tools.focus_set_tool.jira_resolve_filter", _fake)

        # refresh 停滞候选会拉评论（get_issue_comments），mock 返回空评论
        async def _fake_mcp(server, tool, params, user_id=None, timeout=None):
            return {"comments": []} if tool == "get_issue_comments" else {"issues": []}
        monkeypatch.setattr(capability, "call", _fake_mcp)

        deadline = (date.today() + timedelta(days=5)).isoformat()
        result = await create_focus_set({
            "type": "filter",
            "filter_id": "https://jira.example.com/issues/?filter=60513",
            "deadline": deadline,
            "metric_type": "fix_rate", "metric_target": 0.8,
        }, {"_user_id": 1})

        assert result["success"] is True
        assert result["name"] == "本周必清 Bug"
        assert result["member_count"] == 2
        assert result["deadline"] == deadline

        # 看板 API 可见
        sets = (await async_client.get("/api/dashboard/focus_sets")).json()
        fs = next(s for s in sets if s["identifier"] == "60513")
        assert fs["health"]["total"] == 2
        assert fs["health"]["done"] == 1  # TV-2 Resolved
        # 1/2=50% < 80% 目标，5 天后到期 → yellow；TV-1 停滞 → yellow
        assert fs["health"]["status"] == "yellow"
        # 成员/停滞成员含 created（创建时间）字段
        assert fs["members"][0]["created"] == "2026-07-01T10:00:00.000+0000"
        assert fs["health"]["stagnant_members"][0]["created"] == "2026-07-01T10:00:00.000+0000"
        assert fs["health"]["stagnant_members"][0]["assignee"] == "owner"

    @pytest.mark.asyncio
    async def test_create_manual_with_jql(self, async_client, monkeypatch):
        """manual 类型 + 自定义 JQL → 拉成员建集合。"""
        from app.mcp.capability import capability

        async def fake_capability_call(server, tool, params, user_id=None, timeout=None):
            return {"issues": [{"key": "TV-10", "fields": {
                "summary": "手工", "status": {"name": "Open"}, "priority": {"name": "P2"},
                "updated": "2026-08-09T10:00:00.000+0000", "duedate": "",
            }}]}

        monkeypatch.setattr(capability, "call", fake_capability_call)

        result = await create_focus_set({
            "type": "manual", "identifier": "my-focus", "name": "我的关注",
            "jql": "project = TV AND priority = P2",
        }, {"_user_id": 1})

        assert result["success"] is True
        assert result["member_count"] == 1

        sets = (await async_client.get("/api/dashboard/focus_sets")).json()
        assert any(s["identifier"] == "my-focus" for s in sets)

    @pytest.mark.asyncio
    async def test_filter_error_passthrough(self, async_client, monkeypatch):
        """filter 解析失败（如 Jira 未配置）→ 错误透传。"""
        async def _fake_err(p, c):
            return {"success": False, "error": "Jira 未配置服务器"}
        monkeypatch.setattr("app.tools.focus_set_tool.jira_resolve_filter", _fake_err)

        result = await create_focus_set({"type": "filter", "filter_id": "1"}, {"_user_id": 1})
        assert result["success"] is False
        assert "未配置" in result["error"]

    @pytest.mark.asyncio
    async def test_missing_identifier(self, async_client):
        """缺少 identifier → 错误。"""
        result = await create_focus_set({"type": "filter"}, {"_user_id": 1})
        assert result["success"] is False
        assert "filter_id" in result["error"]

    @pytest.mark.asyncio
    async def test_invalid_deadline(self, async_client, monkeypatch, fake_filter_result):
        """deadline 格式非法 → 错误。"""
        async def _fake(p, c):
            return fake_filter_result
        monkeypatch.setattr("app.tools.focus_set_tool.jira_resolve_filter", _fake)
        result = await create_focus_set(
            {"type": "filter", "filter_id": "1", "deadline": "bad-date"}, {"_user_id": 1},
        )
        assert result["success"] is False
        assert "YYYY-MM-DD" in result["error"]

    @pytest.mark.asyncio
    async def test_create_epic_from_key(self, async_client, monkeypatch):
        """epic key → 自动生成 JQL 并拉取成员，同时建立周基线。"""
        from datetime import date, timedelta
        from app.mcp.capability import capability

        async def fake_capability_call(server, tool, params, user_id=None, timeout=None):
            assert params.get("jql") == '"Epic Link" = TV-918177'
            return {"issues": [
                {"key": "TV-1", "fields": {
                    "summary": "epic story", "status": {"name": "Open"}, "priority": {"name": "P2"},
                    "assignee": "owner",
                    "created": "2026-07-01T10:00:00.000+0000",
                    "updated": "2026-08-09T10:00:00.000+0000", "duedate": "",
                }},
            ]}

        monkeypatch.setattr(capability, "call", fake_capability_call)

        deadline = (date.today() + timedelta(days=5)).isoformat()
        result = await create_focus_set({
            "type": "epic", "identifier": "TV-918177",
            "deadline": deadline, "metric_type": "fix_rate", "metric_target": 0.8,
        }, {"_user_id": 1})

        assert result["success"] is True
        assert result["type"] == "epic"
        assert result["member_count"] == 1
        # 看板 API 可见且含基线
        sets = (await async_client.get("/api/dashboard/focus_sets")).json()
        fs = next(s for s in sets if s["identifier"] == "TV-918177")
        assert fs["health"]["baseline_total"] == 1
        assert fs["health"]["baseline_date"] is not None

    @pytest.mark.asyncio
    async def test_create_epic_from_url(self, async_client, monkeypatch):
        """epic browse URL → 解析出 key 并自动生成 JQL。"""
        from app.mcp.capability import capability

        async def fake_capability_call(server, tool, params, user_id=None, timeout=None):
            return {"issues": []}

        monkeypatch.setattr(capability, "call", fake_capability_call)

        result = await create_focus_set({
            "type": "epic", "identifier": "https://jira.example.com/browse/TV-918177",
        }, {"_user_id": 1})

        assert result["success"] is True
        assert result["identifier"] == "TV-918177"
