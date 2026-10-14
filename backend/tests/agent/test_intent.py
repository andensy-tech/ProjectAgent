"""
Tests for app.services.agent.intent module (simplified — data classes only).

In the single-brain architecture, intent understanding is handled by the LLM
directly. This module only provides the Intent data class structure.

Test Cases:
- INT-01: Intent dataclass defaults
- INT-02: Intent with fields
- INT-03: Entity dataclass
"""

from app.services.agent.intent import Intent, Entity


class TestIntentDataClass:
    """Test the Intent data class."""

    def test_defaults(self):
        """INT-01: Intent with no args uses defaults."""
        intent = Intent()
        assert intent.intent_category == "other"
        assert intent.action == ""
        assert intent.entities == {}
        assert intent.target_skill is None
        assert intent.confidence == 0.0
        assert intent.clarification_needed is False

    def test_with_fields(self):
        """INT-02: Intent with explicit fields."""
        intent = Intent(
            intent_category="query",
            action="find issue",
            entities={"epic_ids": ["TV-12345"]},
            target_skill="jira",
            confidence=0.95,
            raw_message="find TV-12345",
        )
        assert intent.intent_category == "query"
        assert intent.action == "find issue"
        assert intent.entities["epic_ids"] == ["TV-12345"]
        assert intent.target_skill == "jira"
        assert intent.confidence == 0.95


class TestEntityDataClass:
    """Test the Entity data class."""

    def test_entity_defaults(self):
        """INT-03: Entity with default confidence."""
        entity = Entity(type="epic_id", value="TV-12345")
        assert entity.type == "epic_id"
        assert entity.value == "TV-12345"
        assert entity.confidence == 1.0
