"""
Custom exception classes for the application.
"""


class ProjectAgentException(Exception):
    """Base exception for Project Agent."""
    
    def __init__(self, message: str, status_code: int = 500, details: dict = None):
        self.message = message
        self.status_code = status_code
        self.details = details or {}
        super().__init__(self.message)


class LLMException(ProjectAgentException):
    """Exception raised when LLM API calls fail."""
    
    def __init__(self, message: str, provider: str = None, details: dict = None):
        self.provider = provider
        super().__init__(message, status_code=503, details=details)


class MCPException(ProjectAgentException):
    """Exception raised when MCP server calls fail."""
    
    def __init__(self, message: str, server: str = None, details: dict = None):
        self.server = server
        super().__init__(message, status_code=502, details=details)


class ToolException(ProjectAgentException):
    """Exception raised when a built-in tool fails."""

    def __init__(self, message: str, tool_name: str = None, details: dict = None):
        self.tool_name = tool_name
        super().__init__(message, status_code=500, details=details)


class SkillException(ProjectAgentException):
    """Exception raised when Skill execution fails."""
    
    def __init__(self, message: str, skill_name: str = None, details: dict = None):
        self.skill_name = skill_name
        super().__init__(message, status_code=500, details=details)


class ValidationException(ProjectAgentException):
    """Exception raised when input validation fails."""
    
    def __init__(self, message: str, details: dict = None):
        super().__init__(message, status_code=400, details=details)


class AuthenticationException(ProjectAgentException):
    """Exception raised when authentication fails."""
    
    def __init__(self, message: str = "Authentication failed"):
        super().__init__(message, status_code=401)


class AuthorizationException(ProjectAgentException):
    """Exception raised when authorization fails."""
    
    def __init__(self, message: str = "Not authorized"):
        super().__init__(message, status_code=403)
