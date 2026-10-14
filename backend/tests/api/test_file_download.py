"""API tests for Agent 产出文件下载（app/api/upload.py 的 /api/files/download）。

Agent 生成的文件落在服务端任意路径，前端凭后端签发的签名令牌下载：
  - 合法令牌 → 200 + 文件内容（Content-Disposition 带原文件名）
  - 篡改/过期/他人令牌 → 403
  - 文件已删除 → 404
"""

import pytest

from app.core.security import create_file_token


def _write(tmp_path, name="report.html", content="<html>weekly</html>"):
    f = tmp_path / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(content, encoding="utf-8")
    return str(f)


class TestDownloadGeneratedFile:
    """GET /api/files/download."""

    @pytest.mark.asyncio
    async def test_download_with_valid_token(self, async_client, tmp_path):
        """合法令牌可下载文件（测试基座认证为 admin/user_id=1）。"""
        path = _write(tmp_path)
        token = create_file_token(path, user_id=1)

        resp = await async_client.get(f"/api/files/download?token={token}")

        assert resp.status_code == 200
        assert resp.text == "<html>weekly</html>"
        assert "report.html" in resp.headers.get("content-disposition", "")

    @pytest.mark.asyncio
    async def test_download_file_outside_skills_dir(self, async_client, tmp_path):
        """非技能目录的文件（Agent 写到 /tmp 或用户指定路径）同样可下载。"""
        path = _write(tmp_path / "sub", "export.xlsx", "binary-ish")
        token = create_file_token(path, user_id=1)

        resp = await async_client.get(f"/api/files/download?token={token}")

        assert resp.status_code == 200
        assert resp.text == "binary-ish"

    @pytest.mark.asyncio
    async def test_tampered_token_rejected(self, async_client, tmp_path):
        """篡改令牌 → 403，不泄露任何文件。"""
        path = _write(tmp_path)
        token = create_file_token(path, user_id=1)
        forged = create_file_token("/etc/passwd", user_id=1).rpartition(".")[0]
        tampered = f"{forged}.{token.rpartition('.')[2]}"

        resp = await async_client.get(f"/api/files/download?token={tampered}")

        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_other_user_token_rejected(self, async_client, tmp_path):
        """他人签发的令牌 → 403。"""
        path = _write(tmp_path)
        token = create_file_token(path, user_id=999)

        resp = await async_client.get(f"/api/files/download?token={token}")

        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_expired_token_rejected(self, async_client, tmp_path):
        """过期令牌 → 403（提示重新生成）。"""
        path = _write(tmp_path)
        token = create_file_token(path, user_id=1, expires_in=-1)

        resp = await async_client.get(f"/api/files/download?token={token}")

        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_missing_file_returns_404(self, async_client, tmp_path):
        """令牌合法但文件已被删除 → 404。"""
        path = _write(tmp_path, "gone.xlsx")
        token = create_file_token(path, user_id=1)
        import os
        os.remove(path)

        resp = await async_client.get(f"/api/files/download?token={token}")

        assert resp.status_code == 404

    def test_requires_auth(self, test_client_unauthenticated, tmp_path):
        """未登录请求被拒（下载接口不能被匿名访问）。"""
        path = _write(tmp_path)
        token = create_file_token(path, user_id=1)

        resp = test_client_unauthenticated.get(f"/api/files/download?token={token}")

        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_malformed_tokens_return_403_not_500(self, async_client):
        """畸形令牌（含非 ASCII / 超长 / 空）一律 403，不能把接口打成 500。"""
        for bad in ("abc.中文签名", "x" * 5000 + "." + "y" * 32, "garbage", ".", "payload."):
            resp = await async_client.get("/api/files/download", params={"token": bad})
            assert resp.status_code == 403, f"token={bad[:20]!r} 返回 {resp.status_code}"

    @pytest.mark.asyncio
    async def test_missing_token_param_is_422(self, async_client):
        """缺少 token 参数 → 参数校验失败（422）。"""
        resp = await async_client.get("/api/files/download")

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_directory_path_token_rejected(self, async_client, tmp_path):
        """令牌指向目录 → 404（不能返回目录内容）。"""
        token = create_file_token(str(tmp_path), user_id=1)

        resp = await async_client.get(f"/api/files/download?token={token}")

        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_chinese_filename_preserved(self, async_client, tmp_path):
        """中文文件名下载不报错（FileResponse 的 filename* 编码）。"""
        path = _write(tmp_path, "项目周报.html", "<html>周报</html>")
        token = create_file_token(path, user_id=1)

        resp = await async_client.get(f"/api/files/download?token={token}")

        assert resp.status_code == 200
        assert resp.text == "<html>周报</html>"
