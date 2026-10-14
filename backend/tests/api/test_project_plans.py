"""
API integration tests for 项目计划知识库（P-D：monitoring_optimization）。

  GET  /api/project-plans                 — 列表
  GET  /api/project-plans/{project_name}  — 详情（含阶段）
  POST /api/project-plans                 — 保存（upsert，阶段替换）
"""

import pytest


class TestProjectPlans:
    """项目计划 CRUD。"""

    @pytest.mark.asyncio
    async def test_save_and_get(self, async_client):
        """保存计划（含阶段）→ 详情可查。"""
        payload = {
            "project_name": "AML123",
            "overall_status": "phase3/5",
            "phases": [
                {"phase_name": "方案评审", "target_date": "2026-08-15", "status": "done", "deliverables": ["方案文档"]},
                {"phase_name": "第五轮测试", "target_date": "2026-09-20", "status": "in_progress"},
                {"phase_name": "客户验收", "target_date": "2026-10-30", "status": "planned"},
            ],
        }
        resp = await async_client.post("/api/project-plans", json=payload)
        assert resp.status_code == 200
        assert resp.json()["project_name"] == "AML123"

        detail = await async_client.get("/api/project-plans/AML123")
        assert detail.status_code == 200
        data = detail.json()
        assert data["overall_status"] == "phase3/5"
        assert len(data["phases"]) == 3
        assert data["phases"][0]["phase_name"] == "方案评审"
        assert data["phases"][0]["target_date"] == "2026-08-15"

    @pytest.mark.asyncio
    async def test_upsert_replaces_phases(self, async_client):
        """同项目重复保存 → 更新主表 + 替换阶段（不重复累积）。"""
        base = {"project_name": "AML123", "phases": [{"phase_name": "阶段A", "target_date": "2026-08-01"}]}
        await async_client.post("/api/project-plans", json=base)

        updated = {"project_name": "AML123", "overall_status": "phase4/5",
                   "phases": [{"phase_name": "阶段B", "target_date": "2026-08-20"}]}
        await async_client.post("/api/project-plans", json=updated)

        detail = (await async_client.get("/api/project-plans/AML123")).json()
        assert detail["overall_status"] == "phase4/5"
        assert len(detail["phases"]) == 1  # 阶段被替换
        assert detail["phases"][0]["phase_name"] == "阶段B"

    @pytest.mark.asyncio
    async def test_get_not_found(self, async_client):
        """不存在的项目计划 → 404。"""
        resp = await async_client.get("/api/project-plans/NONEXISTENT")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_list_plans(self, async_client):
        """保存后列表可见。"""
        await async_client.post("/api/project-plans", json={"project_name": "AML456", "phases": []})
        resp = await async_client.get("/api/project-plans")
        assert resp.status_code == 200
        assert any(p["project_name"] == "AML456" for p in resp.json())

    @pytest.mark.asyncio
    async def test_invalid_target_date_skipped(self, async_client):
        """非法 target_date 被忽略（阶段仍保存）。"""
        payload = {"project_name": "AML789", "phases": [
            {"phase_name": "阶段X", "target_date": "bad-date"},
        ]}
        resp = await async_client.post("/api/project-plans", json=payload)
        assert resp.status_code == 200
        detail = (await async_client.get("/api/project-plans/AML789")).json()
        assert detail["phases"][0]["target_date"] is None
