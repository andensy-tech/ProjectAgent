"""
API integration tests for Skills endpoints.

Test Cases:
- API-S-01: GET /api/skills returns list of skills
- API-S-02: GET /api/skills/{skill}/config returns config
- API-S-03: GET /api/skills/{skill} 404 for unknown skill
- API-S-04: POST /api/skills/{skill}/execute returns execution result
- API-S-05: POST /api/skills/{skill}/execute 404 for unknown skill
- API-S-06: Skills endpoints require auth
"""

import pytest


class TestListSkills:
    """Test GET /api/skills (API-S-01)."""

    def test_list_skills(self, test_client, monkeypatch):
        """API-S-01: Returns list of registered skills."""
        from app.services.agent.executor import action_executor
        # Ensure skills are registered for this test
        action_executor.register_skill("jira-confluence-report", lambda p, c: {})
        action_executor.register_skill("jira-cl-merge-monitor", lambda p, c: {})

        response = test_client.get("/api/skills")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        # At minimum, the two built-in skills should be registered
        skill_names = [s["name"] for s in data]
        assert "jira-confluence-report" in skill_names
        assert "jira-cl-merge-monitor" in skill_names

        for skill in data:
            assert "name" in skill
            assert "display_name" in skill
            assert "description" in skill
            assert "version" in skill
            assert "triggers" in skill


class TestGetSkillConfig:
    """Test GET /api/skills/{skill_name}/config (API-S-02~03)."""

    def test_get_skill_config_exists(self, test_client, monkeypatch):
        """API-S-02: Returns config for existing skill."""
        from app.services.agent.executor import action_executor
        action_executor.register_skill("jira-confluence-report", lambda p, c: {})

        response = test_client.get("/api/skills/jira-confluence-report/config")

        assert response.status_code == 200
        data = response.json()
        assert data["skill_name"] == "jira-confluence-report"
        # Real payload shape: default_config + user_override merged
        assert "default_config" in data
        assert "user_override" in data
        assert "merged_config" in data

    def test_get_skill_config_not_found(self, test_client):
        """API-S-03: 404 for unknown skill."""
        response = test_client.get("/api/skills/nonexistent-skill/config")

        assert response.status_code == 404
        assert "detail" in response.json()


class TestExecuteSkill:
    """Test POST /api/skills/{skill_name}/execute (API-S-04~05)."""

    @pytest.mark.asyncio
    async def test_execute_skill(self, async_client, mock_mcp_manager, monkeypatch):
        """API-S-04: Execute skill returns result."""
        from app.services.agent.executor import action_executor
        action_executor.register_skill("jira-confluence-report", lambda p, c: {"type": "report"})

        response = await async_client.post(
            "/api/skills/jira-confluence-report/execute",
            json={"params": {"config_name": "default"}},
        )

        assert response.status_code == 200
        data = response.json()
        assert "execution_id" in data
        assert data["status"] in ("completed", "failed")
        assert "result" in data

    def test_execute_skill_not_found(self, test_client):
        """API-S-05: 404 for unknown skill."""
        response = test_client.post(
            "/api/skills/nonexistent-skill/execute",
            json={"params": {}},
        )

        assert response.status_code == 404
        assert "detail" in response.json()


class TestSkillsAuthRequired:
    """Test auth requirement for skills endpoints (API-S-06)."""

    def test_skills_requires_auth(self, test_client_unauthenticated, monkeypatch):
        """API-S-06: Skills endpoints reject unauthenticated requests (DEBUG=False)."""
        from app.core.config import settings
        monkeypatch.setattr(settings, "DEBUG", False)

        client = test_client_unauthenticated

        response = client.get("/api/skills")
        assert response.status_code == 401

        response = client.get("/api/skills/jira-confluence-report/config")
        assert response.status_code == 401

        response = client.post(
            "/api/skills/jira-confluence-report/execute",
            json={"params": {}},
        )
        assert response.status_code == 401
