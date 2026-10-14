"""
Intent 数据类定义。

在单脑架构下，意图理解由 LLM 在一个推理过程中完成。
此模块仅保留 Intent/Entity 数据结构供其他模块引用。
"""

from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field


@dataclass
class Intent:
    """Structured representation of user intent.

    In the single-brain architecture, this is constructed from
    the LLM's function calling decisions rather than by a
    separate intent classification module.
    """
    intent_category: str = "other"   # query | execute | record | generate | analyze | investigate | greeting | other
    action: str = ""
    entities: Dict[str, Any] = field(default_factory=dict)
    target_skill: Optional[str] = None
    target_tool: Optional[str] = None
    confidence: float = 0.0
    clarification_needed: bool = False
    clarification_question: str = ""
    raw_message: str = ""
    capability_gap: bool = False
    needs_new_skill: bool = False


@dataclass
class Entity:
    """Extracted entity from user message."""
    type: str
    value: Any
    confidence: float = 1.0
