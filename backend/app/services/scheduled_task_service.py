"""通用定时任务引擎 — Agent scheduled tasks（2026-09-09）。

通用三层架构的数据+引擎层：
  - 数据：ScheduledTask 表（config 声明式规则 JSON / state 运行时状态）
  - 引擎：本模块（CRUD + run_due_tasks，**与任务类型无关**）
  - 执行：handler 注册表（task_type → validate + run）；本期仅 declarative_rule
    （scheduled_task_handlers.py 自注册），未来新类型 = 注册新 handler，引擎/看板/loop 全复用。
    二期 LLM 生成代码型 handler 也经 register_handler 接入，无需改动引擎。

调度语义：interval_minutes 间隔 + run_until 截止日（到期自动 active=False + 站内通知）。
单 worker 由 main.py _should_run_task("scheduled_tasks") 保证；任务执行 Semaphore 并发、
单任务失败只记 last_run_status 不中断其它任务（不静默）。
"""

import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import select, update as sa_update

from app.db.session import AsyncSessionLocal
from app.models.scheduled_task import ScheduledTask

logger = logging.getLogger(__name__)

# ── Handler 注册表 ────────────────────────────────────────────────────────────

# handler 协议：validate(config: dict) -> Optional[str]（None=合法，否则中文错误）
#                run(task: ScheduledTask, user_id: int) -> dict（执行摘要）
HandlerValidate = Callable[[Dict[str, Any]], Optional[str]]
HandlerRun = Callable[[ScheduledTask, int], Dict[str, Any]]

_HANDLERS: Dict[str, Dict[str, Callable]] = {}


def register_handler(task_type: str, validate: HandlerValidate, run: HandlerRun) -> None:
    """注册任务类型 handler。新任务类型 = 调用本函数，无需改引擎。"""
    _HANDLERS[task_type] = {"validate": validate, "run": run}
    logger.info("scheduled task handler registered: %s", task_type)


def get_handler(task_type: str) -> Optional[Dict[str, Callable]]:
    """取 handler；未注册时惰性加载 handlers 模块（触发自注册）。

    惰性加载而非顶部 import，是为了避免 scheduled_task_handlers → scheduled_task_service
    的循环 import；同时保证任何入口（API create/update、loop、直接调用）在首次使用时
    handler 均已就绪。
    """
    handler = _HANDLERS.get(task_type)
    if handler is None:
        try:
            from app.services import scheduled_task_handlers  # noqa: F401 — 触发自注册
            handler = _HANDLERS.get(task_type)
        except ImportError:
            pass
    return handler


def validate_config(task_type: str, config: Dict[str, Any]) -> Optional[str]:
    """校验 config；返回中文错误文案或 None。"""
    handler = get_handler(task_type)
    if handler is None:
        return f"未知的任务类型: {task_type}（已注册: {sorted(_HANDLERS) or '无'}）"
    try:
        return handler["validate"](config or {})
    except Exception as e:  # noqa: BLE001 — 校验器异常按非法配置处理
        return f"配置校验失败: {str(e)[:120]}"


# ── 内部工具 ─────────────────────────────────────────────────────────────────


def _bj_now() -> datetime:
    """北京时区 now（与 focus_set_service._today 同口径扩展）。"""
    from app.services.focus_set_service import _BJS_TZ
    return datetime.now(_BJS_TZ)


def _to_aware(dt: datetime) -> datetime:
    """naive datetime 补北京时区（SQLite 存 naive，比较前统一）。"""
    from app.services.focus_set_service import _BJS_TZ
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=_BJS_TZ)


# ── CRUD ─────────────────────────────────────────────────────────────────────


async def create_scheduled_task(
    user_id: int,
    name: str,
    config: Dict[str, Any],
    interval_minutes: int = 60,
    task_type: str = "declarative_rule",
    run_until: Optional[date] = None,
    description: Optional[str] = None,
) -> Dict[str, Any]:
    """创建定时任务（创建前经 handler.validate 校验，错误抛 ValueError）。"""
    if not user_id:
        raise ValueError("缺少用户身份信息")
    if not name or not str(name).strip():
        raise ValueError("任务名称不能为空")
    if not isinstance(config, dict) or not config:
        raise ValueError("任务配置 config 不能为空")
    err = validate_config(task_type, config)
    if err:
        raise ValueError(err)
    if int(interval_minutes or 0) < 1:
        raise ValueError("interval_minutes 必须 ≥ 1")

    async with AsyncSessionLocal() as db:
        task = ScheduledTask(
            user_id=user_id,
            name=str(name).strip(),
            description=description,
            task_type=task_type,
            config=config,
            interval_minutes=int(interval_minutes),
            run_until=run_until,
            active=True,
            next_run_at=_bj_now(),  # 创建后首轮立即可以执行
            state={},
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
        return _task_to_dict(task)


async def list_scheduled_tasks(user_id: int) -> List[Dict[str, Any]]:
    """列出用户的定时任务（含运行状态，供看板）。"""
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(ScheduledTask)
            .where(ScheduledTask.user_id == user_id)
            .order_by(ScheduledTask.created_at.desc())
        )).scalars().all()
        return [_task_to_dict(t) for t in rows]


async def update_scheduled_task(
    user_id: int,
    task_id: int,
    name: Optional[str] = None,
    active: Optional[bool] = None,
    interval_minutes: Optional[int] = None,
    run_until: Optional[date] = None,
    description: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """更新任务（启停/改名/改间隔/改截止日/改配置）。返回更新后 dict 或 None（不存在/越权）。

    改 config 时：重新 validate（handler 校验），并**重置 state 去重状态**——旧条件
    下的提醒去重记录对新条件无意义，避免改了停滞天数后仍被旧去重拦截。
    """
    async with AsyncSessionLocal() as db:
        task = await db.get(ScheduledTask, task_id)
        if task is None or task.user_id != user_id:
            return None
        if name is not None:
            task.name = str(name).strip() or task.name
        if description is not None:
            task.description = description
        if interval_minutes is not None:
            if int(interval_minutes) < 1:
                raise ValueError("interval_minutes 必须 ≥ 1")
            task.interval_minutes = int(interval_minutes)
        if run_until is not None:
            task.run_until = run_until
        if config is not None:
            err = validate_config(task.task_type, config)
            if err:
                raise ValueError(err)
            task.config = config
            task.state = {}  # 条件变了，重置去重
        if active is not None:
            task.active = bool(active)
            # 重新启用 → 立即恢复调度；停用 → 清除 next_run_at
            task.next_run_at = _bj_now() if task.active else None
        await db.commit()
        await db.refresh(task)
        return _task_to_dict(task)


async def delete_scheduled_task(user_id: int, task_id: int) -> bool:
    """删除任务。返回是否成功。"""
    async with AsyncSessionLocal() as db:
        task = await db.get(ScheduledTask, task_id)
        if task is None or task.user_id != user_id:
            return False
        await db.delete(task)
        await db.commit()
        return True


def _task_to_dict(t: ScheduledTask) -> Dict[str, Any]:
    """ScheduledTask → 可序列化 dict（API/工具/看板共用）。"""
    return {
        "id": t.id,
        "name": t.name,
        "description": t.description,
        "task_type": t.task_type,
        "config": t.config,
        "interval_minutes": t.interval_minutes,
        "run_until": t.run_until.isoformat() if t.run_until else None,
        "active": t.active,
        "next_run_at": t.next_run_at.isoformat() if t.next_run_at else None,
        "last_run_at": t.last_run_at.isoformat() if t.last_run_at else None,
        "last_run_status": t.last_run_status,
        "run_count": t.run_count or 0,
        "created_at": t.created_at.isoformat() if getattr(t, "created_at", None) else None,
    }


# ── 引擎执行（main.py loop 单 worker 驱动）────────────────────────────────────


async def run_due_tasks(max_concurrent: int = 5) -> Dict[str, Any]:
    """扫描并执行到期的任务。返回 {"ran": n, "expired": m, "failed": k}。

    - 到期条件：active=True 且 (next_run_at IS NULL OR next_run_at <= now)
    - 过期（run_until < 今天）→ active=False + 站内通知，不执行
    - 单任务异常只记 last_run_status，不影响其它任务
    """
    from app.services.notification_service import create_notification

    now = _bj_now()
    today = now.date()
    summary = {"ran": 0, "expired": 0, "failed": 0}

    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(ScheduledTask).where(ScheduledTask.active.is_(True))
        )).scalars().all()

        due: List[ScheduledTask] = []
        for t in rows:
            if t.run_until is not None and t.run_until < today:
                t.active = False
                t.next_run_at = None
                t.last_run_status = f"已到期（截止 {t.run_until.isoformat()}），自动停止"
                summary["expired"] += 1
                try:
                    await create_notification(
                        t.user_id,
                        title=f"⏰ 定时任务「{t.name}」已到截止日，自动停止",
                        content=f"任务执行了 {t.run_count or 0} 次。如需继续请修改截止日或新建任务。",
                        notif_type="scheduled_task",
                        link="/scheduled-tasks",
                    )
                except Exception as e:  # noqa: BLE001 — 通知失败不影响主流程
                    logger.warning("scheduled task expire notification failed: %s", e)
                continue
            nxt = _to_aware(t.next_run_at) if t.next_run_at else None
            if nxt is None or nxt <= now:
                due.append(t)
        await db.commit()

    if not due:
        return summary

    sem = asyncio.Semaphore(max_concurrent)

    async def _run_one(task_id: int) -> None:
        async with sem:
            try:
                await _execute_task(task_id, now)
                summary["ran"] += 1
            except Exception as e:  # noqa: BLE001 — 单任务失败隔离
                summary["failed"] += 1
                logger.warning("scheduled task %s failed: %s", task_id, e, exc_info=True)
                await _mark_result(task_id, now, None, f"error: {str(e)[:200]}")

    await asyncio.gather(*(_run_one(t.id) for t in due))
    return summary


async def _execute_task(task_id: int, now: datetime) -> None:
    """执行单个任务：取任务 → handler.run → 更新运行时状态。"""
    async with AsyncSessionLocal() as db:
        task = await db.get(ScheduledTask, task_id)
        if task is None or not task.active:
            return
        handler = get_handler(task.task_type)
        if handler is None:
            await _mark_result(task_id, now, None, f"error: 未知任务类型 {task.task_type}")
            return
        user_id = task.user_id

    # run 在 session 外执行（handler 内部自建 session 调 MCP/DB）
    result = await handler["run"](task, user_id)
    status = "ok: " + "; ".join(f"{k}={v}" for k, v in (result or {}).items())
    await _mark_result(task_id, now, result, status[:290])


async def _mark_result(
    task_id: int, now: datetime,
    result: Optional[Dict[str, Any]], status: str,
) -> None:
    """统一回写执行结果。state 由 handler 经 update_task_state 先写。"""
    async with AsyncSessionLocal() as db:
        task = await db.get(ScheduledTask, task_id)
        if task is None:
            return
        task.last_run_at = now
        task.last_run_status = status
        task.run_count = (task.run_count or 0) + 1
        task.next_run_at = now + timedelta(minutes=task.interval_minutes or 60)
        await db.commit()


async def update_task_state(task_id: int, state: Dict[str, Any]) -> None:
    """handler 写回运行时状态（去重等）。独立短事务，避免长事务持锁。"""
    async with AsyncSessionLocal() as db:
        await db.execute(
            sa_update(ScheduledTask)
            .where(ScheduledTask.id == task_id)
            .values(state=state)
        )
        await db.commit()
