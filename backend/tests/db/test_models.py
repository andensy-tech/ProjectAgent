"""
Tests for database models.

Test Cases:
- DB-M-01~06: Model creation and defaults
- DB-M-07: TimestampMixin auto-population
"""

import pytest
from sqlalchemy import select

from app.models import (
    User,
    Conversation,
    Message,
    SkillConfig,
    Alert,
    UserProfile,
    ProjectMemory,
    LearningRecord,
    SessionMemory,
    Requirement,
    RequirementChange,
    RequirementRelation,
)


class TestUserModel:
    """Test User model (DB-M-01)."""

    @pytest.mark.asyncio
    async def test_create_user(self, db_session):
        """DB-M-01: User created with all fields."""
        user = User(
            username="testuser",
            email="test@example.com",
            full_name="Test User",
            hashed_password="hashed_pass",
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        assert user.id is not None
        assert user.username == "testuser"
        assert user.email == "test@example.com"
        assert user.is_active is True
        assert user.is_superuser is False  # default non-admin


class TestConversationModel:
    """Test Conversation model (DB-M-02)."""

    @pytest.mark.asyncio
    async def test_create_conversation(self, db_session):
        """DB-M-02: Conversation with default JSON fields."""
        # Need a user first
        user = User(username="conv_test", hashed_password="pass")
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        conv = Conversation(
            user_id=user.id,
            title="Test Conversation",
        )
        db_session.add(conv)
        await db_session.commit()
        await db_session.refresh(conv)

        assert conv.id is not None
        assert conv.title == "Test Conversation"
        assert conv.messages == []
        assert conv.context_vars == {}
        assert conv.entities == {}


class TestMessageModel:
    """Test Message model (DB-M-03)."""

    @pytest.mark.asyncio
    async def test_create_message(self, db_session):
        """DB-M-03: Message with default extra_data."""
        user = User(username="msg_test", hashed_password="pass")
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        conv = Conversation(user_id=user.id, title="Test")
        db_session.add(conv)
        await db_session.commit()
        await db_session.refresh(conv)

        msg = Message(
            conversation_id=conv.id,
            role="user",
            content="Hello",
        )
        db_session.add(msg)
        await db_session.commit()
        await db_session.refresh(msg)

        assert msg.id is not None
        assert msg.role == "user"
        assert msg.content == "Hello"
        assert msg.message_type == "text"
        assert msg.extra_data == {}


class TestSkillConfigModel:
    """Test SkillConfig model (DB-M-05)."""

    @pytest.mark.asyncio
    async def test_create_skill_config(self, db_session):
        """DB-M-05: SkillConfig with user override JSON."""
        user = User(username="skill_cfg_user", hashed_password="pass")
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        config = SkillConfig(
            user_id=user.id,
            skill_name="jira-report",
            config_json={"label": "test"},
        )
        db_session.add(config)
        await db_session.commit()
        await db_session.refresh(config)

        assert config.id is not None
        assert config.skill_name == "jira-report"
        assert config.config_json["label"] == "test"


class TestAlertModel:
    """Test Alert model (DB-M-06)."""

    @pytest.mark.asyncio
    async def test_create_alert(self, db_session):
        """DB-M-06: Alert with status default ACTIVE."""
        user = User(username="alert_user", hashed_password="pass")
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        alert = Alert(
            user_id=user.id,
            project_name="AML123",
            rule_id="PROG-001",
            category="PROGRESS",
            level="HIGH",
            title="Schedule delay",
        )
        db_session.add(alert)
        await db_session.commit()
        await db_session.refresh(alert)

        assert alert.id is not None
        assert alert.status == "ACTIVE"
        assert alert.rule_id == "PROG-001"


class TestTimestampMixin:
    """Test timestamp auto-population (DB-M-07)."""

    @pytest.mark.asyncio
    async def test_timestamps_auto_populated(self, db_session):
        """DB-M-07: created_at and updated_at are set."""
        user = User(username="time_test", hashed_password="pass")
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        assert user.created_at is not None
        assert user.updated_at is not None
