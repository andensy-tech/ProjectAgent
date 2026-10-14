"""
Dashboard API — project overview metrics and configuration.

Endpoints:
  GET  /api/dashboard/summary    — aggregated Jira metrics
  GET  /api/dashboard/config     — dashboard configuration
  PUT  /api/dashboard/config     — update configuration
  GET  /api/dashboard/cl_status           — CL status for alerts page (cached, ?refresh=true for live)
  GET  /api/dashboard/cl_monitoring_config — CL monitoring config (independent)
  PUT  /api/dashboard/cl_monitoring_config — save CL monitoring config
"""

import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.api.deps import get_current_active_user
from app.services.dashboard_service import (
    get_dashboard_config as _load_config,
    update_dashboard_config as _save_config,
    get_dashboard_summary as _load_summary,
    get_cl_monitoring_config,
    save_cl_monitoring_config,
    get_project_issues as _load_project_issues,
    admin_get_all_users_summary,
)
from app.services.cl_snapshot_service import load_or_refresh as _load_or_refresh_snapshots
from app.services.dashboard_snapshot_service import load_or_refresh as _load_or_refresh_dashboard

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])
logger = logging.getLogger(__name__)


class DashboardConfigUpdate(BaseModel):
    """Dashboard configuration update body."""
    projects: list = []
    thresholds: dict = {}
    refresh_interval_seconds: int = 30
    jira_web_url: str = ""


class ClMonitoringConfigUpdate(BaseModel):
    """CL monitoring configuration (independent of dashboard)."""
    projects: list = []


class ClSuppressItem(BaseModel):
    """单个不关注的 CL 条目。"""
    instance: str = ""
    change_id: str = ""
    jira_key: str = ""
    reason: str = "manual"


class ClSuppressRequest(BaseModel):
    """批量标记不关注请求体。"""
    items: list[ClSuppressItem] = []


class FocusSetCreate(BaseModel):
    """创建/更新关注集合（P-B：聊天式关注指令 → 看板状态图）。"""
    type: str = "filter"  # filter | epic | manual
    identifier: str
    name: str = ""
    jql: str = ""
    deadline: Optional[str] = None  # ISO 日期
    metric_type: Optional[str] = None  # fix_rate | all_fix
    metric_target: Optional[float] = None
    priority_semantics: Optional[str] = None
    description: Optional[str] = None


@router.get("/summary")
async def dashboard_summary(
    refresh: bool = Query(False, description="强制实时查询而非使用缓存"),
    view: str = Query("self", description="'self' 仅看自己, 'all' 管理员查看所有用户"),
    current_user: dict = Depends(get_current_active_user),
) -> Dict[str, Any]:
    """Return aggregated Jira metrics.

    By default serves from database cache (last snapshot).
    Pass ?refresh=true to trigger a live Jira query.
    Admin can pass ?view=all to see all users' project data.
    """
    is_admin = current_user.get("is_superuser", False)
    user_id = current_user.get("user_id")

    if view == "all":
        if not is_admin:
            from fastapi import HTTPException
            raise HTTPException(status_code=403, detail="仅管理员可查看所有用户数据")
        logger.info("Admin view=all requested by user_id=%s", user_id)
        return await admin_get_all_users_summary()

    return await _load_or_refresh_dashboard(force_live=refresh, user_id=user_id)


@router.get("/config")
async def dashboard_config_get(
    current_user: dict = Depends(get_current_active_user),
) -> Dict[str, Any]:
    """Return current dashboard configuration for this user."""
    user_id = current_user.get("user_id")
    return await _load_config(user_id)


@router.put("/config")
async def dashboard_config_put(
    data: DashboardConfigUpdate,
    current_user: dict = Depends(get_current_active_user),
) -> Dict[str, Any]:
    """Update dashboard configuration for this user."""
    user_id = current_user.get("user_id")
    return await _save_config(data.model_dump(), user_id)


@router.get("/cl_status")
async def dashboard_cl_status(
    refresh: bool = Query(False, description="强制实时查询而非使用缓存"),
    current_user: dict = Depends(get_current_active_user),
) -> List[Dict[str, Any]]:
    """Return Gerrit CL status per project (for alerts page).

    By default serves from database cache (last snapshot).
    Pass ?refresh=true to trigger a live Jira+Gerrit query.

    Results are automatically refreshed daily via background scheduler.
    """
    user_id = current_user.get("user_id")
    return await _load_or_refresh_snapshots(refresh=refresh, user_id=user_id)


@router.get("/cl_focus_summary")
async def cl_focus_summary(
    current_user: dict = Depends(get_current_active_user),
) -> Dict[str, Any]:
    """重点监控汇总（PM review 用）：按状态分组的追踪队列视图。

    包含 Resolved / Verified / Closed（CL 未合并）分组，以及「未登记 CL」清单
    —— 后者最容易被遗漏，需要 PM 确认是否漏提 CL。
    """
    from app.services.cl_queue_service import fetch_summary
    return await fetch_summary(current_user.get("user_id"))


@router.get("/cl_monitoring_config")
async def cl_monitoring_config_get(
    current_user: dict = Depends(get_current_active_user),
) -> Dict[str, Any]:
    """Return CL monitoring configuration for this user."""
    user_id = current_user.get("user_id")
    return await get_cl_monitoring_config(user_id)


@router.put("/cl_monitoring_config")
async def cl_monitoring_config_put(
    data: ClMonitoringConfigUpdate,
    current_user: dict = Depends(get_current_active_user),
) -> Dict[str, Any]:
    """Save CL monitoring configuration for this user."""
    user_id = current_user.get("user_id")
    return await save_cl_monitoring_config(data.model_dump(), user_id)


@router.get("/cl_suppress")
async def cl_suppress_list(
    current_user: dict = Depends(get_current_active_user),
) -> List[Dict[str, Any]]:
    """列出该用户标记不关注的 CL（供前端展示/恢复）。"""
    from app.services.settings_service import get_cl_suppressed
    return await get_cl_suppressed(current_user.get("user_id"))


@router.post("/cl_suppress")
async def cl_suppress_add(
    data: ClSuppressRequest,
    current_user: dict = Depends(get_current_active_user),
) -> List[Dict[str, Any]]:
    """标记 CL 不关注（之后不再显示、不再自动提醒）。"""
    from app.services.settings_service import add_cl_suppressed
    return await add_cl_suppressed(
        current_user.get("user_id"), [i.model_dump() for i in data.items],
    )


@router.delete("/cl_suppress")
async def cl_suppress_remove(
    instance: str = Query(..., description="Gerrit 实例名"),
    change_id: str = Query(..., description="CL 编号"),
    jira_key: str = Query(..., description="Jira issue key"),
    current_user: dict = Depends(get_current_active_user),
) -> List[Dict[str, Any]]:
    """恢复某 CL 关注。"""
    from app.services.settings_service import remove_cl_suppressed
    return await remove_cl_suppressed(current_user.get("user_id"), instance, change_id, jira_key)


@router.get("/project-issues")
async def dashboard_project_issues(
    project: str = Query(..., description="项目配置名称"),
    max_results: int = Query(200, description="最多返回条数"),
    filter: str = Query("all", description="过滤类型: all/p0/eta_expiring/stale"),
    current_user: dict = Depends(get_current_active_user),
) -> List[Dict[str, Any]]:
    """Fetch Jira issue list for a configured project."""
    user_id = current_user.get("user_id")
    return await _load_project_issues(project, max_results, filter, user_id)


@router.get("/focus_sets")
async def focus_sets_list(
    current_user: dict = Depends(get_current_active_user),
) -> List[Dict[str, Any]]:
    """列出用户的关注集合（含健康度 + 成员状态分布 + 停滞成员）。"""
    from app.services.focus_set_service import list_focus_sets
    user_id = current_user.get("user_id")
    return await list_focus_sets(user_id)


@router.post("/focus_sets")
async def focus_sets_create(
    data: FocusSetCreate,
    current_user: dict = Depends(get_current_active_user),
) -> Dict[str, Any]:
    """创建/更新关注集合（按 user_id+type+identifier 去重 upsert）。"""
    from datetime import date as _date
    from app.services.focus_set_service import create_or_update_focus_set

    user_id = current_user.get("user_id")
    deadline = None
    if data.deadline:
        try:
            deadline = _date.fromisoformat(data.deadline.strip())
        except ValueError:
            raise HTTPException(status_code=400, detail="deadline 格式应为 YYYY-MM-DD")

    fs = await create_or_update_focus_set(
        user_id=user_id,
        focus_type=data.type,
        identifier=data.identifier.strip(),
        name=data.name or f"{data.type}-{data.identifier}",
        jql=data.jql or None,
        deadline=deadline,
        metric_type=data.metric_type,
        metric_target=data.metric_target,
        priority_semantics=data.priority_semantics,
        description=data.description,
    )
    return {"id": fs.id, "name": fs.name, "type": fs.type, "identifier": fs.identifier}


@router.put("/focus_sets/{focus_set_id}/dismiss")
async def focus_sets_dismiss(
    focus_set_id: int,
    current_user: dict = Depends(get_current_active_user),
) -> Dict[str, Any]:
    """取消关注某集合（active=False，看板隐藏）。"""
    from app.services.focus_set_service import dismiss_focus_set
    user_id = current_user.get("user_id")
    ok = await dismiss_focus_set(user_id, focus_set_id)
    if not ok:
        raise HTTPException(status_code=404, detail="关注集合不存在")
    return {"success": True, "id": focus_set_id}
