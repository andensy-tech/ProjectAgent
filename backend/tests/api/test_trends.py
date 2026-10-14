"""
API integration tests for Trends endpoints (app/api/trends.py).

  GET /api/trends          — time-series points
  GET /api/trends/summary  — aggregated trend direction

Trend data comes from metric_snapshot rows; with an empty snapshot table the
endpoints return empty-but-valid structures through the real trend_service.
"""

import pytest


class TestTrendsList:
    """GET /api/trends."""

    @pytest.mark.asyncio
    async def test_list_empty(self, async_client):
        """无快照数据时返回空 points 结构。"""
        resp = await async_client.get("/api/trends")
        assert resp.status_code == 200
        data = resp.json()
        assert "points" in data
        assert data["points"] == []

    @pytest.mark.asyncio
    async def test_list_with_project_param(self, async_client):
        """带 project 过滤参数仍返回结构。"""
        resp = await async_client.get("/api/trends", params={"project": "AML123", "days": 7})
        assert resp.status_code == 200
        assert resp.json()["project"] == "AML123"


class TestTrendsSummary:
    """GET /api/trends/summary."""

    @pytest.mark.asyncio
    async def test_summary_empty(self, async_client):
        """空数据时 summary 返回有效 dict。"""
        resp = await async_client.get("/api/trends/summary")
        assert resp.status_code == 200
        assert isinstance(resp.json(), dict)

    @pytest.mark.asyncio
    async def test_summary_with_project(self, async_client):
        """带 project 过滤的 summary 仍返回有效结构。"""
        resp = await async_client.get("/api/trends/summary", params={"project": "AML123", "days": 7})
        assert resp.status_code == 200
        assert isinstance(resp.json(), dict)

    @pytest.mark.asyncio
    async def test_list_with_days_param(self, async_client):
        """days 参数透传到响应。"""
        resp = await async_client.get("/api/trends", params={"days": 14})
        assert resp.status_code == 200
        assert resp.json()["days"] == 14
