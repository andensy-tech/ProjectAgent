"""
Async Task Manager — background execution of long-running skills.

Manages asyncio.Tasks for skill execution, stores results, and optionally
invokes completion callbacks for pushing results to Feishu or other channels.
"""

import asyncio
import uuid
import logging
from typing import Dict, Optional, Callable, Any

logger = logging.getLogger(__name__)


class AsyncTaskManager:
    """Manages background skill executions with result push callbacks."""

    def __init__(self):
        self._tasks: Dict[str, asyncio.Task] = {}
        self._results: Dict[str, Dict] = {}
        self._callbacks: Dict[str, Callable] = {}

    _MAX_RESULTS = 100  # max completed results to retain

    async def _cleanup_old_results(self):
        """Remove oldest completed/failed results when over limit."""
        if len(self._results) <= self._MAX_RESULTS:
            return
        # Remove oldest completed/failed entries (keep running tasks)
        completed = [tid for tid, r in self._results.items()
                     if r.get("status") in ("completed", "failed")]
        completed.sort()  # oldest first by UUID prefix
        to_remove = completed[:len(self._results) - self._MAX_RESULTS]
        for tid in to_remove:
            self._results.pop(tid, None)

    async def submit(
        self,
        skill_name: str,
        params: dict,
        context: dict,
        executor,
    ) -> str:
        """Submit a skill for background execution.

        Args:
            skill_name: Skill to execute
            params: Skill parameters
            context: Execution context
            executor: ActionExecutor instance

        Returns:
            task_id for tracking and callback registration
        """
        task_id = str(uuid.uuid4())
        self._results[task_id] = {"status": "running", "skill_name": skill_name}
        await self._cleanup_old_results()

        task = asyncio.create_task(
            self._run(task_id, skill_name, params, context, executor)
        )
        self._tasks[task_id] = task
        task.add_done_callback(lambda t: self._tasks.pop(task_id, None))
        logger.info("Background task %s submitted for skill '%s'", task_id, skill_name)
        return task_id

    async def _run(self, task_id: str, skill_name: str, params: dict, context: dict, executor):
        """Execute skill and invoke callback on completion."""
        try:
            result = await executor._execute_skill(skill_name, params, context)
            self._results[task_id] = {"status": "completed", "result": result, "skill_name": skill_name}
            cb = self._callbacks.pop(task_id, None)
            if cb:
                await cb(result)
            # Remove completed result with callback (caller already notified)
            if cb:
                self._results.pop(task_id, None)
        except Exception as e:
            logger.error("Async skill %s failed: %s", skill_name, e, exc_info=True)
            error_result = {"error": str(e), "success": False}
            self._results[task_id] = {"status": "failed", "result": error_result, "skill_name": skill_name}
            cb = self._callbacks.pop(task_id, None)
            if cb:
                await cb(error_result)
            if cb:
                self._results.pop(task_id, None)

    async def set_callback(self, task_id: str, callback: Callable) -> bool:
        """Register a completion callback for a running task.

        Thread-safe on asyncio: the callback is invoked from the same event loop.
        If the task has already completed, invokes callback immediately.

        Args:
            task_id: Task ID from submit()
            callback: Async callable(result_dict) called on completion

        Returns:
            True if the task was found, False otherwise
        """
        result = self._results.get(task_id)
        if not result:
            return False

        if result.get("status") in ("completed", "failed"):
            # Task already done — invoke immediately
            cb_result = result.get("result", {})
            try:
                await callback(cb_result)
            except Exception as e:
                logger.error("Callback for completed task %s failed: %s", task_id, e)
        else:
            # Task still running — store for later invocation
            self._callbacks[task_id] = callback

        return True

    def get_result(self, task_id: str) -> Optional[Dict]:
        """Get the result dict for a task, or None if not found."""
        return self._results.get(task_id)

    def get_task_status(self, task_id: str) -> Optional[str]:
        """Get task status: running/completed/failed/None."""
        result = self._results.get(task_id)
        return result.get("status") if result else None


# Global singleton
async_task_manager = AsyncTaskManager()
