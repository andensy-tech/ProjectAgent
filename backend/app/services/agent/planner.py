"""
Execution Plan 数据类定义。

在单脑架构下，任务规划由 LLM 的 function calling 机制完成。
LLM 自主决定调用什么工具、什么顺序、是否继续。

此模块仅保留数据结构供其他模块引用。
"""

from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum


class ActionType(str, Enum):
    """Types of actions in execution plan."""
    SKILL_CALL = "skill_call"
    TOOL_CALL = "tool_call"
    DB_QUERY = "db_query"
    DB_WRITE = "db_write"
    API_CALL = "api_call"
    LLM_GENERATE = "llm_generate"
    USER_CONFIRM = "user_confirm"
    CONDITIONAL = "conditional"
    # Skill self-evolution actions (kept for backward compatibility)
    SKILL_DESIGN = "skill_design"
    SKILL_GENERATE = "skill_generate"
    SKILL_VALIDATE = "skill_validate"
    SKILL_REGISTER = "skill_register"


@dataclass
class PlanStep:
    """Single step in execution plan."""
    step: int
    action: ActionType
    target: str
    params: Dict[str, Any] = field(default_factory=dict)
    output_var: Optional[str] = None
    requires_confirmation: bool = False
    condition: Optional[str] = None
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "step": self.step,
            "action": self.action.value,
            "target": self.target,
            "params": self.params,
            "output_var": self.output_var,
            "requires_confirmation": self.requires_confirmation,
            "condition": self.condition,
            "description": self.description,
        }


@dataclass
class ExecutionPlan:
    """Complete execution plan."""
    thought: str
    steps: List[PlanStep]
    response: str

    def to_dict(self) -> dict:
        return {
            "thought": self.thought,
            "plan": [step.to_dict() for step in self.steps],
            "response": self.response,
        }

    def get_step(self, step_number: int) -> Optional[PlanStep]:
        for step in self.steps:
            if step.step == step_number:
                return step
        return None
