"""
Alert service — CRUD + rule engine for the proactive risk alert system.

Provides:
  - list_alerts() / get_alert() / update_alert_status() / get_alert_summary()
  - generate_alerts()  — evaluate 5 rules against dashboard + CL snapshots
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select, func, or_, and_, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.models.alert import Alert
from app.services.dashboard_snapshot_service import load_latest as _load_dashboard
from app.services.cl_snapshot_service import load_latest_snapshots as _load_cl_snapshots
from app.services.urgency import score_issue, stagnation_factor

logger = logging.getLogger(__name__)


# ── CRUD ─────────────────────────────────────────────────────────────────────


async def list_alerts(
    status: Optional[str] = None,
    level: Optional[str] = None,
    category: Optional[str] = None,
    project_name: Optional[str] = None,
    user_id: Optional[int] = None,
    limit: int = 50,
    offset: int = 0,
) -> Tuple[List[Alert], int]:
    """List alerts with optional filters, newest first.

    Args:
        user_id: If provided, only returns alerts for this user.
                 If None, returns alerts for all users (admin view).

    Returns:
        Tuple of (alerts list, total count ignoring pagination).
    """
    async with AsyncSessionLocal() as db:
        conditions = []
        if status:
            conditions.append(Alert.status == status.upper())
        if level:
            conditions.append(Alert.level == level.upper())
        if category:
            conditions.append(Alert.category == category.upper())
        if project_name:
            conditions.append(Alert.description.ilike(f"%[{project_name}]%"))
        if user_id is not None:
            conditions.append(Alert.user_id == user_id)

        # Total count (ignoring pagination)
        count_stmt = select(func.count(Alert.id))
        if conditions:
            count_stmt = count_stmt.where(and_(*conditions))
        total = (await db.execute(count_stmt)).scalar() or 0

        # Paginated query
        stmt = select(Alert)
        if conditions:
            stmt = stmt.where(and_(*conditions))
        stmt = stmt.order_by(Alert.id.desc()).limit(limit).offset(offset)
        result = await db.execute(stmt)
        alerts = list(result.scalars().all())

        return alerts, total


async def get_alert(alert_id: int) -> Optional[Alert]:
    """Fetch a single alert by ID."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Alert).where(Alert.id == alert_id))
        return result.scalar_one_or_none()


async def update_alert_status(
    alert_id: int,
    status: str,
    resolution_note: Optional[str] = None,
    resolved_by: Optional[str] = None,
) -> Optional[Alert]:
    """Update an alert's status (ACKNOWLEDGED / RESOLVED / IGNORED)."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Alert).where(Alert.id == alert_id))
        alert = result.scalar_one_or_none()
        if alert is None:
            return None
        alert.status = status.upper()
        if status.upper() in ("RESOLVED", "IGNORED"):
            alert.resolved_at = datetime.now(timezone.utc)
            alert.resolved_by = resolved_by
            alert.resolution_note = resolution_note
        await db.commit()
        await db.refresh(alert)
        return alert


async def get_alert_summary(project_name: Optional[str] = None,
                            user_id: Optional[int] = None) -> Dict[str, Any]:
    """Return aggregated counts by status, level, and category.

    Args:
        project_name: Optional project filter (searches description for [project_name]).
        user_id: If provided, only counts alerts for this user.
    """
    async with AsyncSessionLocal() as db:
        # Optional project filter
        base_condition = []
        if project_name:
            base_condition.append(Alert.description.ilike(f"%[{project_name}]%"))
        if user_id is not None:
            base_condition.append(Alert.user_id == user_id)

        # Total by status
        status_counts: Dict[str, int] = {}
        stmt = select(Alert.status, func.count(Alert.id)).group_by(Alert.status)
        if base_condition:
            stmt = stmt.where(and_(*base_condition))
        result = await db.execute(stmt)
        for row in result:
            status_counts[row[0]] = row[1]

        # Total by level
        level_counts: Dict[str, int] = {}
        stmt = select(Alert.level, func.count(Alert.id)).group_by(Alert.level)
        if base_condition:
            stmt = stmt.where(and_(*base_condition))
        result = await db.execute(stmt)
        for row in result:
            level_counts[row[0]] = row[1]

        # Total by category
        category_counts: Dict[str, int] = {}
        stmt = select(Alert.category, func.count(Alert.id)).group_by(Alert.category)
        if base_condition:
            stmt = stmt.where(and_(*base_condition))
        result = await db.execute(stmt)
        for row in result:
            category_counts[row[0]] = row[1]

        # Active HIGH count (for sidebar badge)
        stmt = select(func.count(Alert.id)).where(
            and_(Alert.status == "ACTIVE", Alert.level == "HIGH", *base_condition)
        )
        active_high = await db.execute(stmt)
        high_count = active_high.scalar() or 0

    return {
        "total": sum(status_counts.values()),
        "by_status": {
            "ACTIVE": status_counts.get("ACTIVE", 0),
            "ACKNOWLEDGED": status_counts.get("ACKNOWLEDGED", 0),
            "RESOLVED": status_counts.get("RESOLVED", 0),
            "IGNORED": status_counts.get("IGNORED", 0),
        },
        "by_level": {
            "HIGH": level_counts.get("HIGH", 0),
            "MEDIUM": level_counts.get("MEDIUM", 0),
            "LOW": level_counts.get("LOW", 0),
        },
        "by_category": {
            "PROGRESS": category_counts.get("PROGRESS", 0),
            "QUALITY": category_counts.get("QUALITY", 0),
            "SCOPE": category_counts.get("SCOPE", 0),
        },
        "active_high": high_count,
    }


# ── Rule Engine ──────────────────────────────────────────────────────────────


async def _get_active_alert(db: AsyncSession, rule_id: str, project_name: str,
                             user_id: Optional[int] = None) -> Optional[Alert]:
    """Find existing ACTIVE/ACKNOWLEDGED alert for this rule + project + user.

    Returns the alert if found, None if no active alert exists.
    Used for dedup AND for updating evidence counts on re-scan.
    """
    conditions = [
        Alert.rule_id == rule_id,
        Alert.project_name == project_name,
        Alert.status.in_(["ACTIVE", "ACKNOWLEDGED"]),
    ]
    if user_id is not None:
        conditions.append(Alert.user_id == user_id)
    stmt = select(Alert).where(*conditions).limit(1)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def _upsert_alert(
    db: AsyncSession,
    rule_id: str,
    rule_name: str,
    category: str,
    level: str,
    title: str,
    description: str,
    impact: str,
    suggestion: str,
    project_name: str,
    user_id: Optional[int] = None,
    evidence: Optional[List[Dict[str, Any]]] = None,
) -> int:
    """Create a new alert or update existing one with fresh evidence.

    If an ACTIVE/ACKNOWLEDGED alert already exists for this rule+project+user,
    updates its title, description, and evidence with the latest counts
    (so the alert always shows current data, not stale creation-time values).
    Otherwise creates a new alert.

    Returns the alert ID.
    """
    existing = await _get_active_alert(db, rule_id, project_name, user_id)
    if existing:
        # Update existing alert with fresh counts
        existing.title = title
        existing.description = f"[{project_name}] {description}"
        existing.impact = impact
        existing.suggestion = suggestion
        existing.evidence = evidence or []
        await db.flush()
        return existing.id  # type: ignore[return-value]

    # Create new alert
    alert = Alert(
        project_name=project_name,
        user_id=user_id,
        rule_id=rule_id,
        rule_name=rule_name,
        category=category,
        level=level,
        title=title,
        description=f"[{project_name}] {description}",
        impact=impact,
        suggestion=suggestion,
        evidence=evidence or [],
        status="ACTIVE",
    )
    db.add(alert)
    await db.flush()
    return alert.id  # type: ignore[return-value]


async def generate_alerts(
    dashboard_summary: Optional[Dict[str, Any]] = None,
    cl_snapshots: Optional[List[Dict[str, Any]]] = None,
    user_id: Optional[int] = None,
) -> List[int]:
    """Evaluate all 5 rules against current data and create new alerts.

    De-duplication: same rule_id + project_name with ACTIVE/ACKNOWLEDGED
    status will be skipped.

    Args:
        dashboard_summary: If None, loads latest from cache.
        cl_snapshots: If None, loads latest from cache.
        user_id: If provided, associates created alerts with this user.

    Returns:
        List of created alert IDs.
    """
    if dashboard_summary is None:
        dashboard_summary = await _load_dashboard()
    if cl_snapshots is None:
        cl_snapshots = await _load_cl_snapshots() or []

    projects = (dashboard_summary or {}).get("projects", [])
    if not projects:
        logger.info("No dashboard projects found, skipping alert scan")
        return []

    # Build CL lookup: project_name → unmerged_cls count
    cl_unmerged: Dict[str, int] = {}
    for snap in (cl_snapshots or []):
        pname = snap.get("project", "")
        cl_unmerged[pname] = snap.get("unmerged_cls", 0)

    created_ids: List[int] = []

    async with AsyncSessionLocal() as db:
        for proj in projects:
            pname = proj.get("name", "")
            if not proj.get("enabled", True):
                continue

            issues = proj.get("issues", 0)
            epic_total = proj.get("epic_total", 0)
            unmerged = cl_unmerged.get(pname, -1)

            # ── 紧迫度评分（P-A：monitoring_optimization 紧迫度模型）────
            # 用逐 issue 明细（_count_issues）算紧迫度 = 优先级权重 × 时间紧迫度 × 停滞度，
            # 替代原先的简单计数（原 R1 读 "p1" 字段但 dashboard 存的是 "p0"，
            # 导致 P0/P1 预警永不触发 —— 本次修复）。
            thresholds = (dashboard_summary or {}).get("thresholds", {})
            stale_days = thresholds.get("stale_days", 7)
            eta_days = thresholds.get("eta_expiring_days", 7)
            count_issues = proj.get("_count_issues", []) or []
            scored = [score_issue(i, eta_days, stale_days)
                      for i in count_issues if isinstance(i, dict)]
            high_urgent = [s for s in scored if s["level"] == "HIGH"]          # 🔴 紧急
            attention = [s for s in scored if s["level"] in ("HIGH", "MEDIUM")]  # 🔴🟠 关注
            urgent_stale = [s for s in high_urgent
                            if stagnation_factor(s["updated"], stale_days) > 1.0]
            urgent_eta = [s for s in high_urgent if s["duedate"]]

            # ── Rule R1: 高紧迫度问题停滞（修复原 bug）──────────────────
            if urgent_stale:
                keys = "、".join(s["key"] for s in urgent_stale[:5])
                rid = await _upsert_alert(
                    db, "R1_P1_STALE", "高紧迫度问题停滞", "PROGRESS", "HIGH",
                    title=f"{len(urgent_stale)} 个高紧迫度问题停滞未更新",
                    description=f"项目 {pname} 有 {len(urgent_stale)} 个高紧迫度问题超过 {stale_days} 天未更新（{keys}）",
                    impact="高优先级+临期+停滞问题直接影响交付目标，需立即推动",
                    suggestion="逐条查看高紧迫度问题，确认阻塞原因并推动经办人更新状态",
                    project_name=pname, user_id=user_id,
                    evidence=[{"urgent_stale": [s["key"] for s in urgent_stale]}],
                )
                created_ids.append(rid)

            # ── Rule R2: 关注级问题堆积 ─────────────────────────────────
            if len(attention) > 5:
                rid = await _upsert_alert(
                    db, "R2_STALE_PILEUP", "关注级问题堆积", "PROGRESS", "MEDIUM",
                    title=f"{len(attention)} 个关注级问题（紧迫度≥3）",
                    description=f"项目 {pname} 有 {len(attention)} 个紧迫度 ≥3 的问题，其中 {len(high_urgent)} 个 🔴 紧急",
                    impact="关注级问题堆积说明项目进展受阻，需整体排查推进速度",
                    suggestion="按紧迫度排序逐一处理，优先推动 🔴 紧急问题",
                    project_name=pname,
                    evidence=[{"attention": len(attention), "high": len(high_urgent)}], user_id=user_id,
                )
                created_ids.append(rid)

            # ── Rule R3: CL_BOTTLENECK（保留，CL 独立维度）──────────────
            if unmerged > 3:
                rid = await _upsert_alert(
                    db, "R3_CL_BOTTLENECK", "CL 未合并堆积", "QUALITY", "MEDIUM",
                    title=f"{unmerged} 个 CL 未合并，存在 Code Review 瓶颈",
                    description=f"项目 {pname} 有 {unmerged} 个 CL 未合并，Code Review 可能出现瓶颈",
                    impact="CL 堆积会延长功能交付周期，增加合并冲突风险，影响后续迭代节奏",
                    suggestion="检查未合并 CL 的 Review 状态，协调 Reviewer 资源加速审批。建议设置 CL 合并 deadline",
                    project_name=pname,
                    evidence=[{"unmerged_cls": unmerged}], user_id=user_id,
                )
                created_ids.append(rid)

            # ── Rule R4: 高紧迫度问题临期/超期 ──────────────────────────
            if urgent_eta:
                keys = "、".join(s["key"] for s in urgent_eta[:5])
                rid = await _upsert_alert(
                    db, "R4_ETA_EXPIRING", "高紧迫度问题临期", "SCOPE", "MEDIUM",
                    title=f"{len(urgent_eta)} 个高紧迫度问题临期/超期",
                    description=f"项目 {pname} 有 {len(urgent_eta)} 个高紧迫度问题临近或超过目标日期（{keys}）",
                    impact="高优先级问题集中到期可能导致 Sprint 范围无法完整交付",
                    suggestion="逐条评估临期问题进度，必要时与 PM 沟通调整交付预期或重新分配资源",
                    project_name=pname,
                    evidence=[{"urgent_eta": [s["key"] for s in urgent_eta]}], user_id=user_id,
                )
                created_ids.append(rid)

            # ── Rule R5: EPIC_CONVERGENCE ───────────────────────────────
            if epic_total > 5 and unmerged >= 0 and unmerged < 2:
                rid = await _upsert_alert(
                    db, "R5_EPIC_CONVERGENCE", "Epic 收敛不足", "PROGRESS", "MEDIUM",
                    title=f"Epic 范围 {epic_total} 个问题，但仅 {max(unmerged, 0)} 个 CL 未合并",
                    description=f"项目 {pname} 的 Epic 下辖 {epic_total} 个问题，但 CL 进展不足 (未合并: {max(unmerged, 0)})，开发进度可能滞后于计划",
                    impact="Epic 收敛速度慢会影响整体交付时间线，可能导致里程碑延期",
                    suggestion="检视 Epic 下各 issue 的实际开发状态，确认是否存在技术阻塞或资源不足",
                    project_name=pname,
                    evidence=[{"epic_total": epic_total, "unmerged_cls": max(unmerged, 0)}], user_id=user_id,
                )
                created_ids.append(rid)

        await db.commit()

    if created_ids:
        logger.info("Alert scan created %d new alert(s): %s", len(created_ids), created_ids)
        # P-F：预警生成 → 站内消息（顶栏铃铛红点）
        if user_id:
            try:
                from app.services.notification_service import create_notification
                await create_notification(
                    user_id,
                    title=f"检测到 {len(created_ids)} 条新风险预警",
                    content="监控扫描发现新预警，请到预警中心查看并处理。",
                    notif_type="alert",
                    link="/alerts",
                )
            except Exception:
                logger.warning("Failed to create alert notification", exc_info=True)
    else:
        logger.info("Alert scan completed — no new alerts needed")
    return created_ids
