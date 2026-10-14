"""CI 构建服务包测试（config / auth token cache / 管线编排）。

网络（Google API / FTPS）全部 mock，不触达真实外部服务。
用户场景：
- 设置页保存 branch/target/FTP/OAuth 配置后，chat 查询最新构建
- chat 下发「下载最新 userdebug 构建并传 FTP」，后台任务跑管线
"""

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from app.services.ci_build import (
    config_from_dict,
    token_cache_path,
    storage_dir,
)
from app.services.ci_build.auth import TokenCache, GoogleAuth, AuthRequiredError, NoAuth
from app.services.ci_build.pipeline import _format_build_date, run_fetch_pipeline, query_latest_build, CiBuildError


# ── 配置解析与校验 ──────────────────────────────────────────────────────────

class TestCiBuildConfig:
    def test_defaults_when_empty(self):
        cfg = config_from_dict({})
        assert cfg.branch == ""
        assert cfg.target == ""
        assert cfg.default_build_type == "userdebug"
        assert cfg.auth.enabled is True
        assert cfg.ftp.port == 21

    def test_from_full_dict(self):
        cfg = config_from_dict({
            "branch": "b1", "target": "CHIPX1", "ota_flavor": "URT2",
            "default_build_type": "user",
            "auth": {"enabled": False},
            "ftp": {"host": "h", "port": "990", "username": "u",
                    "password": "p", "remote_dir": "/r"},
        })
        assert cfg.branch == "b1"
        assert cfg.ftp.port == 990  # 字符串端口也能解析
        assert cfg.auth.enabled is False

    def test_invalid_port_falls_back(self):
        cfg = config_from_dict({"ftp": {"port": "abc"}})
        assert cfg.ftp.port == 21

    def test_validate_query_requires_branch_or_target(self):
        cfg = config_from_dict({})
        assert cfg.validate_query() is not None
        cfg2 = config_from_dict({"branch": "b1"})
        assert cfg2.validate_query() is None

    def test_validate_download_requires_ftp(self):
        cfg = config_from_dict({"branch": "b1", "target": "T", "auth": {"enabled": False}})
        assert "FTP" in cfg.validate_download()
        cfg2 = config_from_dict({
            "branch": "b1", "target": "T",
            "auth": {"enabled": False},
            "ftp": {"host": "h", "username": "u"},
        })
        assert cfg2.validate_download() is None

    def test_validate_download_private_branch_requires_oauth(self):
        cfg = config_from_dict({
            "target": "T",
            "ftp": {"host": "h", "username": "u"},
        })
        assert "OAuth" in cfg.validate_download()


# ── Token cache ─────────────────────────────────────────────────────────────

class TestTokenCache:
    def test_roundtrip(self, tmp_path):
        cache = TokenCache(str(tmp_path / "token.json"))
        assert cache.load() is None
        token = {"access_token": "a", "refresh_token": "r",
                 "expiry": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}
        cache.save(token)
        assert cache.load()["access_token"] == "a"

    def test_expiry_check(self, tmp_path):
        cache = TokenCache(str(tmp_path / "t.json"))
        expired = {"access_token": "a", "expiry": "2020-01-01T00:00:00+00:00"}
        assert cache.is_expired(expired)
        assert cache.needs_refresh(expired)

    def test_permissions_600(self, tmp_path):
        cache = TokenCache(str(tmp_path / "t.json"))
        cache.save({"access_token": "a"})
        mode = (tmp_path / "t.json").stat().st_mode & 0o777
        assert mode == 0o600


class TestGoogleAuth:
    def test_no_valid_token_raises(self, tmp_path):
        auth = GoogleAuth("cid", "sec", str(tmp_path / "none.json"))
        with pytest.raises(AuthRequiredError):
            auth.get_headers()

    def test_refresh_success(self, tmp_path, monkeypatch):
        """过期 token 用 refresh_token 自动续期（mock token endpoint）。"""
        import httpx

        auth = GoogleAuth("cid", "sec", str(tmp_path / "t.json"))
        old = {"access_token": "old", "refresh_token": "rr",
               "expiry": "2020-01-01T00:00:00+00:00"}
        auth.cache.save(old)

        class FakeResp:
            status_code = 200
            def raise_for_status(self): pass
            def json(self):
                return {"access_token": "new", "expires_in": 3600,
                        "token_type": "Bearer"}

        monkeypatch.setattr(httpx, "post", lambda *a, **k: FakeResp())
        headers = auth.get_headers()
        assert headers["Authorization"] == "Bearer new"
        assert auth.cache.load()["refresh_token"] == "rr"

    def test_no_auth_headers(self):
        assert NoAuth().get_headers() == {"Accept": "application/json"}


# ── 日期子目录推导 ──────────────────────────────────────────────────────────

class TestBuildDate:
    def test_iso_timestamp(self):
        assert _format_build_date("2026-09-24T19:29:58.633Z", "12345678") == "2026-09-24-12345678"

    def test_ms_timestamp(self):
        ms = str(int(datetime(2026, 3, 15).timestamp() * 1000))
        assert _format_build_date(ms, "123") == "2026-03-15-123"

    def test_fallback_today(self):
        assert _format_build_date("", "123") == f"{datetime.now().strftime('%Y-%m-%d')}-123"

    def test_invalid_fallback(self):
        assert _format_build_date("not-a-date", "7") == f"{datetime.now().strftime('%Y-%m-%d')}-7"


# ── 用户数据目录隔离 ────────────────────────────────────────────────────────

class TestUserDirs:
    def test_token_path_per_user(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.services.ci_build.config.ci_build_data_dir",
                            lambda: tmp_path)
        p1, p2 = token_cache_path(1), token_cache_path(2)
        assert p1 != p2
        assert "token_1.json" in str(p1)

    def test_storage_per_user(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.services.ci_build.config.ci_build_data_dir",
                            lambda: tmp_path)
        s1 = storage_dir(1)
        assert s1.exists()
        assert str(s1).endswith("storage/1")


# ── 管线（mock 网络层）─────────────────────────────────────────────────────

def _cfg() -> "CiBuildConfig":
    return config_from_dict({
        "branch": "b1", "target": "CHIPX1",
        "auth": {"enabled": False},
        "ftp": {"host": "ftp.example.com", "port": 21, "username": "u",
                "password": "p", "remote_dir": "/root"},
    })


class TestRunFetchPipeline:
    @pytest.mark.asyncio
    async def test_download_and_upload_flow(self, tmp_path, monkeypatch):
        """完整管线：解析 2 个产物 → 下载 → 上传，结果汇总正确。"""
        from app.services.ci_build import pipeline

        monkeypatch.setattr(pipeline, "storage_dir", lambda uid: tmp_path / "storage")

        build = {"build_id": "12345678", "release_candidate_name": "",
                 "signed": False, "creation_timestamp_ms": "2026-09-24T19:29:58.633Z"}
        monkeypatch.setattr(pipeline, "query_latest_build",
                            lambda *a, **k: _async(build))

        monkeypatch.setattr(pipeline, "resolve_artifacts",
                            lambda *a, **k: ["CHIPX1-ota-12345678.zip", "otatools.zip"])

        # mock fetcher：写两个假文件
        class FakeFetcher:
            def __init__(self, out, auth): self.out = out
            def download(self, build_id, target, artifact, project_id=""):
                d = Path(self.out) / build_id / target
                d.mkdir(parents=True, exist_ok=True)
                f = d / Path(artifact).name
                f.write_bytes(b"x" * 100)
                return True, str(f)
        monkeypatch.setattr(pipeline, "ArtifactFetcher", FakeFetcher)

        uploaded = []
        class FakeUploader:
            def __init__(self, host, port, user, pwd, remote_dir):
                self.remote_dir = remote_dir
            def upload(self, local_path, max_retries=3):
                uploaded.append((self.remote_dir, Path(local_path).name))
                return True
        monkeypatch.setattr(pipeline, "FTUploader", FakeUploader)

        progress_msgs = []
        summary = await run_fetch_pipeline(
            _cfg(), user_id=1, build_type="userdebug",
            progress_cb=progress_msgs.append,
        )

        assert summary["success"] is True
        assert summary["build_id"] == "12345678"
        assert summary["ok_count"] == 2
        # FTP 目录结构：{remote}/{date}-{build_id}/{type}
        assert uploaded[0][0] == "/root/2026-09-24-12345678/userdebug"
        art = summary["artifacts"][0]
        assert art["status"] == "ok"
        assert art["ftp_path"].startswith("/root/2026-09-24-12345678/userdebug/")
        # 刻意不用 file_path 键（避免被 file_outputs 识别为可回传文件）
        assert "local_file" in art and "file_path" not in art
        assert any("下载" in m for m in progress_msgs)

    @pytest.mark.asyncio
    async def test_local_cache_cleaned_after_upload(self, tmp_path, monkeypatch):
        """上传成功后清理本地下载数据；目录清空后被移除（本地不留数据）。"""
        from app.services.ci_build import pipeline

        storage = tmp_path / "storage"
        monkeypatch.setattr(pipeline, "storage_dir", lambda uid: storage)

        build = {"build_id": "888", "release_candidate_name": "",
                 "signed": False, "creation_timestamp_ms": "2026-09-24T19:29:58.633Z"}
        monkeypatch.setattr(pipeline, "query_latest_build", lambda *a, **k: _async(build))
        monkeypatch.setattr(pipeline, "resolve_artifacts", lambda *a, **k: ["a.zip", "b.zip"])

        downloaded = []
        class FakeFetcher:
            def __init__(self, out, auth): pass
            def download(self, build_id, target, artifact, project_id=""):
                d = storage / build_id / target
                d.mkdir(parents=True, exist_ok=True)
                f = d / Path(artifact).name
                f.write_bytes(b"x" * 100)
                downloaded.append(f)
                return True, str(f)
        monkeypatch.setattr(pipeline, "ArtifactFetcher", FakeFetcher)

        uploaded_ok = {"a.zip"}
        class FakeUploader:
            def __init__(self, *a, **k): pass
            def upload(self, local_path, max_retries=3):
                return Path(local_path).name in uploaded_ok
        monkeypatch.setattr(pipeline, "FTUploader", FakeUploader)

        summary = await run_fetch_pipeline(_cfg(), user_id=1)

        # 上传成功的 a.zip 已删除，上传失败的 b.zip 保留（便于排查）
        by_name = {Path(d).name: d for d in downloaded}
        assert not Path(by_name["a.zip"]).exists()
        assert Path(by_name["b.zip"]).exists()

        ok_art = next(r for r in summary["artifacts"] if r["artifact"] == "a.zip")
        fail_art = next(r for r in summary["artifacts"] if r["artifact"] == "b.zip")
        assert ok_art["local_cleaned"] is True and ok_art["local_file"] == ""
        assert fail_art["local_cleaned"] is False and fail_art["local_file"]

        # 目录非空（b.zip 还在）→ 构建目录保留
        assert (storage / "888").exists()

    @pytest.mark.asyncio
    async def test_empty_build_dir_pruned(self, tmp_path, monkeypatch):
        """全部上传成功 → 本地构建目录被整体清除。"""
        from app.services.ci_build import pipeline

        storage = tmp_path / "storage"
        monkeypatch.setattr(pipeline, "storage_dir", lambda uid: storage)
        build = {"build_id": "999", "release_candidate_name": "",
                 "signed": False, "creation_timestamp_ms": ""}
        monkeypatch.setattr(pipeline, "query_latest_build", lambda *a, **k: _async(build))
        monkeypatch.setattr(pipeline, "resolve_artifacts", lambda *a, **k: ["a.zip"])

        class FakeFetcher:
            def __init__(self, out, auth): pass
            def download(self, build_id, target, artifact, project_id=""):
                d = storage / build_id / target
                d.mkdir(parents=True, exist_ok=True)
                f = d / Path(artifact).name
                f.write_bytes(b"x")
                return True, str(f)
        monkeypatch.setattr(pipeline, "ArtifactFetcher", FakeFetcher)

        class FakeUploader:
            def __init__(self, *a, **k): pass
            def upload(self, local_path, max_retries=3): return True
        monkeypatch.setattr(pipeline, "FTUploader", FakeUploader)

        summary = await run_fetch_pipeline(_cfg(), user_id=1)
        assert summary["ok_count"] == 1
        assert not (storage / "999").exists()


class TestResultMessage:
    """任务完成后回聊天的结果文本（上传路径 + 清单）。"""

    def test_success_message_has_paths_and_manifest(self):
        from app.services.ci_build.pipeline import _build_result_message

        summary = {
            "ok_count": 2, "build_id": "12345678", "build_type": "userdebug",
            "build_date": "2026-09-24", "release_candidate_name": "URT2.260924.001",
            "ftp_base": "/root/2026-09-24-12345678",
            "artifacts": [
                {"artifact": "CHIPX1-ota-12345678.zip", "status": "ok", "size": 1024,
                 "ftp_path": "/root/2026-09-24-12345678/userdebug/CHIPX1-ota-12345678.zip",
                 "local_file": "", "local_cleaned": True},
                {"artifact": "otatools.zip", "status": "ok", "size": 2048,
                 "ftp_path": "/root/2026-09-24-12345678/userdebug/otatools.zip",
                 "local_file": "", "local_cleaned": True},
            ],
        }
        msg = _build_result_message(summary, [])

        assert "✅" in msg
        assert "12345678" in msg
        # FTP 路径与清单都要出现
        assert "/root/2026-09-24-12345678/userdebug/CHIPX1-ota-12345678.zip" in msg
        assert "CHIPX1-ota-12345678.zip" in msg and "otatools.zip" in msg
        assert "FTP 根目录" in msg
        assert "本地下载缓存已清理" in msg
        # 不回传下载卡片（无 file_path 键）
        assert "file_path" not in msg

    def test_partial_failure_message_lists_failed(self):
        from app.services.ci_build.pipeline import _build_result_message

        failed = [{"artifact": "b.zip", "status": "upload_failed", "local_file": "/tmp/b.zip"}]
        summary = {
            "ok_count": 1, "build_id": "1", "build_type": "user", "build_date": "",
            "release_candidate_name": "", "ftp_base": "/r/1",
            "artifacts": [
                {"artifact": "a.zip", "status": "ok", "size": 10,
                 "ftp_path": "/r/1/user/a.zip", "local_file": "", "local_cleaned": True},
                *failed,
            ],
        }
        msg = _build_result_message(summary, failed)
        assert "⚠️" in msg
        assert "失败项" in msg and "b.zip" in msg
        assert "保留在服务器本地" in msg

    def test_all_failed_message(self):
        from app.services.ci_build.pipeline import _build_result_message

        failed = [{"artifact": "a.zip", "status": "download_failed", "local_file": ""}]
        msg = _build_result_message(
            {"ok_count": 0, "build_id": "7", "build_type": "user", "build_date": "",
             "release_candidate_name": "", "ftp_base": "/r", "artifacts": failed},
            failed,
        )
        assert "❌" in msg
        assert "下载失败" in msg

    def test_human_size(self):
        from app.services.ci_build.pipeline import _human_size
        assert _human_size(0) == "0 B"
        assert _human_size(512) == "512 B"
        assert _human_size(1536) == "1.5 KB"
        assert _human_size(5 * 1024 ** 3) == "5.0 GB"

    @pytest.mark.asyncio
    async def test_all_failed_marks_error(self, tmp_path, monkeypatch):
        from app.services.ci_build import pipeline
        monkeypatch.setattr(pipeline, "storage_dir", lambda uid: tmp_path / "s")
        build = {"build_id": "1", "release_candidate_name": "",
                 "signed": False, "creation_timestamp_ms": ""}
        monkeypatch.setattr(pipeline, "query_latest_build",
                            lambda *a, **k: _async(build))
        monkeypatch.setattr(pipeline, "resolve_artifacts",
                            lambda *a, **k: ["a.zip"])

        class FailFetcher:
            def __init__(self, out, auth): pass
            def download(self, *a, **k): return False, None
        monkeypatch.setattr(pipeline, "ArtifactFetcher", FailFetcher)

        summary = await run_fetch_pipeline(_cfg(), user_id=1)
        assert summary["success"] is False
        assert "error" in summary

    @pytest.mark.asyncio
    async def test_invalid_build_type(self):
        with pytest.raises(CiBuildError):
            await run_fetch_pipeline(_cfg(), user_id=1, build_type="bogus")

    @pytest.mark.asyncio
    async def test_config_error(self):
        cfg = config_from_dict({})
        with pytest.raises(CiBuildError) as exc_info:
            await run_fetch_pipeline(cfg, user_id=1)
        assert "设置页" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_query_latest_no_build(self, monkeypatch):
        from app.services.ci_build import pipeline
        monkeypatch.setattr(pipeline, "get_latest_good_build", lambda *a, **k: None)
        with pytest.raises(CiBuildError) as exc_info:
            await query_latest_build(_cfg(), user_id=1)
        assert "未找到成功构建" in str(exc_info.value)


def _async(value):
    """包一个值为 coroutine（mock async 函数用）。"""
    async def _coro(*args, **kwargs):
        return value
    return _coro()
