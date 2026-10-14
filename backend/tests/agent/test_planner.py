"""
Tests for app.services.agent.planner module (simplified — data structures only).

In the single-brain architecture, task planning is handled by the LLM's
function calling mechanism. This module only provides the data structures.

Test Cases:
- PLN-01: ActionType enum values
- PLN-02: PlanStep creation
- PLN-03: ExecutionPlan creation
- PLN-04: PlanStep to_dict
- PLN-05: ExecutionPlan to_dict
- PLN-06: get_step lookup
"""

from app.services.agent.planner import ActionType, PlanStep, ExecutionPlan


class TestActionType:
    """Test the ActionType enum."""

    def test_core_values(self):
        """PLN-01: Core action types exist."""
        assert ActionType.SKILL_CALL == "skill_call"
        assert ActionType.TOOL_CALL == "tool_call"
        assert ActionType.LLM_GENERATE == "llm_generate"
        assert ActionType.DB_QUERY == "db_query"


class TestPlanStep:
    """Test the PlanStep data class."""

    def test_create_step(self):
        """PLN-02: Create a plan step."""
        step = PlanStep(
            step=1,
            action=ActionType.SKILL_CALL,
            target="jira-confluence-report",
            params={"config_name": "default"},
            description="Run jira report",
        )
        assert step.step == 1
        assert step.action == ActionType.SKILL_CALL
        assert step.target == "jira-confluence-report"

    def test_to_dict(self):
        """PLN-04: Step serialization."""
        step = PlanStep(
            step=1,
            action=ActionType.LLM_GENERATE,
            target="chat",
            params={"query": "hello"},
            output_var="response",
        )
        d = step.to_dict()
        assert d["step"] == 1
        assert d["action"] == "llm_generate"
        assert d["target"] == "chat"
        assert d["output_var"] == "response"


class TestExecutionPlan:
    """Test the ExecutionPlan data class."""

    def test_create_plan(self):
        """PLN-03: Create an execution plan."""
        step = PlanStep(step=1, action=ActionType.LLM_GENERATE, target="chat", params={})
        plan = ExecutionPlan(
            thought="simple reply",
            steps=[step],
            response="Hello!",
        )
        assert plan.thought == "simple reply"
        assert len(plan.steps) == 1
        assert plan.response == "Hello!"

    def test_to_dict(self):
        """PLN-05: Plan serialization."""
        step = PlanStep(step=1, action=ActionType.TOOL_CALL, target="read_pdf", params={})
        plan = ExecutionPlan(thought="read doc", steps=[step], response="reading...")
        d = plan.to_dict()
        assert len(d["plan"]) == 1
        assert d["plan"][0]["target"] == "read_pdf"

    def test_get_step(self):
        """PLN-06: Lookup step by number."""
        step = PlanStep(step=1, action=ActionType.LLM_GENERATE, target="chat", params={})
        plan = ExecutionPlan(thought="", steps=[step], response="")
        found = plan.get_step(1)
        assert found is not None
        assert found.target == "chat"
        assert plan.get_step(99) is None
