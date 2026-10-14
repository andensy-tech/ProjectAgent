"""
API integration tests for memory maintenance endpoints (app/api/memory.py).

  GET  /api/memory/maintenance-status    — undistilled logs count (real DB)
  POST /api/memory/distill               — run maintenance (LLM stages mocked)
  POST /api/memory/embeddings/backfill   — compute embeddings (LLM mocked)
"""

import pytest


class TestMemoryStatus:
    """GET /api/memory/maintenance-status."""

    @pytest.mark.asyncio
    async def test_maintenance_status(self, async_client):
        """维护状态可查询（真实 DB，无网络依赖）。"""
        resp = await async_client.get("/api/memory/maintenance-status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "data" in data


class TestMemoryDistill:
    """POST /api/memory/distill."""

    @pytest.mark.asyncio
    async def test_distill(self, async_client, monkeypatch):
        """手动触发记忆维护（LLM distillation 阶段 mock 掉）。"""
        async def fake_run_maintenance(self, db=None, user_id=None, days_back=7, log_retention_days=30):
            return {"distilled": 0, "cleaned": 0}

        monkeypatch.setattr(
            "app.services.memory.system.MemorySystem.run_maintenance",
            fake_run_maintenance,
        )

        resp = await async_client.post("/api/memory/distill")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    @pytest.mark.asyncio
    async def test_distill_with_params(self, async_client, monkeypatch):
        """days_back 参数透传（复用测试 DB，走真实端点）。"""
        captured = {}

        async def fake_run_maintenance(self, db=None, user_id=None, days_back=7, log_retention_days=30):
            captured["days_back"] = days_back
            return {"distilled": 1, "cleaned": 0}

        monkeypatch.setattr(
            "app.services.memory.system.MemorySystem.run_maintenance",
            fake_run_maintenance,
        )

        resp = await async_client.post("/api/memory/distill", params={"days_back": 3})
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert captured["days_back"] == 3


class TestEmbeddingsBackfill:
    """POST /api/memory/embeddings/backfill."""

    @pytest.mark.asyncio
    async def test_backfill(self, async_client, monkeypatch):
        """嵌入回填（embedding 计算 mock 掉）。"""
        async def fake_compute():
            return {"computed": 0, "failed": 0}

        monkeypatch.setattr(
            "app.services.memory.search_engine.compute_all_embeddings",
            fake_compute,
        )

        resp = await async_client.post("/api/memory/embeddings/backfill")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    @pytest.mark.asyncio
    async def test_backfill_failure(self, async_client, monkeypatch):
        """嵌入计算异常时返回 500 错误。"""
        async def fake_compute():
            raise RuntimeError("embedding service down")

        monkeypatch.setattr(
            "app.services.memory.search_engine.compute_all_embeddings",
            fake_compute,
        )

        resp = await async_client.post("/api/memory/embeddings/backfill")
        assert resp.status_code == 500
