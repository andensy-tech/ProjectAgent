"""
risk_analyze 工具测试（P-G：LLM 风险精判）。

验证：预置关注集合成员 → 工具调 LLM（mock）→ 返回结构化风险分析。
LLM 被 mock（route_and_complete_structured），成员数据走真实 DB。
"""

import pytest

from app.tools.risk_analysis_tool import risk_analyze


class TestRiskAnalyze:
    """LLM 风险精判。"""

    @pytest.mark.asyncio
    async def test_analyze_with_focus_set(self, async_client, monkeypatch):
        """预置停滞成员 → 调 LLM → 返回结构化风险分析。"""
        from datetime import datetime, timedelta
        from app.services.focus_set_service import create_or_update_focus_set, refresh_focus_set_members
        from app.services.llm import router as router_module

        fs = await create_or_update_focus_set(1, "filter", "60513", "本周必清 Bug", jql="project = TV")
        stale = (datetime.now() + timedelta(days=-10)).strftime("%Y-%m-%dT%H:%M:%S")
        await refresh_focus_set_members(fs.id, [
            {"key": "TV-1", "summary": "客户P0问题", "status": "Open", "priority": "P1",
             "updated": stale},  # 停滞 → 高紧迫度
            {"key": "TV-2", "summary": "正常问题", "status": "In Progress", "priority": "P3",
             "updated": (datetime.now() + timedelta(days=-1)).strftime("%Y-%m-%dT%H:%M:%S")},
        ])

        async def fake_structured(task_type, messages, schema=None, **kwargs):
            return {
                "risk_items": [{
                    "issue_key": "TV-1", "is_overdue": True,
                    "reason": "P1 高优先级且停滞 10 天，存在交付风险",
                    "suggestion": "推动经办人更新状态并协调资源", "owner": "张三",
                }],
                "summary": "1 个高紧迫度问题需关注",
            }

        monkeypatch.setattr(router_module.llm_router, "route_and_complete_structured", fake_structured)

        result = await risk_analyze({"focus_set_id": fs.id}, {"_user_id": 1})
        assert result["success"] is True
        data = result["data"]
        assert data["risk_items"][0]["issue_key"] == "TV-1"
        assert data["risk_items"][0]["is_overdue"] is True
        assert "summary" in data

    @pytest.mark.asyncio
    async def test_no_high_urgency_members(self, async_client):
        """集合无高紧迫度成员（无停滞无 P0/P1）→ 无可分析。"""
        from datetime import datetime, timedelta
        from app.services.focus_set_service import create_or_update_focus_set, refresh_focus_set_members

        fs = await create_or_update_focus_set(1, "filter", "888", "正常集合", jql="project = TV")
        await refresh_focus_set_members(fs.id, [
            {"key": "TV-9", "summary": "x", "status": "Open", "priority": "P3",
             "updated": (datetime.now() + timedelta(days=-1)).strftime("%Y-%m-%dT%H:%M:%S")},
        ])

        result = await risk_analyze({"focus_set_id": fs.id}, {"_user_id": 1})
        assert result["success"] is False
        assert "没有可分析" in result["error"]

    @pytest.mark.asyncio
    async def test_missing_user(self):
        """缺用户 → 错误。"""
        result = await risk_analyze({}, {})
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_llm_failure_returns_error(self, async_client, monkeypatch):
        """LLM 调用失败 → 返回可读错误。"""
        from datetime import datetime, timedelta
        from app.services.focus_set_service import create_or_update_focus_set, refresh_focus_set_members
        from app.services.llm import router as router_module

        fs = await create_or_update_focus_set(1, "filter", "777", "集合", jql="project = TV")
        stale = (datetime.now() + timedelta(days=-10)).strftime("%Y-%m-%dT%H:%M:%S")
        await refresh_focus_set_members(fs.id, [
            {"key": "TV-1", "summary": "x", "status": "Open", "priority": "P0", "updated": stale},
        ])

        async def fake_fail(task_type, messages, schema=None, **kwargs):
            raise RuntimeError("LLM API error")

        monkeypatch.setattr(router_module.llm_router, "route_and_complete_structured", fake_fail)

        result = await risk_analyze({"focus_set_id": fs.id}, {"_user_id": 1})
        assert result["success"] is False
        assert "风险分析失败" in result["error"]
