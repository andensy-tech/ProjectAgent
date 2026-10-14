"""Alembic environment configuration for Project Agent.

Supports both SQLite (development) and PostgreSQL (production).
Auto-discovers .env from the project root directory.
"""

import os
import sys
from logging.config import fileConfig
from pathlib import Path
from sqlalchemy import engine_from_config, pool
from alembic import context

# Add backend directory to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Load .env from project root (parent of backend/)
from dotenv import load_dotenv
env_path = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(env_path)

# Import all models so they register with Base.metadata for autogenerate
from app.models import Base, User, Conversation, Message, SkillConfig, Alert
from app.models import UserProfile, ProjectMemory, LearningRecord, SessionMemory, UserSetting
from app.models import Requirement, RequirementChange, RequirementRelation
from app.models import DashboardSnapshot, ClMonitorSnapshot, MetricSnapshot
from app.models import FocusSet, FocusSetMember

# Alembic Config object
config = context.config

# Set up Python logging from config file
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Override sqlalchemy.url from environment
db_url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./projectagent.db")
config.set_main_option("sqlalchemy.url", db_url.replace("+aiosqlite", "").replace("+asyncpg", ""))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (generate SQL, no DB connection needed)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (connect to DB directly)."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
