"""
API integration tests for Health endpoint.

Test Cases:
- API-H-01: GET /health returns status and component availability
- API-H-02: Health endpoint does not require auth
"""

import pytest


class TestHealthCheck:
    """Test GET /health (API-H-01~02)."""

    def test_health_status(self, test_client):
        """API-H-01: Returns healthy status with component info."""
        response = test_client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "llm_commercial" in data
        assert "llm_local" in data
        assert "mcp_servers" in data
        assert isinstance(data["llm_commercial"], bool)
        assert isinstance(data["llm_local"], bool)
        assert isinstance(data["mcp_servers"], list)

    def test_health_no_auth_required(self, test_client_unauthenticated):
        """API-H-02: Health endpoint accessible without auth."""
        response = test_client_unauthenticated.get("/health")

        assert response.status_code == 200
        assert response.json()["status"] == "healthy"
