"""
Tests for app.core.exceptions module.

Test Cases:
- EXC-01: LLMException creation
- EXC-02: MCPException creation
- EXC-03: SkillException creation
- EXC-04: ValidationException creation
- EXC-05: AuthenticationException creation
"""

import pytest

from app.core.exceptions import (
    ProjectAgentException,
    LLMException,
    MCPException,
    SkillException,
    ValidationException,
    AuthenticationException,
    AuthorizationException,
)


class TestProjectAgentException:
    """Test base exception class."""

    def test_base_exception(self):
        """Base exception stores message and status code."""
        exc = ProjectAgentException("Something went wrong", status_code=500)

        assert str(exc) == "Something went wrong"
        assert exc.status_code == 500
        assert exc.details == {}

    def test_base_exception_with_details(self):
        """Base exception stores details dict."""
        exc = ProjectAgentException("Error", status_code=400, details={"field": "invalid"})

        assert exc.details == {"field": "invalid"}


class TestLLMException:
    """Test LLMException."""

    def test_llm_exception(self):
        """EXC-01: LLMException has status_code=503 and provider."""
        exc = LLMException("LLM timeout", provider="deepseek")

        assert exc.status_code == 503
        assert exc.provider == "deepseek"
        assert "LLM timeout" in str(exc)
        assert exc.details == {}

    def test_llm_exception_with_details(self):
        """EXC-01: LLMException stores details."""
        exc = LLMException(
            "LLM error",
            provider="deepseek",
            details={"model": "deepseek-chat"},
        )

        assert exc.details == {"model": "deepseek-chat"}


class TestMCPException:
    """Test MCPException."""

    def test_mcp_exception(self):
        """EXC-02: MCPException has status_code=502 and server."""
        exc = MCPException("Connection failed", server="jira")

        assert exc.status_code == 502
        assert exc.server == "jira"
        assert "Connection failed" in str(exc)

    def test_mcp_exception_with_details(self):
        """EXC-02: MCPException stores error details."""
        exc = MCPException("Error", server="confluence", details={"code": 404})

        assert exc.details == {"code": 404}


class TestSkillException:
    """Test SkillException."""

    def test_skill_exception(self):
        """EXC-03: SkillException has status_code=500 and skill_name."""
        exc = SkillException("Skill not found", skill_name="jira-report")

        assert exc.status_code == 500
        assert exc.skill_name == "jira-report"
        assert "Skill not found" in str(exc)


class TestValidationException:
    """Test ValidationException."""

    def test_validation_exception(self):
        """EXC-04: ValidationException has status_code=400."""
        exc = ValidationException("Invalid input", details={"field": "required"})

        assert exc.status_code == 400
        assert exc.details == {"field": "required"}

    def test_validation_exception_default_message(self):
        """EXC-04: ValidationException with default message."""
        exc = ValidationException("Bad request")

        assert exc.status_code == 400
        assert str(exc) == "Bad request"


class TestAuthenticationException:
    """Test AuthenticationException."""

    def test_auth_exception(self):
        """EXC-05: AuthenticationException has status_code=401."""
        exc = AuthenticationException()

        assert exc.status_code == 401
        assert "Authentication failed" in str(exc)

    def test_auth_exception_custom_message(self):
        """EXC-05: AuthenticationException with custom message."""
        exc = AuthenticationException("Token expired")

        assert "Token expired" in str(exc)


class TestAuthorizationException:
    """Test AuthorizationException."""

    def test_authz_exception(self):
        """AuthorizationException has status_code=403."""
        exc = AuthorizationException()

        assert exc.status_code == 403
        assert "Not authorized" in str(exc)
