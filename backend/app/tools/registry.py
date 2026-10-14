"""
Tool Registry — manages built-in document processing tools.

Tools are async functions registered in-process alongside
Skills (subprocess scripts) and MCP clients (HTTP).
"""

import logging
from typing import Dict, List, Optional, Any, Callable, Awaitable
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ToolDef:
    """Definition of a built-in tool."""
    name: str
    display_name: str
    description: str
    handler: Callable[[Dict[str, Any], Dict[str, Any]], Awaitable[Dict[str, Any]]]
    input_types: List[str] = field(default_factory=list)
    output_types: List[str] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)
    timeout: int = 120


class ToolRegistry:
    """Registry for built-in document processing tools."""

    def __init__(self):
        self._tools: Dict[str, ToolDef] = {}

    def register(self, tool_def: ToolDef) -> None:
        self._tools[tool_def.name] = tool_def
        logger.info(f"Tool registered: {tool_def.name}")

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def get(self, name: str) -> Optional[ToolDef]:
        return self._tools.get(name)

    def list_tools(self) -> List[ToolDef]:
        return list(self._tools.values())

    def get_descriptions(self) -> str:
        return "\n".join(
            f"- {t.name}: {t.description} (inputs: {', '.join(t.input_types)})"
            for t in self._tools.values()
        )

    async def execute(self, name: str, params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        tool = self._tools.get(name)
        if not tool:
            raise ValueError(f"Tool not found: {name}")
        return await tool.handler(params, context)


# Global singleton
tool_registry = ToolRegistry()
