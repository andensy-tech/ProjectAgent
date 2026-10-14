"""BackgroundTaskWorker 完成回调机制（2026-09-30 飞书后台任务完成送达）。

覆盖：
  - 任务未完成时注册 → 到达终态后触发
  - 任务已完成时注册 → 立即调用（注册/完成竞态兜底）
  - 回调抛异常 → 只记日志，不影响任务终态
  - 失败 / 取消任务同样触发

注意：``db_engine`` fixture 必须参与（函数级隔离临时库），否则
``_persist_task_result`` 会写生产库。
"""

import asyncio

import pytest

from app.services.task_queue import (
    BackgroundTaskWorker,
    STATUS_CANCELLED,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_RUNNING,
)

_MAX_WAIT = 5.0   # 轮询任务终态/回调触发的最长秒数
_POLL = 0.02


def _make_callback(sink: list):
    async def _cb(result: dict) -> None:
        sink.append(result)
    return _cb


async def _wait_until(predicate, timeout: float = _MAX_WAIT) -> bool:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(_POLL)
    return predicate()


async def _wait_terminal(worker: BackgroundTaskWorker, task_id: str) -> dict:
    async def _poll():
        return (await worker.get_status(task_id))["status"]
    loop = asyncio.get_event_loop()
    deadline = loop.time() + _MAX_WAIT
    status = STATUS_PENDING
    while loop.time() < deadline:
        status = await _poll()
        if status in (STATUS_DONE, STATUS_FAILED, STATUS_CANCELLED):
            break
        await asyncio.sleep(_POLL)
    return await worker.get_status(task_id)


class TestCompletionCallback:
    """set_completion_callback / 终态触发。"""

    @pytest.mark.asyncio
    async def test_callback_fired_on_completion(self, db_engine):
        worker = BackgroundTaskWorker(max_concurrent=1)
        worker.start()
        try:
            async def _handler(params, ctx):
                return {"message": "任务完成", "success": True}

            fired: list = []
            task_id = await worker.submit(_handler, {})
            await worker.set_completion_callback(task_id, _make_callback(fired))
            status = await _wait_terminal(worker, task_id)
            assert status["status"] == STATUS_DONE
            assert await _wait_until(lambda: bool(fired))
            assert fired[0].get("message") == "任务完成"
        finally:
            await worker.stop()

    @pytest.mark.asyncio
    async def test_callback_registered_after_completion_fires_immediately(self, db_engine):
        """注册晚于完成（竞态兜底）：立即调用，结果可送达。"""
        worker = BackgroundTaskWorker(max_concurrent=1)
        worker.start()
        try:
            async def _handler(params, ctx):
                return {"message": "早已完成"}

            task_id = await worker.submit(_handler, {})
            await _wait_terminal(worker, task_id)

            fired: list = []
            await worker.set_completion_callback(task_id, _make_callback(fired))
            assert len(fired) == 1
            assert fired[0].get("message") == "早已完成"
        finally:
            await worker.stop()

    @pytest.mark.asyncio
    async def test_callback_exception_does_not_affect_task(self, db_engine):
        """回调抛异常 → 只记日志，任务仍是 done（送达环节不能反噬任务）。"""
        worker = BackgroundTaskWorker(max_concurrent=1)
        worker.start()
        try:
            async def _handler(params, ctx):
                return {"message": "ok"}

            async def _bad_cb(result: dict) -> None:
                raise RuntimeError("push channel boom")

            task_id = await worker.submit(_handler, {})
            await worker.set_completion_callback(task_id, _bad_cb)
            status = await _wait_terminal(worker, task_id)
            assert status["status"] == STATUS_DONE
        finally:
            await worker.stop()

    @pytest.mark.asyncio
    async def test_failed_task_also_fires_callback(self, db_engine):
        """handler 返回 error 结果（FAILED 终态）→ 回调同样触发并带上结果。"""
        worker = BackgroundTaskWorker(max_concurrent=1)
        worker.start()
        try:
            async def _handler(params, ctx):
                return {"error": "磁盘满", "message": "上传失败"}

            fired: list = []
            task_id = await worker.submit(_handler, {})
            await worker.set_completion_callback(task_id, _make_callback(fired))
            status = await _wait_terminal(worker, task_id)
            assert status["status"] == STATUS_FAILED
            assert await _wait_until(lambda: bool(fired))
            assert fired[0].get("error") == "磁盘满"
        finally:
            await worker.stop()

    @pytest.mark.asyncio
    async def test_cancelled_task_fires_callback(self, db_engine):
        """取消（CANCELLED 终态）→ 回调同样触发。"""
        worker = BackgroundTaskWorker(max_concurrent=1)
        worker.start()
        try:
            started = asyncio.Event()

            async def _slow(params, ctx):
                started.set()
                await asyncio.sleep(60)
                return {"message": "不应到达"}

            fired: list = []
            task_id = await worker.submit(_slow, {})
            # 等任务进入 RUNNING 再注册 + 取消，保证覆盖运行中取消路径
            loop = asyncio.get_event_loop()
            deadline = loop.time() + _MAX_WAIT
            while loop.time() < deadline:
                if (await worker.get_status(task_id))["status"] == STATUS_RUNNING:
                    break
                await asyncio.sleep(_POLL)

            await worker.set_completion_callback(task_id, _make_callback(fired))
            await worker.cancel(task_id)
            assert await _wait_until(lambda: bool(fired))
        finally:
            await worker.stop()
