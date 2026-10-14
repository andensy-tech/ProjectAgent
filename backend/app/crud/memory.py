"""
CRUD operations for memory system models (UserProfile, LearningRecord, ProjectMemory, SessionMemory).

These back the four-layer memory architecture with database persistence
so that long-term memory survives server restarts.
"""

from typing import List, Optional, Dict, Any
from datetime import datetime, date, timedelta
from sqlalchemy import select, desc, and_, delete
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.memory import UserProfile, LearningRecord, ProjectMemory, SessionMemory as SessionMemoryModel, MemoryLog


# ── UserProfile ──────────────────────────────────────────────────────────────

async def get_user_profile(db: AsyncSession, user_id: int) -> Optional[UserProfile]:
    """Get a user profile by user_id."""
    stmt = select(UserProfile).where(UserProfile.user_id == user_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def update_user_profile(
    db: AsyncSession,
    user_id: int,
    updates: Dict[str, Any],
) -> UserProfile:
    """Create or update a user profile."""
    profile = await get_user_profile(db, user_id)
    if profile is None:
        profile = UserProfile(user_id=user_id)
        db.add(profile)

    for key, value in updates.items():
        if hasattr(profile, key):
            if key in ("preferred_projects", "preferred_skills"):
                existing = getattr(profile, key) or []
                if value not in existing:
                    setattr(profile, key, existing + [value])
            elif key == "learned_preferences":
                existing = profile.learned_preferences or {}
                existing.update(value)
                profile.learned_preferences = existing
            else:
                setattr(profile, key, value)

    await db.commit()
    await db.refresh(profile)
    return profile


# ── LearningRecord ───────────────────────────────────────────────────────────

async def create_learning_record(
    db: AsyncSession,
    user_id: int,
    pattern: str,
    context: Optional[str] = None,
    success_rate: float = 1.0,
) -> LearningRecord:
    """Create a new learning record."""
    record = LearningRecord(
        user_id=user_id,
        pattern=pattern,
        context=context,
        success_rate=success_rate,
        usage_count=1,
        last_used=datetime.utcnow(),
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record


async def list_learning_records(
    db: AsyncSession,
    user_id: int,
    limit: int = 10,
) -> List[LearningRecord]:
    """Get recent learning records for a user."""
    stmt = (
        select(LearningRecord)
        .where(LearningRecord.user_id == user_id)
        .order_by(desc(LearningRecord.last_used))
        .limit(limit)
    )
    result = await db.execute(stmt)
    return result.scalars().all()


# ── ProjectMemory ────────────────────────────────────────────────────────────

async def get_project_memory(db: AsyncSession, project_id: int) -> Optional[ProjectMemory]:
    """Get project memory."""
    stmt = select(ProjectMemory).where(ProjectMemory.project_id == project_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def update_project_memory(
    db: AsyncSession,
    project_id: int,
    updates: Dict[str, Any],
) -> ProjectMemory:
    """Create or update project memory."""
    memory = await get_project_memory(db, project_id)
    if memory is None:
        memory = ProjectMemory(project_id=project_id)
        db.add(memory)

    for key, value in updates.items():
        if hasattr(memory, key):
            existing = getattr(memory, key) or []
            if isinstance(value, list):
                setattr(memory, key, existing + value)
            else:
                setattr(memory, key, value)

    await db.commit()
    await db.refresh(memory)
    return memory


# ── SessionMemory ────────────────────────────────────────────────────────────

async def get_session_memory(db: AsyncSession, session_id: str) -> Optional[SessionMemoryModel]:
    """Get session memory by session_id."""
    stmt = select(SessionMemoryModel).where(SessionMemoryModel.session_id == session_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def save_session_memory(
    db: AsyncSession,
    session_id: str,
    user_id: int,
    active_project: Optional[str] = None,
    active_config: Optional[str] = None,
    skill_results: Optional[Dict] = None,
    preferences: Optional[Dict] = None,
) -> SessionMemoryModel:
    """Create or update session memory."""
    session = await get_session_memory(db, session_id)
    if session is None:
        session = SessionMemoryModel(
            session_id=session_id,
            user_id=user_id,
        )
        db.add(session)

    if active_project is not None:
        session.active_project = active_project
    if active_config is not None:
        session.active_config = active_config
    if skill_results is not None:
        session.skill_results = skill_results
    if preferences is not None:
        session.preferences = preferences

    await db.commit()
    await db.refresh(session)
    return session


# ── MemoryLog (daily session logs for maintenance) ──────────────────────────


async def create_memory_log(
    db: AsyncSession,
    user_id: int,
    date_str: str,
    session_id: Optional[str] = None,
    turn_index: int = 0,
    user_message_preview: Optional[str] = None,
    assistant_summary: Optional[str] = None,
    key_topics: Optional[list] = None,
    tool_usage: Optional[list] = None,
) -> MemoryLog:
    """Create a daily session log entry for one conversation turn."""
    log = MemoryLog(
        user_id=user_id,
        date=date_str,
        session_id=session_id,
        turn_index=turn_index,
        user_message_preview=user_message_preview,
        assistant_summary=assistant_summary,
        key_topics=key_topics or [],
        tool_usage=tool_usage or [],
    )
    db.add(log)
    await db.commit()
    await db.refresh(log)
    return log


async def list_memory_logs(
    db: AsyncSession,
    user_id: int,
    since_date: Optional[str] = None,
    limit: int = 100,
    undistilled_only: bool = False,
) -> List[MemoryLog]:
    """List memory logs for a user, newest first."""
    conditions = [MemoryLog.user_id == user_id]
    if since_date:
        conditions.append(MemoryLog.date >= since_date)
    if undistilled_only:
        conditions.append(MemoryLog.distilled == False)

    stmt = (
        select(MemoryLog)
        .where(and_(*conditions))
        .order_by(desc(MemoryLog.id))
        .limit(limit)
    )
    result = await db.execute(stmt)
    return result.scalars().all()


async def count_undistilled_logs(db: AsyncSession, user_id: int) -> int:
    """Count conversation turns not yet distilled."""
    stmt = (
        select(MemoryLog)
        .where(and_(MemoryLog.user_id == user_id, MemoryLog.distilled == False))
    )
    result = await db.execute(stmt)
    return len(result.scalars().all())


async def mark_logs_distilled(db: AsyncSession, user_id: int, up_to_id: int) -> int:
    """Mark logs as distilled (up to a given id). Returns count updated."""
    stmt = (
        select(MemoryLog)
        .where(
            and_(
                MemoryLog.user_id == user_id,
                MemoryLog.distilled == False,
                MemoryLog.id <= up_to_id,
            )
        )
    )
    result = await db.execute(stmt)
    logs = result.scalars().all()
    now = datetime.utcnow()
    for log in logs:
        log.distilled = True
    if logs:
        await db.commit()
    return len(logs)


async def mark_logs_distilled_by_ids(db: AsyncSession, user_id: int, log_ids: List[int]) -> int:
    """Mark specific logs as distilled by their IDs. Returns count updated."""
    if not log_ids:
        return 0
    stmt = (
        select(MemoryLog)
        .where(
            and_(
                MemoryLog.user_id == user_id,
                MemoryLog.distilled == False,
                MemoryLog.id.in_(log_ids),
            )
        )
    )
    result = await db.execute(stmt)
    logs = result.scalars().all()
    for log in logs:
        log.distilled = True
    if logs:
        await db.commit()
    return len(logs)


async def delete_old_logs(db: AsyncSession, before_date: str, user_id: Optional[int] = None) -> int:
    """Delete memory logs older than a date, optionally for a specific user."""
    stmt = delete(MemoryLog).where(MemoryLog.date < before_date)
    if user_id is not None:
        stmt = stmt.where(MemoryLog.user_id == user_id)
    result = await db.execute(stmt)
    await db.commit()
    return result.rowcount


async def delete_stale_learning_records(
    db: AsyncSession,
    min_usage: int = 3,
    max_success_rate: float = 0.3,
    older_than_days: int = 90,
    user_id: Optional[int] = None,
) -> int:
    """Delete learning records with low usage and low success rate.

    Only targets records that haven't been used recently.
    Can be scoped to a specific user_id.
    """
    cutoff = datetime.utcnow() - timedelta(days=older_than_days)
    conditions = [
        LearningRecord.usage_count < min_usage,
        LearningRecord.success_rate < max_success_rate,
        LearningRecord.last_used < cutoff,
    ]
    if user_id is not None:
        conditions.append(LearningRecord.user_id == user_id)
    stmt = (
        select(LearningRecord)
        .where(and_(*conditions))
    )
    result = await db.execute(stmt)
    records = result.scalars().all()
    for r in records:
        await db.delete(r)
    if records:
        await db.commit()
    return len(records)
