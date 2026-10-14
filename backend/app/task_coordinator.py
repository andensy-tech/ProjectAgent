"""
跨进程任务协调器 — 确保后台任务在 gunicorn 多 worker 下只执行一次。

原理：利用文件锁（fcntl.flock）做跨进程互斥。
每个 Worker 在循环中先尝试获取锁，
只有获取到锁的 Worker 执行任务，
其他 Worker 跳过本轮。

用法：
    if task_coordinator.should_run("cl_refresh", interval=3600):
        await do_refresh()
"""

import os
import time
import fcntl
import logging

logger = logging.getLogger(__name__)

_LOCK_DIR = "/tmp/agent-task-locks"


def _ensure_dir():
    os.makedirs(_LOCK_DIR, exist_ok=True)


def should_run(task_name: str, interval: int = 3600) -> bool:
    """Check if a periodic task should run.

    Uses a shared file + fcntl lock so only one worker across all
    gunicorn processes will get True for each interval window.

    Args:
        task_name: Unique name for the task (e.g. "cl_refresh").
        interval: Minimum seconds between executions.

    Returns:
        True if this worker should execute the task (only one worker gets True).
    """
    _ensure_dir()
    state_file = os.path.join(_LOCK_DIR, f"{task_name}.state")
    lock_file = os.path.join(_LOCK_DIR, f"{task_name}.lock")

    try:
        with open(lock_file, "w") as lf:
            # Try non-blocking exclusive lock
            try:
                fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                # Another worker holds the lock — skip
                return False

            # Read last run time
            try:
                with open(state_file, "r") as sf:
                    last_run = float(sf.read().strip())
            except (FileNotFoundError, ValueError):
                last_run = 0.0

            now = time.time()
            if now - last_run >= interval:
                # Claim this interval
                with open(state_file, "w") as sf:
                    sf.write(f"{now}\n")
                return True

            return False
    except Exception as e:
        logger.warning("Task coordinator error for %s: %s", task_name, e)
        # Fail open: let the task run (worst case: redundant execution)
        return True
