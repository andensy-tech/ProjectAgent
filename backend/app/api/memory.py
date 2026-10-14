"""
API endpoints for memory maintenance (distillation + cleanup).

Provides on-demand triggers for manually running memory maintenance
and checking maintenance status.
"""

import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_current_active_user as get_current_user, get_db
from app.services.memory import memory_system, search_engine

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/memory", tags=["memory"])


@router.post("/distill")
async def run_memory_distillation(
    days_back: int = 7,
    log_retention_days: int = 30,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Manually trigger memory distillation + cleanup (Stages 2+3).

    Scans recent session logs, extracts patterns into long-term memory,
    and cleans up stale entries. Returns stats on what was processed.
    """
    try:
        user_id = user.get("user_id")
        result = await memory_system.run_maintenance(
            db=db,
            user_id=user_id,
            days_back=days_back,
            log_retention_days=log_retention_days,
        )
        return {
            "success": True,
            "message": "记忆维护完成",
            "data": result,
        }
    except Exception as e:
        logger.error("Memory distillation failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"记忆维护失败: {str(e)}")


@router.get("/maintenance-status")
async def get_memory_maintenance_status(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get memory maintenance status (undistilled logs count etc.)."""
    try:
        user_id = user.get("user_id")
        status = await memory_system.get_maintenance_status(db, user_id)
        return {
            "success": True,
            "data": status,
        }
    except Exception as e:
        logger.error("Failed to get maintenance status: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/embeddings/backfill")
async def backfill_search_embeddings(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Backfill vector embeddings for all FTS5 index entries.

    Call this once to enable hybrid (vector + keyword) search.
    """
    try:
        stats = await search_engine.compute_all_embeddings()
        return {
            "success": True,
            "message": f"嵌入计算完成: {stats.get('computed', 0)} 成功, "
                       f"{stats.get('failed', 0)} 失败",
            "data": stats,
        }
    except Exception as e:
        logger.error("Embedding backfill failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
