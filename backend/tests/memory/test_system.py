"""
Tests for app.services.memory.system module.

Test Cases:
- MEM-01~03: System memory (skills, tools)
- MEM-04~06: Working memory (messages, entities, context)
- MEM-07~09: Session memory (project, cache, preferences)
- MEM-10~12: Long-term memory (profile, learning)
- MEM-13: Complete context aggregation
- MEM-14~16: _build_memory_vars pattern classification
"""

import pytest
from datetime import datetime
from typing import Dict, Any

from app.services.memory.system import (
    SystemMemory,
    WorkingMemory,
    SessionMemory,
    LongTermMemory,
    MemorySystem,
)
from app.agent_context import _build_memory_vars


class TestSystemMemory:
    """Test SystemMemory (Layer 1) (MEM-01~03)."""

    def test_add_skill(self):
        """MEM-01: Skills are registered correctly."""
        mem = SystemMemory()
        mem.add_skill("test-skill", "A test skill", ["param1", "param2"])

        assert "test-skill" in mem.skills
        assert mem.skills["test-skill"]["description"] == "A test skill"
        assert mem.skills["test-skill"]["params"] == ["param1", "param2"]

    def test_add_tool(self):
        """MEM-02: Tools are registered correctly."""
        mem = SystemMemory()
        mem.add_tool("jira", "search_issues", "Search Jira issues")

        assert "jira/search_issues" in mem.tools
        assert mem.tools["jira/search_issues"]["description"] == "Search Jira issues"

    def test_get_skill_descriptions(self):
        """MEM-03: Skill descriptions formatted correctly."""
        mem = SystemMemory()
        mem.add_skill("skill-a", "Description A")
        mem.add_skill("skill-b", "Description B")

        desc = mem.get_skill_descriptions()
        assert "skill-a: Description A" in desc
        assert "skill-b: Description B" in desc

    def test_get_tool_descriptions(self):
        """Tool descriptions formatted correctly."""
        mem = SystemMemory()
        mem.add_tool("server", "tool1", "Does something")

        desc = mem.get_tool_descriptions()
        assert "server/tool1: Does something" in desc


class TestWorkingMemory:
    """Test WorkingMemory (Layer 2) (MEM-04~06)."""

    def test_add_message(self):
        """MEM-04: Messages stored in order."""
        mem = WorkingMemory("conv-1")
        mem.add_message("user", "Hello")
        mem.add_message("assistant", "Hi there")

        assert len(mem.messages) == 2
        assert mem.messages[0]["role"] == "user"
        assert mem.messages[1]["role"] == "assistant"

    def test_message_truncation(self):
        """MEM-04: Older messages are compacted once over max + 2."""
        mem = WorkingMemory("conv-1")
        mem.max_messages = 3
        # Compaction triggers when len > max_messages + 2 (i.e. > 5)
        for i in range(1, 7):
            mem.add_message("user", str(i))

        assert len(mem.messages) == 3  # keeps the most recent max_messages
        assert mem.messages[0]["content"] == "4"
        assert mem.messages[-1]["content"] == "6"
        # Overflowed messages are compacted into summaries, not silently dropped
        assert len(mem.compacted_summaries) >= 1

    def test_add_entity(self):
        """MEM-05: Entities tracked by type."""
        mem = WorkingMemory("conv-1")
        mem.add_entity("project", "AML123")
        mem.add_entity("project", "AML456")

        assert len(mem.entities["project"]) == 2
        assert mem.entities["project"][0]["value"] == "AML123"

    def test_context_variables(self):
        """MEM-06: Context variables set and get."""
        mem = WorkingMemory("conv-1")
        mem.set_context_var("active_skill", "jira-report")

        assert mem.context_vars["active_skill"] == "jira-report"

    def test_get_recent_messages(self):
        """Recent messages returned correctly."""
        mem = WorkingMemory("conv-1")
        for i in range(15):
            mem.add_message("user", str(i))

        recent = mem.get_recent_messages(5)
        assert len(recent) == 5
        assert recent[-1]["content"] == "14"

    def test_intent_stack(self):
        """Intent stack push/pop works."""
        mem = WorkingMemory("conv-1")
        mem.push_intent("query")
        mem.push_intent("execute")

        assert mem.pop_intent() == "execute"
        assert mem.pop_intent() == "query"
        assert mem.pop_intent() is None


class TestSessionMemory:
    """Test SessionMemory (Layer 3) (MEM-07~09)."""

    def test_set_active_project(self):
        """MEM-07: Active project set correctly."""
        mem = SessionMemory("session-1", "user-1")
        mem.set_active_project("AML123")

        assert mem.active_project == "AML123"

    def test_cache_skill_result(self):
        """MEM-08: Skill results cached and retrieved."""
        mem = SessionMemory("session-1", "user-1")
        mem.cache_skill_result("jira-report", {"count": 5})

        result = mem.get_cached_result("jira-report")
        assert result == {"count": 5}

    def test_cache_miss(self):
        """Missing cache returns None."""
        mem = SessionMemory("session-1", "user-1")
        assert mem.get_cached_result("nonexistent") is None

    def test_set_preference(self):
        """MEM-09: Preferences stored correctly."""
        mem = SessionMemory("session-1", "user-1")
        mem.set_preference("theme", "dark")

        assert mem.preferences["theme"] == "dark"

    def test_to_dict(self):
        """Session serialization includes key info."""
        mem = SessionMemory("session-1", "user-1")
        mem.set_active_project("AML123")

        data = mem.to_dict()
        assert data["session_id"] == "session-1"
        assert data["active_project"] == "AML123"
        assert "preferences" in data


class TestLongTermMemory:
    """Test LongTermMemory (Layer 4) (MEM-10~12) — DB-backed."""

    @pytest.mark.asyncio
    async def test_update_user_profile_new(self, db_session):
        """MEM-10: New user profile created with defaults."""
        mem = LongTermMemory()
        await mem.update_user_profile(db_session, 1, {"preferred_projects": "AML123"})

        profile = await mem.get_user_profile(db_session, 1)
        assert profile is not None
        assert "AML123" in profile["preferred_projects"]
        assert profile["communication_style"] == "professional"

    @pytest.mark.asyncio
    async def test_update_user_profile_existing(self, db_session):
        """MEM-11: Existing profile updated (append semantics)."""
        mem = LongTermMemory()
        await mem.update_user_profile(db_session, 1, {"preferred_projects": "AML123"})
        await mem.update_user_profile(db_session, 1, {"preferred_projects": "AML456"})

        profile = await mem.get_user_profile(db_session, 1)
        assert "AML123" in profile["preferred_projects"]
        assert "AML456" in profile["preferred_projects"]

    @pytest.mark.asyncio
    async def test_add_learning_record(self, db_session):
        """MEM-12: Learning records stored with context."""
        mem = LongTermMemory()
        await mem.add_learning_record(db_session, 1, "prefers_morning_reports", "from_chat")

        records = await mem.get_learning_records(db_session, 1)
        assert len(records) == 1
        assert records[0]["pattern"] == "prefers_morning_reports"

    @pytest.mark.asyncio
    async def test_get_learning_records_empty(self, db_session):
        """No records returns empty list."""
        mem = LongTermMemory()
        assert await mem.get_learning_records(db_session, 999) == []


class TestMemorySystem:
    """Test MemorySystem integration (MEM-13)."""

    @pytest.fixture
    def memory(self):
        return MemorySystem()

    def test_get_working_memory(self, memory):
        """Working memory created on demand."""
        wm = memory.get_working_memory("conv-1")
        assert wm.conversation_id == "conv-1"

    def test_get_working_memory_cached(self, memory):
        """Same conversation returns same working memory."""
        wm1 = memory.get_working_memory("conv-1")
        wm2 = memory.get_working_memory("conv-1")
        assert wm1 is wm2

    def test_get_session_memory(self, memory):
        """Session memory created on demand."""
        sm = memory.get_session_memory("session-1", "user-1")
        assert sm.session_id == "session-1"
        assert sm.user_id == "user-1"

    @pytest.mark.asyncio
    async def test_get_context(self, memory, db_session):
        """MEM-13: Complete context aggregates all layers."""
        # Setup
        memory.system_memory.add_skill("test-skill", "Test")
        wm = memory.get_working_memory("conv-1", user_id=1)
        wm.add_message("user", "Hello")
        wm.set_context_var("key", "value")
        sm = memory.get_session_memory("session-1", user_id=1)
        sm.set_active_project("AML123")
        await memory.long_term_memory.update_user_profile(db_session, 1, {"preferred_projects": "AML123"})

        context = await memory.get_context(
            db=db_session,
            conversation_id="conv-1",
            user_id=1,
            session_id="session-1",
        )

        assert "available_skills" in context
        assert "recent_messages" in context
        assert "conversation_context" in context
        assert context["active_project"] == "AML123"
        assert "user_profile" in context

    @pytest.mark.asyncio
    async def test_update_from_execution(self, memory, db_session):
        """Memory updated after execution."""
        from app.services.agent.intent import Intent

        intent = Intent(
            intent_category="execute",
            action="summarize",
            target_skill="jira-confluence-report",
            entities={"project": "AML123"},
        )

        await memory.update_from_execution(
            db=db_session,
            conversation_id="conv-1",
            user_id=1,
            session_id="session-1",
            message="汇总Jira",
            intent=intent,
        )

        wm = memory.get_working_memory("conv-1", user_id=1)
        assert len(wm.messages) > 0
        assert "project" in wm.entities


# ── _build_memory_vars pattern classification tests ──


class TestBuildMemoryVars:
    """Test _build_memory_vars pattern classification (MEM-14~16)."""

    def test_flush_decision_goes_to_decision_patterns(self):
        """MEM-14: [flush.decision]* patterns classified as decision_patterns."""
        extra_context = {
            "learned_patterns": [
                {"pattern": "[flush.decision] Important decision content", "usage_count": 5},
                {"pattern": "[flush.user_preference] User likes dark mode", "usage_count": 3},
            ],
        }
        result = _build_memory_vars(extra_context)
        assert "记住" in result["important_decisions"] or "[flush.decision]" in result["important_decisions"]

    def test_llm_distill_goes_to_high_usage(self):
        """MEM-15: [llm_distill.*] patterns go to other_high_usage pool."""
        extra_context = {
            "learned_patterns": [
                {"pattern": "[llm_distill.user_preference] Prefers summaries", "usage_count": 0},
            ],
        }
        result = _build_memory_vars(extra_context)
        assert "Prefers summaries" in result["important_decisions"]

    def test_high_usage_patterns_included(self):
        """MEM-16: High usage_count (>3) patterns included."""
        extra_context = {
            "learned_patterns": [
                {"pattern": "Frequently used pattern", "usage_count": 10},
            ],
        }
        result = _build_memory_vars(extra_context)
        assert "Frequently used pattern" in result["important_decisions"]

    def test_behavior_adjustments_from_flush(self):
        """flush patterns shown in behavior_adjustments."""
        extra_context = {
            "learned_patterns": [
                {"pattern": "[flush.overflow] project: AML123", "usage_count": 1},
            ],
        }
        result = _build_memory_vars(extra_context)
        assert "behavior_adjustments" in result
        assert "[flush.overflow]" in result["behavior_adjustments"]

    def test_flush_project_goes_to_project_context(self):
        """[flush.project*] patterns go to project_context."""
        extra_context = {
            "learned_patterns": [
                {"pattern": "[flush.project_info] AML123 is a test project", "usage_count": 1},
            ],
        }
        result = _build_memory_vars(extra_context)
        assert "AML123" in result["project_context"]
