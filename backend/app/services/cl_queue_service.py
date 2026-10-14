"""CL 监控追踪队列服务 — `cl_jira_states` 的读写与查询。

队列语义（2026-09-16 CL 监控 V2）：
  - 配置的 JQL 命中的题全部入队，之后新命中的陆续入队
  - 入队后只进不出，直到「CL 全部合并 且 Jira 已 Closed」才退出（queued=0）
  - 不在 JQL 结果里但仍在队列的题（如已 Closed）由扫描侧逐个 get_issue 复查

本模块只管数据存取；扫描编排见 `cl_scan_service`。
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.models.cl_jira_state import ClJiraState

logger = logging.getLogger(__name__)

# 进入「重点监控」的状态：Resolved 提醒走验证，Verified 提醒登记并合并 CL
FOCUS_STATUSES = ("Resolved", "Verified")
# 视为已闭环的状态（Closed 且 CL 全合并 -> 退出队列）
CLOSED_STATUSES = ("Closed", "Done")


def ensure_aware(dt: Optional[datetime]) -> Optional[datetime]:
    """SQLite 丢弃 tzinfo，读回后补回 UTC，避免与 aware 时间相减报错。"""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


async def load_states(db: AsyncSession, user_id: int,
                      project_name: Optional[str] = None,
                      only_queued: bool = False,
                      include_archived: bool = True) -> List[ClJiraState]:
    """加载该用户的追踪队列。

    Args:
        db: 数据库 session。
        user_id: 用户 ID。
        project_name: 限定项目；None 表示全部项目。
        only_queued: 只返回仍在队列中的（queued=True）。
        include_archived: 是否包含首轮归档的历史 Closed 记录。

    Returns:
        按 issue_key 排序的 ClJiraState 列表。
    """
    stmt = select(ClJiraState).where(ClJiraState.user_id == user_id)
    if project_name is not None:
        stmt = stmt.where(ClJiraState.project_name == project_name)
    if only_queued:
        stmt = stmt.where(ClJiraState.queued.is_(True))
    if not include_archived:
        stmt = stmt.where(ClJiraState.archived.is_(False))
    stmt = stmt.order_by(ClJiraState.issue_key)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def load_queue_map(db: AsyncSession, user_id: int,
                         project_name: Optional[str] = None) -> Dict[str, ClJiraState]:
    """返回 issue_key -> ClJiraState（仅队列中的题，供扫描 diff 使用）。"""
    states = await load_states(db, user_id, project_name, only_queued=True)
    return {s.issue_key: s for s in states}


async def get_state(db: AsyncSession, user_id: int,
                    issue_key: str) -> Optional[ClJiraState]:
    """按 issue_key 取单条队列表记录。"""
    stmt = select(ClJiraState).where(
        ClJiraState.user_id == user_id,
        ClJiraState.issue_key == issue_key,
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def upsert_state(db: AsyncSession, user_id: int, issue_key: str,
                       project_name: str = "", **fields: Any) -> ClJiraState:
    """按 (user_id, issue_key) 取或建队列记录，并写入给定字段。

    调用方负责 commit。
    """
    state = await get_state(db, user_id, issue_key)
    if state is None:
        state = ClJiraState(
            user_id=user_id, issue_key=issue_key,
            project_name=project_name, first_seen_at=datetime.now(timezone.utc),
        )
        db.add(state)
        await db.flush()
    elif project_name and state.project_name != project_name:
        state.project_name = project_name
    for key, value in fields.items():
        setattr(state, key, value)
    return state


async def list_focus(db: AsyncSession, user_id: int,
                     project_name: Optional[str] = None) -> List[ClJiraState]:
    """返回当前处于重点监控的题（队列中、未归档、状态为 Resolved/Verified/Closed）。

    Closed 的题仍需展示：CL 未合并时要进 PM summary 供 review。
    """
    states = await load_states(db, user_id, project_name, only_queued=True,
                               include_archived=False)
    watched = set(FOCUS_STATUSES) | set(CLOSED_STATUSES)
    return [s for s in states if s.status in watched]


def _state_to_item(state: ClJiraState) -> Dict[str, Any]:
    """把队列记录转成前端/汇总用的字典。"""
    try:
        cl_items = json.loads(state.cl_items_json or "[]")
    except (json.JSONDecodeError, TypeError):
        cl_items = []
    return {
        "jira_key": state.issue_key,
        "summary": state.summary,
        "assignee": state.assignee,
        "status": state.status,
        "prev_status": state.prev_status,
        "status_changed_at": (ensure_aware(state.status_changed_at).isoformat()
                              if state.status_changed_at else None),
        "project": state.project_name,
        "queue_reason": state.queue_reason,
        "cl_total": state.cl_total,
        "cl_merged": state.cl_merged,
        "cl_unmerged": state.cl_unmerged,
        "cl_unregistered": state.cl_noregister,
        "cl_items": cl_items,
        "last_reminded_at": (ensure_aware(state.last_reminded_at).isoformat()
                             if state.last_reminded_at else None),
        "queue_age_days": _age_days(state.first_seen_at),
        # 经办人已说明该题 CL 处理完毕 -> 已停止提醒（保留在此供 PM 复核）
        "ack_at": (ensure_aware(state.ack_at).isoformat() if state.ack_at else None),
        "ack_reason": state.ack_reason or "",
    }


def _age_days(dt: Optional[datetime]) -> Optional[int]:
    """入队至今天数（北京时区口径由调用方决定，这里只做 UTC 差值）。"""
    aware = ensure_aware(dt)
    if aware is None:
        return None
    return max(0, (datetime.now(timezone.utc) - aware).days)


def build_summary(states: List[ClJiraState]) -> Dict[str, Any]:
    """构建 PM review 用的重点监控汇总（纯函数，便于测试）。

    Args:
        states: 队列记录列表（通常来自 list_focus）。

    Returns:
        {"total": n, "groups": {"Resolved": [...], "Verified": [...], "Closed": [...]},
         "unregistered": [...], "unmerged": [...]}
        - groups: 按当前状态分组
        - unregistered: 未登记任何 CL 的题（易被遗漏，需 PM 确认）
        - unmerged: CL 未全部合并的题
    """
    groups: Dict[str, List[Dict[str, Any]]] = {s: [] for s in FOCUS_STATUSES}
    groups["Closed"] = []
    unregistered: List[Dict[str, Any]] = []
    unmerged: List[Dict[str, Any]] = []

    for state in states:
        item = _state_to_item(state)
        bucket = item["status"] if item["status"] in groups else "Closed"
        groups[bucket].append(item)
        if item["cl_unregistered"]:
            unregistered.append(item)
        elif item["cl_unmerged"] > 0:
            unmerged.append(item)

    return {
        "total": len(states),
        "groups": groups,
        "unregistered": unregistered,
        "unmerged": unmerged,
    }


async def fetch_summary(user_id: int) -> Dict[str, Any]:
    """API 用：打开独立 session 构建重点监控汇总。"""
    async with AsyncSessionLocal() as db:
        states = await list_focus(db, user_id)
        return build_summary(states)
