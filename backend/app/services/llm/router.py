"""
LLM Router - Routes tasks to appropriate LLM (commercial or local).

Strategy:
- Commercial LLM: Semantic understanding, complex reasoning, report generation
- Local LLM: Data parsing, formatting, OCR post-processing, data masking
"""

from typing import Optional, List, Dict, Any
from enum import Enum
from app.services.llm.client import llm_client
from app.core.config import settings


class TaskType(str, Enum):
    """Types of tasks that can be routed to LLMs."""
    # Semantic understanding → Commercial
    INTENT_PARSING = "intent_parsing"
    RISK_ANALYSIS = "risk_analysis"
    REPORT_GENERATION = "report_generation"
    REQUIREMENT_UNDERSTANDING = "requirement_understanding"
    QUESTION_ANSWERING = "question_answering"
    SUMMARIZATION = "summarization"
    
    # Data processing → Local
    DATA_PARSING = "data_parsing"
    TEXT_FORMATTING = "text_formatting"
    STRUCTURE_EXTRACTION = "structure_extraction"
    OCR_POSTPROCESS = "ocr_postprocess"
    DATA_MASKING = "data_masking"
    EMBEDDING = "embedding"


# Default routing rules
DEFAULT_ROUTING = {
    # Semantic tasks → Commercial
    TaskType.INTENT_PARSING: "commercial",
    TaskType.RISK_ANALYSIS: "commercial",
    TaskType.REPORT_GENERATION: "commercial",
    TaskType.REQUIREMENT_UNDERSTANDING: "commercial",
    TaskType.QUESTION_ANSWERING: "commercial",
    TaskType.SUMMARIZATION: "commercial",
    
    # Data tasks → Local
    TaskType.DATA_PARSING: "local",
    TaskType.TEXT_FORMATTING: "local",
    TaskType.STRUCTURE_EXTRACTION: "local",
    TaskType.OCR_POSTPROCESS: "local",
    TaskType.DATA_MASKING: "local",
    TaskType.EMBEDDING: "local",
}


class LLMRouter:
    """Routes LLM requests based on task type and configuration."""
    
    def __init__(self):
        self.routing_rules = DEFAULT_ROUTING.copy()
        # Override with custom config if available
        custom_routing = getattr(settings, 'LLM_ROUTING', {})
        for task, provider in custom_routing.items():
            try:
                self.routing_rules[TaskType(task)] = provider
            except ValueError:
                pass  # Ignore invalid task types
    
    def get_provider(self, task_type: TaskType,
                     default_provider: Optional[str] = None) -> str:
        """
        Get the LLM provider for a given task type.

        Args:
            task_type: The type of task
            default_provider: Override provider ("commercial" or "local").
                              When set, all task types use this provider.

        Returns:
            "commercial" or "local"
        """
        if default_provider is not None:
            return default_provider
        return self.routing_rules.get(task_type, "commercial")
    
    async def route_and_complete(
        self,
        task_type: TaskType,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        stream: bool = False,
        json_mode: bool = False,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        default_provider: Optional[str] = None,
    ) -> str:
        """
        Route task to appropriate LLM and complete.

        Args:
            task_type: Type of task for routing
            messages: List of message dicts
            model: Specific model override
            temperature: Sampling temperature
            max_tokens: Maximum tokens
            stream: Whether to stream
            json_mode: Whether to request JSON
            api_key: Per-user API key override
            api_base: Per-user API base URL override
            default_provider: Override TaskType routing ("commercial" or "local")

        Returns:
            Generated text response
        """
        provider = self.get_provider(task_type, default_provider=default_provider)
        return await llm_client.complete(
            messages=messages,
            model_type=provider,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=stream,
            json_mode=json_mode,
            api_key=api_key,
            api_base=api_base,
        )

    async def route_and_complete_with_tools(
        self,
        task_type: TaskType,
        messages: List[Dict[str, str]],
        tools: List[Dict],
        tool_choice: Optional[str] = None,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        default_provider: Optional[str] = None,
    ) -> tuple[Optional[str], Optional[List[Dict]]]:
        """
        Route task to appropriate LLM with tool calling support.

        Args:
            task_type: Type of task for routing
            messages: List of message dicts
            tools: List of tool definitions in OpenAI function calling format
            tool_choice: Tool selection strategy
            temperature: Sampling temperature
            max_tokens: Maximum tokens
            api_key: Per-user API key override
            api_base: Per-user API base URL override
            default_provider: Override TaskType routing ("commercial" or "local")

        Returns:
            Tuple of (content, tool_calls)
        """
        provider = self.get_provider(task_type, default_provider=default_provider)
        return await llm_client.complete_with_tools(
            messages=messages,
            tools=tools,
            model_type=provider,
            tool_choice=tool_choice,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            api_key=api_key,
            api_base=api_base,
        )

    async def route_and_complete_structured(
        self,
        task_type: TaskType,
        messages: List[Dict[str, str]],
        schema: Optional[Dict[str, Any]] = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        default_provider: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Route task to appropriate LLM and get structured response.

        Args:
            task_type: Type of task for routing
            messages: List of message dicts
            schema: JSON schema for validation
            model: Specific model override
            api_key: Per-user API key override
            api_base: Per-user API base URL override
            default_provider: Override TaskType routing ("commercial" or "local")

        Returns:
            Parsed JSON response
        """
        provider = self.get_provider(task_type, default_provider=default_provider)
        return await llm_client.complete_structured(
            messages=messages,
            model_type=provider,
            model=model,
            schema=schema,
            api_key=api_key,
            api_base=api_base,
        )
    
    def get_routing_info(self) -> Dict[str, str]:
        """Get current routing configuration."""
        return {
            task.value: provider
            for task, provider in self.routing_rules.items()
        }


# Global router instance
llm_router = LLMRouter()
