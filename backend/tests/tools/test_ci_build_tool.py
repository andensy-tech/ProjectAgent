"""内置工具 ci_build_query / ci_build_download 测试。

mock 配置读取与管线，不触达 Google API / FTPS / 真实后台队列。
用户场景：
- PM 在 chat 问「CHIPX1 最新构建是什么」→ ci_build_query 返回 build id
- PM 说「下载最新 userdebug 构建并传 FTP」→ ci_build_download 提交后台任务
"""

import pytest

from app.tools.ci_build_tool import ci_build_query, ci_build_download


def _context(user_id: int = 1) -> dict:
    return {"_user_id": user_id}


def _cfg_dict(**overrides) -> dict:
    cfg = {
        "branch": "android-u-tv-test",
        "target": "CHIPX1",
        "ota_flavor": "URT2",
        "default_build_type": "userdebug",
        "auth": {"enabled": False},
        "ftp": {"host": "ftp.example.com", "port": 21, "username": "u",
                "password": "p", "remote_dir": "/root"},
    }
    cfg.update(overrides)
    return cfg


class TestCiBuildQuery:
    @pytest.mark.asyncio
    async def test_query_latest_success(self, monkeypatch):
        from app.tools import ci_build_tool

        async def fake_raw(uid):
            return _cfg_dict()
        monkeypatch.setattr(ci_build_tool, "get_ci_build_config_raw", fake_raw)

        build = {"build_id": "12345678", "release_candidate_name": "URT2.260924.001",
                 "signed": True, "creation_timestamp_ms": "2026-09-24T19:29:58.633Z",
                 "build_date": "2026-09-24"}
        monkeypatch.setattr(
            "app.services.ci_build.query_latest_build",
            lambda *a, **k: _coro(build),
        )
        monkeypatch.setattr(
            "app.services.ci_build.list_artifacts",
            lambda *a, **k: ["CHIPX1-ota-12345678.zip"],
        )

        result = await ci_build_query({}, _context())
        assert result["success"] is True
        assert "12345678" in result["message"]
        assert result["data"]["build_id"] == "12345678"

    @pytest.mark.asyncio
    async def test_query_missing_user(self):
        result = await ci_build_query({}, {})
        assert result["type"] == "error"

    @pytest.mark.asyncio
    async def test_query_no_build_configured(self, monkeypatch):
        """未配置 branch/target 时返回引导错误（不绕过配置）。"""
        from app.tools import ci_build_tool

        async def fake_raw(uid):
            return {}
        monkeypatch.setattr(ci_build_tool, "get_ci_build_config_raw", fake_raw)

        result = await ci_build_query({}, _context())
        assert result["type"] == "error"
        assert "设置页" in result["error"]

    @pytest.mark.asyncio
    async def test_query_with_build_id_lists_artifacts(self, monkeypatch):
        from app.tools import ci_build_tool

        async def fake_raw(uid):
            return _cfg_dict()
        monkeypatch.setattr(ci_build_tool, "get_ci_build_config_raw", fake_raw)
        monkeypatch.setattr(
            "app.services.ci_build.list_artifacts",
            lambda *a, **k: ["signed/signed-CHIPX1-img-12345678.zip", "otatools.zip"],
        )

        result = await ci_build_query({"build_id": "12345678"}, _context())
        assert result["success"] is True
        assert "signed/signed-CHIPX1-img-12345678.zip" in result["message"]
        assert result["data"]["artifacts"][1] == "otatools.zip"


class TestResolveQueryTarget:
    """build_type=all 不是合法 target 后缀，查询需映射为代表类型。"""

    def _cfg(self, target="CHIPX1"):
        from app.services.ci_build import config_from_dict
        return config_from_dict({"target": target})

    def test_all_maps_to_userdebug(self):
        from app.tools.ci_build_tool import _resolve_query_target
        assert _resolve_query_target(self._cfg(), "", "all") == "CHIPX1-userdebug"

    def test_user_and_userdebug(self):
        from app.tools.ci_build_tool import _resolve_query_target
        assert _resolve_query_target(self._cfg(), "", "user") == "CHIPX1-user"
        assert _resolve_query_target(self._cfg(), "", "userdebug") == "CHIPX1-userdebug"

    def test_full_target_passthrough(self):
        """已带后缀的完整 target 原样使用（不被 all 覆盖）。"""
        from app.tools.ci_build_tool import _resolve_query_target
        assert _resolve_query_target(self._cfg(), "CHIPX1-user", "all") == "CHIPX1-user"

    def test_param_overrides_config(self):
        from app.tools.ci_build_tool import _resolve_query_target
        assert _resolve_query_target(self._cfg(), "OTHER", "userdebug") == "OTHER-userdebug"

    def test_empty_when_unconfigured(self):
        from app.tools.ci_build_tool import _resolve_query_target
        assert _resolve_query_target(self._cfg(target=""), "", "all") == ""


class TestCiBuildQueryAllType:
    @pytest.mark.asyncio
    async def test_query_with_all_type_uses_userdebug_target(self, monkeypatch):
        """设置页选 ALL 后查询最新构建：target 用 userdebug 代表，不出现 -all。"""
        from app.tools import ci_build_tool

        captured = {}

        async def fake_raw(uid):
            return _cfg_dict(default_build_type="all")
        monkeypatch.setattr(ci_build_tool, "get_ci_build_config_raw", fake_raw)

        async def fake_query(cfg, user_id, target=None):
            captured["target"] = target
            return {"build_id": "12345678", "release_candidate_name": "",
                    "signed": False, "creation_timestamp_ms": "",
                    "build_date": "2026-09-24"}
        monkeypatch.setattr("app.services.ci_build.query_latest_build", fake_query)
        monkeypatch.setattr("app.services.ci_build.list_artifacts", lambda *a, **k: [])

        result = await ci_build_query({}, _context())
        assert result["success"] is True
        assert captured["target"] == "CHIPX1-userdebug"
        assert "-all" not in captured["target"]


class TestCiBuildDownload:
    @pytest.mark.asyncio
    async def test_submit_background_task(self, monkeypatch):
        from app.tools import ci_build_tool

        async def fake_raw(uid):
            return _cfg_dict()
        monkeypatch.setattr(ci_build_tool, "get_ci_build_config_raw", fake_raw)

        submitted = {}

        class FakeWorker:
            is_running = True
            async def submit(self, handler, params, user_id, skill_name):
                submitted["params"] = params
                submitted["skill_name"] = skill_name
                return "task-123"

        # worker 已收敛为 task_queue 全局单例：patch 模块级 get_worker
        # （ci_build_tool._get_worker 在调用时 late-import 它）
        from app.services import task_queue
        async def fake_get_worker():
            return FakeWorker()
        monkeypatch.setattr(task_queue, "get_worker", fake_get_worker)

        result = await ci_build_download({"build_id": "12345678"}, _context())
        assert result["type"] == "task_submitted"
        assert result["task_id"] == "task-123"
        assert submitted["skill_name"] == "ci_build_download"
        assert submitted["params"]["build_id"] == "12345678"

    @pytest.mark.asyncio
    async def test_fail_fast_when_ftp_missing(self, monkeypatch):
        from app.tools import ci_build_tool

        async def fake_raw(uid):
            cfg = _cfg_dict()
            cfg["ftp"] = {"host": "", "port": 21, "username": "", "password": "", "remote_dir": "/"}
            return cfg
        monkeypatch.setattr(ci_build_tool, "get_ci_build_config_raw", fake_raw)

        result = await ci_build_download({}, _context())
        assert result["type"] == "error"
        assert "设置页" in result["error"]

    @pytest.mark.asyncio
    async def test_fail_fast_when_no_token(self, monkeypatch, tmp_path):
        """私有分支无 token 时直接提示跑授权脚本，不提交后台任务。

        注意 patch 目标是 `app.services.ci_build.config.token_cache_path`
        （build_auth 在 config 模块命名空间解析该函数），而非包级重导出；
        否则真实环境存在 token 文件时本用例会环境依赖地失败。
        """
        from app.tools import ci_build_tool

        async def fake_raw(uid):
            cfg = _cfg_dict()
            cfg["auth"] = {"enabled": True, "client_id": "cid",
                           "client_secret": "sec", "project_id": ""}
            return cfg
        monkeypatch.setattr(ci_build_tool, "get_ci_build_config_raw", fake_raw)

        # token cache 指向不存在的路径（config 模块是实际解析点）
        monkeypatch.setattr(
            "app.services.ci_build.config.token_cache_path",
            lambda uid: tmp_path / "none.json",
        )

        result = await ci_build_download({}, _context())
        assert result["type"] == "error"
        assert "ci_build_auth" in result["error"]

    @pytest.mark.asyncio
    async def test_invalid_artifacts_param(self, monkeypatch):
        from app.tools import ci_build_tool

        async def fake_raw(uid):
            return _cfg_dict()
        monkeypatch.setattr(ci_build_tool, "get_ci_build_config_raw", fake_raw)

        result = await ci_build_download({"artifacts": "not-a-list"}, _context())
        assert result["type"] == "error"

    @pytest.mark.asyncio
    async def test_all_build_type_accepted(self, monkeypatch):
        """build_type=all 提交后台任务（前置校验放行）。"""
        from app.tools import ci_build_tool

        async def fake_raw(uid):
            return _cfg_dict(default_build_type="all")
        monkeypatch.setattr(ci_build_tool, "get_ci_build_config_raw", fake_raw)

        submitted = {}

        class FakeWorker:
            is_running = True
            async def submit(self, handler, params, user_id, skill_name):
                submitted.update(params)
                return "task-all"

        from app.services import task_queue
        async def fake_get_worker():
            return FakeWorker()
        monkeypatch.setattr(task_queue, "get_worker", fake_get_worker)

        result = await ci_build_download({}, _context())
        assert result["type"] == "task_submitted"
        assert submitted["build_type"] == "all"

    @pytest.mark.asyncio
    async def test_invalid_build_type_fail_fast(self, monkeypatch):
        """非法 build_type 在提交前就报错，不产生注定失败的后台任务。"""
        from app.tools import ci_build_tool

        async def fake_raw(uid):
            return _cfg_dict()
        monkeypatch.setattr(ci_build_tool, "get_ci_build_config_raw", fake_raw)

        result = await ci_build_download({"build_type": "bogus"}, _context())
        assert result["type"] == "error"
        assert "bogus" in result["error"]

    @pytest.mark.asyncio
    async def test_download_task_error_surfaces(self, monkeypatch):
        """后台任务体：管线抛 CiBuildError → 结果带 error（任务标记失败）。"""
        from app.tools import ci_build_tool
        from app.services.ci_build import CiBuildError

        async def fake_raw(uid):
            return _cfg_dict()
        monkeypatch.setattr(ci_build_tool, "get_ci_build_config_raw", fake_raw)
        monkeypatch.setattr(
            "app.services.ci_build.run_fetch_pipeline",
            lambda *a, **k: _raise(CiBuildError("构建不存在")),
        )

        result = await ci_build_tool._run_download_task(
            {"build_id": "1"}, _context(), lambda m: None,
        )
        assert result["success"] is False
        assert "构建不存在" in result["error"]


class TestCompletionDelivery:
    """任务完成后的送达：写入会话历史 + 站内消息。

    背景：真实使用中 26 分钟的下载任务完成后，用户没收到任何提醒——
    因为结果只走前端轮询，刷新/切会话后轮询即死。送达必须服务端主动完成。
    """

    _summary = {
        "success": True,
        "build_id": "16453281",
        "build_type": "userdebug",
        "ftp_base": "/OEM-A_Gtv/wave3/2026-09-26-12345678",
        "ok_count": 2,
        "fail_count": 0,
        "artifacts": [{"artifact": "a.zip", "status": "ok"}, {"artifact": "b.zip", "status": "ok"}],
        "message": "✅ 构建 16453281 上传 FTP 完成（2/2 个产物）\n\n| 产物 | FTP 路径 |\n|---|---|\n| a.zip | /ftp/a.zip |",
    }

    @pytest.mark.asyncio
    async def test_writes_message_into_conversation(self, db_session):
        """结果写入会话 → 刷新/换设备后仍能在聊天历史看到。"""
        from sqlalchemy import select

        from app.models.conversation import Conversation
        from app.models.conversation import Message
        from app.tools.ci_build_tool import _deliver_completion

        conv = Conversation(user_id=1, title="下载构建")
        db_session.add(conv)
        await db_session.commit()
        await db_session.refresh(conv)

        await _deliver_completion(1, conv.id, self._summary)

        rows = (await db_session.execute(
            select(Message).where(Message.conversation_id == conv.id)
        )).scalars().all()
        assert len(rows) == 1
        assert rows[0].role == "assistant"
        assert "16453281" in rows[0].content
        assert "a.zip" in rows[0].content
        assert rows[0].extra_data.get("source") == "background_task"

    @pytest.mark.asyncio
    async def test_creates_in_app_notification(self, db_session):
        """发站内消息（铃铛）→ 用户不在该会话时也能收到提醒。"""
        from sqlalchemy import select

        from app.models.notification import Notification
        from app.tools.ci_build_tool import _deliver_completion

        await _deliver_completion(1, None, self._summary)

        rows = (await db_session.execute(
            select(Notification).where(Notification.user_id == 1,
                                       Notification.type == "ci_build")
        )).scalars().all()
        assert len(rows) == 1
        assert "16453281" in rows[0].title
        assert len(rows[0].title) <= 200  # 模型 String(200) 约束
        assert rows[0].read is False

    @pytest.mark.asyncio
    async def test_never_raises_on_bad_input(self, db_session):
        """送达环节失败绝不能影响任务本身（不抛异常）。"""
        from app.tools.ci_build_tool import _deliver_completion

        # 不存在的会话 ID + 空 message
        await _deliver_completion(1, 999999, self._summary)
        await _deliver_completion(1, None, {})
        await _deliver_completion(1, None, {"message": "", "ok_count": 0})

    @pytest.mark.asyncio
    async def test_failure_summary_notifies(self, db_session):
        """失败任务的送达也带失败语义。"""
        from sqlalchemy import select

        from app.models.notification import Notification
        from app.tools.ci_build_tool import _deliver_completion

        await _deliver_completion(1, None, {
            "success": False, "build_id": "1", "ok_count": 0, "fail_count": 1,
            "artifacts": [{"artifact": "a.zip", "status": "upload_failed"}],
            "message": "❌ 构建 1 上传 FTP 失败（0/1 个产物）",
        })
        rows = (await db_session.execute(
            select(Notification).where(Notification.user_id == 1,
                                       Notification.type == "ci_build")
        )).scalars().all()
        assert len(rows) == 1
        assert "上传结束" in rows[0].title


def _coro(value):
    async def _c(*a, **k):
        return value
    return _c()


def _raise(exc):
    async def _c(*a, **k):
        raise exc
    return _c()
