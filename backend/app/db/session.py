"""
Database session management.

Provides async SQLAlchemy engine and session factory.
Supports both PostgreSQL (asyncpg) and SQLite (aiosqlite).
"""

import logging
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from app.core.config import settings

logger = logging.getLogger(__name__)

# Determine driver based on URL
if settings.DATABASE_URL.startswith("sqlite"):
    # Enable WAL mode before any engine connection — standalone sync connection
    # at module init time, before the async engine pool creates any connections.
    # Once set, WAL persists in the database file across all future connections
    # (including those from the uvicorn --reload secondary process).
    # WAL mode allows concurrent reads + single writer, reducing lock contention.
    import sqlite3 as _sqlite3
    _db_path = settings.DATABASE_URL.replace("sqlite+aiosqlite:///", "")
    try:
        _wal_conn = _sqlite3.connect(_db_path, timeout=10)
        _wal_conn.execute("PRAGMA journal_mode=WAL")
        # Set busy_timeout to 60s on the WAL connection too
        _wal_conn.execute("PRAGMA busy_timeout=60000")
        _wal_conn.close()
    except Exception as exc:
        logger.warning("Cannot enable WAL mode (non-fatal): %s", exc)

    # timeout=60 means SQLite waits up to 60 seconds for the write lock
    # before raising "database is locked". This is critical for async apps
    # where background tasks and SSE generators may briefly contend for writes.
    engine = create_async_engine(
        settings.DATABASE_URL,
        echo=settings.DEBUG,
        connect_args={"check_same_thread": False, "timeout": 60},
    )

    # Enable foreign key enforcement on every connection from the pool.
    # SQLite defaults to PRAGMA foreign_keys = OFF, so ondelete="CASCADE"
    # declared in models would be silently ignored without this.
    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_foreign_keys(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
else:
    # PostgreSQL: asyncpg driver
    engine = create_async_engine(
        settings.DATABASE_URL,
        echo=settings.DEBUG,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
    )

# Session factory
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncSession:
    """Dependency that provides a database session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db():
    """Create all database tables."""
    from app.models.base import Base
    from app.models import (
        User, Conversation, Message, SkillConfig,
        Alert, UserProfile, ProjectMemory, LearningRecord, SessionMemory, MemoryLog,
        Requirement, RequirementChange, RequirementRelation,
        ClMonitorSnapshot,
        ClJiraState,
        DashboardSnapshot,
        MetricSnapshot,
        UserSetting,
        BackgroundTask,
        FeishuBindCode,
        FocusSet, FocusSetMember,
        Notification,
        ProjectPlan, ProjectPlanPhase,
        ScheduledTask,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def seed_default_user():
    """Create default admin user if no users exist in the database.

    Uses the ADMIN_PASSWORD_HASH from env config, or creates one from
    a default password in DEBUG mode. Admin must change password on first login.
    """
    from app.models import User
    from app.core.security import get_password_hash
    from sqlalchemy import select, func

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(func.count(User.id)))
        count = result.scalar() or 0
        if count > 0:
            return  # Users already exist

        # Determine password
        if settings.ADMIN_PASSWORD_HASH and settings.ADMIN_PASSWORD_HASH != "$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/X4.VTtYA.qGZvKG6G":
            # Custom hash from env
            hashed = settings.ADMIN_PASSWORD_HASH
        else:
            # Default: admin123
            hashed = get_password_hash("admin123")

        admin = User(
            username="admin",
            email="admin@project-agent.local",
            full_name="Administrator",
            hashed_password=hashed,
            is_active=True,
            is_superuser=True,
            force_password_change=True,
        )
        db.add(admin)
        await db.commit()
        if settings.DEBUG:
            print("✅ 默认 admin 用户已创建（用户名: admin，密码: admin123）— 请立即修改密码！")
        else:
            print("✅ 默认 admin 用户已创建 — 请通过系统管理员获取初始密码并立即修改。")
