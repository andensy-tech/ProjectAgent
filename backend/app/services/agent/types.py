"""
Shared types for Agent execution pipeline.
Avoids circular imports.
"""

from typing import Dict, List, Any
from dataclasses import dataclass, field


@dataclass
class AgentResponse:
    """Response from Agent to user."""
    type: str  # text | thinking | card | action | error | complete | task_submitted | ask_user | file
    content: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    actions: List[Dict[str, Any]] = field(default_factory=list)
