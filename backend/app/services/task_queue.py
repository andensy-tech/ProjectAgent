"""
Background Task Queue — light-weight async task execution for long-running skills.

Design:
  - In-memory asyncio.Queue for intra-worker task dispatch
  - DB-backed (BackgroundTask table) for cross-worker status/results sharing
  - Gunicorn workers are process-isolated; the DB provides ACID-guaranteed
    shared state so any worker can answer status queries for any task.
  - Fallback-safe: if DB is unavailable, falls back to in-memory only.

Usage:
    task_queue = BackgroundTaskWorker()
    task_queue.start()
    task_id = await task_queue.submit(handler, {"key": "val"}, user_id=1)
    status = await task_queue.get_status(task_id)
    ok = await task_queue.cancel(task_id)
"""

import asyncio
import json
import time
import uuid
import logging
from typing import Callable, Dict, List, Optional, Any, Awaitable
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Task status constants
STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

# 终态集合（完成回调只在这几种状态下触发）
_TERMINAL_STATUSES = (STATUS_DONE, STATUS_FAILED, STATUS_CANCELLED)

# Max concurrent background tasks
_MAX_CONCURRENT = 12

# update_progress 落库最小间隔（秒），避免高频进度打爆 DB
_PROGRESS_PERSIST_INTERVAL = 10.0

# 进程内全局单例：任务提交 / 执行 / 状态查询 / 完成回调统一走同一个 worker。
# 此前 engine / ci_build_tool / api.tasks 各持有一个独立实例（独立队列），
# "按 task_id 注册完成回调" 必须落在执行任务的那个实例上，否则永远不触发
# （2026-09-30 飞书后台任务完成推送踩过的坑）。收敛为单例后注册必然命中。
_worker_singleton: Optional["BackgroundTaskWorker"] = None
_worker_singleton_lock: Optional[asyncio.Lock] = None


async def get_worker() -> "BackgroundTaskWorker":
    """返回本进程的全局 BackgroundTaskWorker（惰性创建并启动）。"""
    global _worker_singleton, _worker_singleton_lock
    if _worker_singleton is not None and _worker_singleton.is_running:
        return _worker_singleton
    if _worker_singleton_lock is None:
        _worker_singleton_lock = asyncio.Lock()
    async with _worker_singleton_lock:
        if _worker_singleton is None or not _worker_singleton.is_running:
            _worker_singleton = BackgroundTaskWorker(max_concurrent=_MAX_CONCURRENT)
            _worker_singleton.start()
            logger.info("Global BackgroundTaskWorker initialized (max_concurrent=%d)",
                        _MAX_CONCURRENT)
    return _worker_singleton


class BackgroundTaskWorker:
    """Light-weight background task executor using asyncio.Queue.

    Results are persisted to the BackgroundTask DB table so that any
    gunicorn worker can query the status of any task via get_status().
    """

    def __init__(self, max_concurrent: int = _MAX_CONCURRENT):
        self._queue: "asyncio.Queue[Optional[Dict]]" = asyncio.Queue()
        self._max_concurrent = max_concurrent
        self._tasks: Dict[str, "asyncio.Task"] = {}       # task_id → running Task
        self._results: Dict[str, Dict] = {}                 # task_id → final result (in-memory cache)
        self._progress: Dict[str, str] = {}                 # task_id → current progress msg
        self._status: Dict[str, str] = {}                   # task_id → status string
        self._last_progress_persist: Dict[str, float] = {}  # task_id → 上次进度落库时间
        self._created_at: Dict[str, float] = {}             # task_id → timestamp
        # 完成回调注册表：task_id → async callable(result_dict)
        # 任务到达终态后由 worker 触发（飞书等推送制渠道的送达挂载点）
        self._callbacks: Dict[str, Callable[[Dict[str, Any]], Awaitable[None]]] = {}
        self._lock = asyncio.Lock()
        self._worker_task: Optional[asyncio.Task] = None
        self._consumer_tasks: List[asyncio.Task] = []

    def start(self):
        """Start the background worker coroutine."""
        if self._worker_task is not None:
            return  # already started
        self._worker_task = asyncio.create_task(self._run(), name="bg-task-worker")
        logger.info("BackgroundTaskWorker started (max_concurrent=%d)", self._max_concurrent)

    async def stop(self, timeout: float = 10.0):
        """Stop the background worker gracefully."""
        if self._worker_task is None:
            return
        for _ in range(self._max_concurrent):
            await self._queue.put(None)
        try:
            await asyncio.wait_for(self._worker_task, timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("BackgroundTaskWorker stop timeout, cancelling...")
            self._worker_task.cancel()
        self._worker_task = None
        logger.info("BackgroundTaskWorker stopped")

    async def submit(self, handler, params: Dict[str, Any],
                     user_id: Optional[int] = None,
                     skill_name: str = "") -> str:
        """Submit a task to the background queue.

        Persists a pending record to BackgroundTask table so that
        other workers can see the task exists.

        Args:
            handler: The async callable to execute.
            params: Parameters dict passed to the handler.
            user_id: Optional user ID for context.
            skill_name: Skill name for identification.

        Returns:
            task_id string for tracking.
        """
        task_id = str(uuid.uuid4())
        async with self._lock:
            self._status[task_id] = STATUS_PENDING
            self._created_at[task_id] = time.time()
            self._progress[task_id] = "任务已提交，等待执行..."
            self._results[task_id] = {}

        # Persist to DB so cross-worker queries work
        await self._save_to_db(task_id, skill_name, user_id)

        await self._queue.put({
            "task_id": task_id,
            "handler": handler,
            "params": params,
            "user_id": user_id,
            "submitted_at": time.time(),
        })
        logger.info("Task %s submitted (queue_size=~%d)", task_id, self._queue.qsize())
        return task_id

    async def get_status(self, task_id: str) -> Dict[str, Any]:
        """Get task status and result.

        Priority: in-memory (same worker) → DB (cross-worker) → pending fallback.
        """
        async with self._lock:
            if task_id in self._status:
                status = self._status.get(task_id, STATUS_FAILED)
                result = self._results.get(task_id, {})
                progress = self._progress.get(task_id, "")
                created = self._created_at.get(task_id, 0.0)
                elapsed = time.time() - created if created else 0.0
                return {
                    "task_id": task_id,
                    "status": status,
                    "progress": progress,
                    "result": result if status in (STATUS_DONE, STATUS_FAILED, STATUS_CANCELLED) else None,
                    "elapsed_seconds": round(elapsed, 1),
                }

        # Fallback: query DB (cross-worker, ACID-safe)
        db_record = await self._load_from_db(task_id)
        if db_record:
            return db_record

        # Not found anywhere — return pending instead of falsely reporting failed
        return {
            "task_id": task_id,
            "status": STATUS_PENDING,
            "progress": "等待执行结果...",
            "result": None,
            "elapsed_seconds": 0.0,
        }

    async def update_progress(self, task_id: str, message: str,
                              persist: bool = False) -> None:
        """Update the progress message of a running task.

        纯增量方法，不影响既有调用方。内存中的 progress 即时更新；
        persist=True 时（或距上次落库超过 _PROGRESS_PERSIST_INTERVAL 秒）
        同步写 BackgroundTask 表，供其他 gunicorn worker 轮询到最新进度。
        """
        async with self._lock:
            if task_id not in self._status:
                return
            self._progress[task_id] = message

        now = time.time()
        last = self._last_progress_persist.get(task_id, 0.0)
        if persist or (now - last) >= _PROGRESS_PERSIST_INTERVAL:
            self._last_progress_persist[task_id] = now
            await self._save_to_db(task_id)

    async def cancel(self, task_id: str) -> bool:
        """Cancel a running or pending task."""
        async with self._lock:
            current = self._status.get(task_id)
            if current in (STATUS_DONE, STATUS_FAILED, STATUS_CANCELLED):
                return False
            self._status[task_id] = STATUS_CANCELLED
            self._progress[task_id] = "任务已被取消"

            running_task = self._tasks.get(task_id)
            if running_task and not running_task.done():
                running_task.cancel()

        await self._persist_task_result(task_id, "cancelled")
        logger.info("Task %s cancelled", task_id)
        return True

    async def get_active_task_ids(self) -> List[str]:
        """Return list of non-terminal task IDs."""
        async with self._lock:
            return [tid for tid, s in self._status.items()
                    if s in (STATUS_PENDING, STATUS_RUNNING)]

    # ── 完成回调（推送制渠道：飞书等后台任务完成送达） ──────────────────

    async def set_completion_callback(
            self, task_id: str,
            callback: Callable[[Dict[str, Any]], Awaitable[None]]) -> None:
        """注册任务完成回调（进程内）。

        - 任务未完成：存入注册表，到达终态后由 worker 触发
        - 任务已完成：立即调用（注册与完成存在竞态时的兜底）

        回调只消费任务结果 dict，异常由 worker 兜底，绝不影响任务本身。
        注意：必须在**提交该任务的同一进程**注册（队列是进程内的，
        跨进程注册不会被触发）—— 用全局 ``get_worker()`` 即可保证。
        """
        async with self._lock:
            if self._status.get(task_id) in _TERMINAL_STATUSES:
                result = self._results.get(task_id, {})
                fire_now = True
            else:
                self._callbacks[task_id] = callback
                fire_now = False
        if fire_now:
            await self._safe_invoke_callback(task_id, callback, result)

    async def _fire_completion_callbacks(self, task_id: str) -> None:
        """任务到达终态后触发并清理注册表（异常只记日志）。"""
        async with self._lock:
            if self._status.get(task_id) not in _TERMINAL_STATUSES:
                return
            callback = self._callbacks.pop(task_id, None)
            result = self._results.get(task_id, {})
        if callback is not None:
            await self._safe_invoke_callback(task_id, callback, result)

    async def _safe_invoke_callback(self, task_id: str,
                                    callback: Callable[[Dict[str, Any]], Awaitable[None]],
                                    result: Dict[str, Any]) -> None:
        """调用完成回调，异常只记日志（送达环节绝不能反噬任务结果）。"""
        try:
            await callback(result)
            logger.info("Completion callback fired for task %s", task_id)
        except Exception:
            logger.warning("Completion callback for task %s failed", task_id,
                           exc_info=True)

    # ── DB Persistence (cross-worker shared state) ──────────────────────

    async def _save_to_db(self, task_id: str, skill_name: str = "",
                          user_id: Optional[int] = None):
        """Insert or update BackgroundTask row."""
        try:
            from app.db.session import AsyncSessionLocal
            from app.models.background_task import BackgroundTask

            async with AsyncSessionLocal() as db:
                existing = await db.get(BackgroundTask, task_id)
                if existing:
                    existing.status = self._status.get(task_id, STATUS_PENDING)
                    existing.progress = self._progress.get(task_id, "")
                    existing.elapsed_seconds = round(
                        time.time() - self._created_at.get(task_id, time.time()), 1
                    ) if self._created_at.get(task_id) else 0.0
                    result = self._results.get(task_id)
                    if result is not None:
                        existing.set_result(result)
                else:
                    record = BackgroundTask(
                        id=task_id,
                        skill_name=skill_name,
                        user_id=user_id or 0,
                        status=self._status.get(task_id, STATUS_PENDING),
                        progress=self._progress.get(task_id, ""),
                    )
                    record.set_result(self._results.get(task_id, {}))
                    db.add(record)

                await db.commit()
        except Exception as e:
            logger.warning("Failed to persist task %s to DB: %s", task_id, e)

    async def _persist_task_result(self, task_id: str, status: str):
        """Write final task result to DB (called from consumer finally)."""
        async with self._lock:
            result = self._results.get(task_id, {})
            progress = self._progress.get(task_id, "")
            created = self._created_at.get(task_id, 0.0)
            elapsed = round(time.time() - created, 1) if created else 0.0

        try:
            from app.db.session import AsyncSessionLocal
            from app.models.background_task import BackgroundTask
            from sqlalchemy import select

            async with AsyncSessionLocal() as db:
                result_rows = await db.execute(
                    select(BackgroundTask).where(BackgroundTask.id == task_id)
                )
                record = result_rows.scalar_one_or_none()
                if record is None:
                    record = BackgroundTask(id=task_id, user_id=0)
                    db.add(record)

                record.status = status
                record.progress = progress
                record.elapsed_seconds = elapsed
                record.finished_at = datetime.now(timezone.utc)
                record.set_result(result)
                await db.commit()
        except Exception as e:
            logger.warning("Failed to persist task result %s to DB: %s", task_id, e)
        finally:
            # 触发完成回调（消费方：飞书等推送制渠道）。
            # 只在终态触发；取消路径（cancel() 直接调用本方法）同样覆盖。
            await self._fire_completion_callbacks(task_id)

    @staticmethod
    async def _load_from_db(task_id: str) -> Optional[Dict]:
        """Load task status from DB (cross-worker read)."""
        try:
            from app.db.session import AsyncSessionLocal
            from app.models.background_task import BackgroundTask

            async with AsyncSessionLocal() as db:
                record = await db.get(BackgroundTask, task_id)
                if record is None:
                    return None
                return {
                    "task_id": record.id,
                    "status": record.status,
                    "progress": record.progress,
                    "result": record.get_result(),
                    "elapsed_seconds": record.elapsed_seconds,
                }
        except Exception as e:
            logger.warning("Failed to load task %s from DB: %s", task_id, e)
            return None

    @staticmethod
    async def _cleanup_old_tasks(max_age_seconds: int = 86400):
        """Remove DB records older than max_age_seconds (daily cleanup)."""
        try:
            from app.db.session import AsyncSessionLocal
            from app.models.background_task import BackgroundTask
            from sqlalchemy import delete
            from datetime import datetime, timezone, timedelta

            cutoff = datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)
            async with AsyncSessionLocal() as db:
                await db.execute(
                    delete(BackgroundTask).where(BackgroundTask.created_at < cutoff)
                )
                await db.commit()
        except Exception as e:
            logger.warning("Background task cleanup error: %s", e)

    # ── Internal ──

    async def _run(self):
        """Main worker loop: consume queue and dispatch to concurrent consumers."""
        consumers = []
        for i in range(self._max_concurrent):
            t = asyncio.create_task(self._consumer(i), name=f"bg-consumer-{i}")
            consumers.append(t)
            self._consumer_tasks.append(t)

        await asyncio.gather(*consumers, return_exceptions=True)

    async def _consumer(self, idx: int):
        """Consumer: pull tasks from queue, execute, store results."""
        while True:
            item = await self._queue.get()
            if item is None:  # shutdown sentinel
                self._queue.task_done()
                break

            task_id = item["task_id"]
            handler = item["handler"]
            params = item["params"]
            user_id = item.get("user_id")

            final_status = STATUS_FAILED  # fallback

            try:
                # Check if cancelled before starting
                async with self._lock:
                    if self._status.get(task_id) == STATUS_CANCELLED:
                        self._queue.task_done()
                        continue
                    self._status[task_id] = STATUS_RUNNING
                    self._progress[task_id] = "任务执行中..."
                await self._save_to_db(task_id, user_id=user_id)

                # Execute the skill (potential blocking call)
                result = await handler(params, {"_user_id": user_id, "_background_task": True})

                async with self._lock:
                    if self._status.get(task_id) != STATUS_CANCELLED:
                        if isinstance(result, dict) and result.get("error"):
                            final_status = STATUS_FAILED
                            self._results[task_id] = result
                            self._progress[task_id] = (
                                result.get("message") or str(result.get("error", "执行失败"))
                            )
                        else:
                            final_status = STATUS_DONE
                            self._results[task_id] = (
                                result if isinstance(result, dict) else {"data": result}
                            )
                            self._progress[task_id] = "任务完成"
                        self._status[task_id] = final_status

            except asyncio.CancelledError:
                final_status = STATUS_CANCELLED
                async with self._lock:
                    self._status[task_id] = STATUS_CANCELLED
                    self._progress[task_id] = "任务被取消"

            except Exception as e:
                logger.error("Task %s failed: %s", task_id, e)
                final_status = STATUS_FAILED
                async with self._lock:
                    self._status[task_id] = STATUS_FAILED
                    self._results[task_id] = {"error": str(e)}
                    self._progress[task_id] = f"执行出错: {str(e)}"

            finally:
                await self._persist_task_result(task_id, final_status)
                self._queue.task_done()

    @property
    def is_running(self) -> bool:
        return self._worker_task is not None and not self._worker_task.done()
