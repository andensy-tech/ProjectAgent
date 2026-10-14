"""
judge_progress 工具测试（P-E：实时判定，结合项目计划 + 关注集合）。

验证：预置 plan + focus_set → 工具返回进展摘要/下个里程碑/关注集合状态。
"""

from datetime import date, timedelta

import pytest

from app.tools.progress_tool import judge_progress


class TestJudgeProgress:
    """项目进展判定。"""

    @pytest.mark.asyncio
    async def test_with_plan_and_focus(self, async_client):
        """预置计划 + 关注集合 → 判定返回结构化进展。"""
        from app.services.focus_set_service import create_or_update_focus_set
        from app.services.project_plan_service import save_project_plan

        future = (date.today() + timedelta(days=30)).isoformat()
        await save_project_plan(1, "AML123", [
            {"phase_name": "方案评审", "target_date": (date.today() - timedelta(days=10)).isoformat(), "status": "done"},
            {"phase_name": "第五轮测试", "target_date": future, "status": "in_progress"},
        ], overall_status="phase3/5")
        await create_or_update_focus_set(
            1, "filter", "60513", "本周必清 Bug", jql="project = TV",
            deadline=date.today() + timedelta(days=3), metric_type="fix_rate", metric_target=0.8,
        )

        result = await judge_progress({"project_name": "AML123"}, {"_user_id": 1})
        assert result["success"] is True
        data = result["data"]
        assert data["project"] == "AML123"
        assert data["overall_status"] == "phase3/5"
        # 下个里程碑 = 最近的未完成阶段（第五轮测试）
        assert data["next_milestone"]["phase_name"] == "第五轮测试"
        assert data["next_milestone"]["target_date"] == future
        # 关注集合可见
        assert any(f["identifier"] == "60513" for f in data["focus_sets"])
        # 摘要含关键信息
        assert "第五轮测试" in data["summary"]
        assert "本周必清 Bug" in data["summary"]

    @pytest.mark.asyncio
    async def test_empty_returns_summary(self, async_client):
        """无计划/关注 → 返回空摘要（不报错）。"""
        result = await judge_progress({}, {"_user_id": 1})
        assert result["success"] is True
        assert result["data"]["plan"] is None
        assert result["data"]["focus_sets"] == []
        assert "暂无" in result["data"]["summary"]

    @pytest.mark.asyncio
    async def test_missing_user(self):
        """缺用户 → 错误。"""
        result = await judge_progress({}, {})
        assert result["success"] is False
