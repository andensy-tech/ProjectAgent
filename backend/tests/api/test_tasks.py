"""
API integration tests for background task endpoints (app/api/tasks.py).

  POST /api/tasks/submit              — submit a skill to the background queue
  GET  /api/tasks/{task_id}           — poll task status
  POST /api/tasks/{task_id}/cancel    — cancel a task

The real task_submit endpoint logic (skill lookup, per-user param injection,
task_id return, 404 handling) runs as-is; only the background queue itself is
replaced by a lightweight fake so tests don't spin up worker threads that
outlive the pytest event loop.
"""

import pytest

from app.services.agent.executor import action_executor


class _FakeQueue:
    """Minimal in-memory queue matching BackgroundTaskWorker's public API."""

    def __init__(self):
        self._tasks = {}
        self._n = 0

    async def submit(self, handler, params, user_id, skill_name):
        self._n += 1
        tid = f"task-{self._n}"
        # Execute handler inline (async) to simulate a quick completion
        result = await handler(params, {"_user_id": user_id})
        self._tasks[tid] = {"status": "completed", "result": result}
        return tid

    async def get_status(self, task_id):
        t = self._tasks.get(task_id)
        if t:
            return {"task_id": task_id, "status": t["status"], "result": t["result"],
                    "progress": "", "elapsed_seconds": 0.0}
        # 真实 worker 对未知任务返回 pending 而非 404
        return {"task_id": task_id, "status": "pending", "result": None,
                "progress": "", "elapsed_seconds": 0.0}

    async def cancel(self, task_id):
        if task_id in self._tasks:
            self._tasks[task_id]["status"] = "cancelled"
        # 真实 worker 对未知任务也返回 True（标记 cancelled）
        return True


@pytest.fixture
def fake_task_queue(monkeypatch):
    queue = _FakeQueue()

    async def _get_queue():
        return queue

    monkeypatch.setattr("app.api.tasks._get_queue", _get_queue)
    return queue


def _register_skill(name, handler):
    action_executor.register_skill(name, handler)


class TestTaskSubmit:
    """POST /api/tasks/submit."""

    @pytest.mark.asyncio
    async def test_submit_and_poll(self, async_client, fake_task_queue):
        """提交技能后轮询状态返回 task_id 一致。"""
        async def fake_handler(params, context):
            return {"ok": True}

        _register_skill("test-task-skill", fake_handler)

        resp = await async_client.post(
            "/api/tasks/submit",
            json={"skill_name": "test-task-skill", "params": {"k": "v"}},
        )
        assert resp.status_code == 200
        task_id = resp.json()["task_id"]
        assert resp.json()["status"] == "pending"

        status = await async_client.get(f"/api/tasks/{task_id}")
        assert status.status_code == 200
        assert status.json()["task_id"] == task_id

    @pytest.mark.asyncio
    async def test_submit_injects_user_id(self, async_client, fake_task_queue):
        """提交时向 params 注入 _user_id（真实编排逻辑）。"""
        captured = {}

        async def spy_handler(params, context):
            captured["params"] = params
            return {"ok": True}

        _register_skill("spy-skill", spy_handler)

        resp = await async_client.post(
            "/api/tasks/submit", json={"skill_name": "spy-skill", "params": {}},
        )
        assert resp.status_code == 200
        assert captured["params"].get("_user_id") == 1  # seeded admin

    @pytest.mark.asyncio
    async def test_submit_unknown_skill(self, async_client, fake_task_queue):
        """提交不存在的技能返回 404。"""
        resp = await async_client.post(
            "/api/tasks/submit",
            json={"skill_name": "no-such-skill", "params": {}},
        )
        assert resp.status_code == 404


class TestTaskStatus:
    """GET /api/tasks/{task_id}."""

    @pytest.mark.asyncio
    async def test_status_unknown_task(self, async_client, fake_task_queue):
        """未知任务返回 pending 状态（真实 worker 语义）。"""
        resp = await async_client.get("/api/tasks/does-not-exist")
        assert resp.status_code == 200
        assert resp.json()["status"] == "pending"

    @pytest.mark.asyncio
    async def test_status_exposes_downloadable_files(self, async_client, fake_task_queue, tmp_path):
        """技能产出文件 → 任务结果带 files[] 签名下载链接，且该链接可直接下载。

        复现"技能面板执行 → 轮询 → 下载产出文件"的真实链路。
        """
        report = tmp_path / "weekly_report_2026_09_15.html"
        report.write_text("<html>周报</html>", encoding="utf-8")

        async def report_handler(params, context):
            return {
                "type": "success",
                "title": "报告已生成",
                "data": {"success": True, "message": "报告已生成",
                         "file_path": str(report), "skill_name": "file-skill"},
                "message": "报告已生成",
                "error": None,
            }

        _register_skill("file-skill", report_handler)

        submit = await async_client.post(
            "/api/tasks/submit", json={"skill_name": "file-skill", "params": {}},
        )
        task_id = submit.json()["task_id"]

        status = (await async_client.get(f"/api/tasks/{task_id}")).json()
        files = status["result"]["files"]
        assert files[0]["file_name"] == "weekly_report_2026_09_15.html"
        assert files[0]["skill_name"] == "file-skill"

        download = await async_client.get(files[0]["download_url"])
        assert download.status_code == 200
        assert download.text == "<html>周报</html>"

    @pytest.mark.asyncio
    async def test_status_exposes_multiple_files(self, async_client, fake_task_queue, tmp_path):
        """一次产出多个文件 → files[] 全部返回且各自可下载。"""
        xlsx = tmp_path / "export.xlsx"
        xlsx.write_text("xlsx-bytes", encoding="utf-8")
        html = tmp_path / "export.html"
        html.write_text("html-bytes", encoding="utf-8")

        async def multi_handler(params, context):
            return {
                "success": True,
                "data": {"files": [{"file_path": str(xlsx)}, {"file_path": str(html)}]},
                "message": "导出完成",
            }

        _register_skill("multi-file-skill", multi_handler)

        submit = await async_client.post(
            "/api/tasks/submit", json={"skill_name": "multi-file-skill", "params": {}},
        )
        status = (await async_client.get(f"/api/tasks/{submit.json()['task_id']}")).json()

        files = status["result"]["files"]
        assert sorted(f["file_name"] for f in files) == ["export.html", "export.xlsx"]
        for f in files:
            assert (await async_client.get(f["download_url"])).status_code == 200

    @pytest.mark.asyncio
    async def test_status_failed_task_with_path_has_no_files(self, async_client, fake_task_queue, tmp_path):
        """失败任务的 result 里即便带路径也不给下载链接。"""
        ghost = tmp_path / "ghost.xlsx"
        ghost.write_text("x", encoding="utf-8")

        async def failing_handler(params, context):
            return {"error": "导出失败", "file_path": str(ghost)}

        _register_skill("failing-file-skill", failing_handler)

        submit = await async_client.post(
            "/api/tasks/submit", json={"skill_name": "failing-file-skill", "params": {}},
        )
        status = (await async_client.get(f"/api/tasks/{submit.json()['task_id']}")).json()

        assert "files" not in status["result"]

    @pytest.mark.asyncio
    async def test_status_without_file_has_no_files_key(self, async_client, fake_task_queue):
        """无产出文件的任务不额外附带 files 字段。"""
        async def plain_handler(params, context):
            return {"ok": True, "message": "完成"}

        _register_skill("plain-skill", plain_handler)

        submit = await async_client.post(
            "/api/tasks/submit", json={"skill_name": "plain-skill", "params": {}},
        )
        task_id = submit.json()["task_id"]

        status = (await async_client.get(f"/api/tasks/{task_id}")).json()
        assert "files" not in status["result"]


class TestTaskCancel:
    """POST /api/tasks/{task_id}/cancel."""

    @pytest.mark.asyncio
    async def test_cancel_task(self, async_client, fake_task_queue):
        """提交后取消，返回 cancelled。"""
        async def fake_handler(params, context):
            return {"ok": True}

        _register_skill("cancel-skill", fake_handler)

        resp = await async_client.post(
            "/api/tasks/submit", json={"skill_name": "cancel-skill", "params": {}},
        )
        task_id = resp.json()["task_id"]

        cancel = await async_client.post(f"/api/tasks/{task_id}/cancel")
        assert cancel.status_code == 200
        assert cancel.json()["status"] == "cancelled"
