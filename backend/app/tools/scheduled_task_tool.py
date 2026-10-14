"""定时任务工具 — Agent 通用 scheduled tasks 的内置入口（2026-09-09）。

PM 在 chat 里表达"每隔 N 重复做 X 直到某日 / 每天定时做 X"类诉求时，LLM 组装声明式
config 调 create_scheduled_task 创建；list_scheduled_tasks 供 chat 内查询已有任务。
任务执行由平台引擎（scheduled_task_service.run_due_tasks）无人值守驱动，
看板「定时任务」页面可管理（启停/删除）。
"""

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


async def create_scheduled_task(params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """创建无人值守定时任务（声明式规则）。

    Args:
        params:
          name: 任务名（必填）
          config: 声明式配置（必填）：
            {"source": {"kind": "jira_jql", "jql": "..."},
             "conditions": [{"kind": "status_not_in", "statuses": [...]},
                            {"kind": "stale_days", "days": N, "basis": "last_comment"|"updated"}],
             "action": {"kind": "jira_comment", "template": "...",
                        "remind_interval_days": 3}}
            模板变量：{key} {summary} {owner} {stale_days}
          interval_minutes: 执行间隔（默认 60）
          run_until: 截止日 YYYY-MM-DD（可选；到期自动停止并通知）
          description: 说明（可选）
        context: 含 _user_id

    Returns:
        {"success": True, "id": ..., "name": ..., "next_run_at": ...}
        或 {"success": False, "error": 中文提示}
    """
    user_id = context.get("_user_id") if isinstance(context, dict) else None
    if not user_id:
        return {"success": False, "error": "缺少用户身份信息"}

    name = str(params.get("name", "") or "").strip()
    config = params.get("config")
    interval = params.get("interval_minutes", 60)

    run_until = None
    raw_until = params.get("run_until")
    if raw_until:
        from datetime import date as _date
        try:
            run_until = _date.fromisoformat(str(raw_until).strip())
        except ValueError:
            return {"success": False, "error": f"run_until 格式应为 YYYY-MM-DD，收到: {raw_until}"}

    from app.services import scheduled_task_handlers  # noqa: F401 — 确保 handler 已注册
    from app.services.scheduled_task_service import create_scheduled_task as svc_create

    try:
        task = await svc_create(
            user_id=user_id,
            name=name,
            config=config,
            interval_minutes=int(interval or 60),
            run_until=run_until,
            description=params.get("description"),
        )
    except ValueError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:  # noqa: BLE001 — 工具边界返回可读错误
        logger.warning("create_scheduled_task failed: %s", e)
        return {"success": False, "error": f"创建定时任务失败: {str(e)[:120]}"}

    return {
        "success": True,
        "id": task["id"],
        "name": task["name"],
        "interval_minutes": task["interval_minutes"],
        "run_until": task["run_until"],
        "next_run_at": task["next_run_at"],
        "message": f"已创建定时任务「{task['name']}」，每 {task['interval_minutes']} 分钟执行一次"
                   + (f"，至 {task['run_until']} 自动停止" if task["run_until"] else ""),
    }


async def list_scheduled_tasks(params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """列出当前用户的定时任务概览（chat 内查询；管理请用看板「定时任务」页面）。"""
    user_id = context.get("_user_id") if isinstance(context, dict) else None
    if not user_id:
        return {"success": False, "error": "缺少用户身份信息"}

    from app.services.scheduled_task_service import list_scheduled_tasks as svc_list

    try:
        tasks = await svc_list(user_id)
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": f"查询定时任务失败: {str(e)[:120]}"}

    return {
        "success": True,
        "count": len(tasks),
        "tasks": [
            {
                "id": t["id"],
                "name": t["name"],
                "task_type": t["task_type"],
                "interval_minutes": t["interval_minutes"],
                "run_until": t["run_until"],
                "active": t["active"],
                "next_run_at": t["next_run_at"],
                "last_run_status": t["last_run_status"],
                "run_count": t["run_count"],
            }
            for t in tasks
        ],
    }
