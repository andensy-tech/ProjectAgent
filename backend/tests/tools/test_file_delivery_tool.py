"""文件回传工具测试（app/tools/file_delivery_tool.py）。

用户场景：在 chat 里说「把 XXX 文件发给我」——文件已存在于服务器，本轮没有任何工具
产出它，需要 deliver_file 定位文件并交给引擎渲染下载卡片。

覆盖：按路径 / 按文件名（精确同名优先、模糊兜底、最新优先、数量上限）/ 未找到 /
参数缺失 / 非法路径不抛异常 / 与引擎文件提取的契约（返回结构能被识别成下载卡片）。
"""

import os

import pytest

from app.services.file_outputs import describe_file_outputs, extract_file_outputs
from app.tools.file_delivery_tool import deliver_file


def _mk(tmp_path, name, content="data", mtime=None):
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    if mtime:
        os.utime(path, (mtime, mtime))
    return str(path)


@pytest.fixture
def search_in_tmp(monkeypatch, tmp_path):
    """把按文件名查找的根目录限定到 tmp_path（避免扫描真实文件系统）。"""
    monkeypatch.setattr("app.tools.file_delivery_tool._primary_roots", lambda: [str(tmp_path)])
    monkeypatch.setattr("app.tools.file_delivery_tool._fallback_roots", lambda: [])
    return tmp_path


class TestDeliverByPath:
    """按 file_path 发送（已知道确切路径）。"""

    @pytest.mark.asyncio
    async def test_deliver_existing_file(self, tmp_path):
        path = _mk(tmp_path, "report.xlsx", "xlsx-bytes")

        result = await deliver_file({"file_path": path}, {})

        assert result["success"] is True
        assert result["files"] == [{
            "file_path": path, "file_name": "report.xlsx", "size": len("xlsx-bytes"),
        }]
        assert "不要再让用户自己去服务器找路径" in result["message"]

    @pytest.mark.asyncio
    async def test_relative_path_resolved(self, tmp_path, monkeypatch):
        _mk(tmp_path, "rel.html")
        monkeypatch.chdir(tmp_path)

        result = await deliver_file({"file_path": "rel.html"}, {})

        assert result["success"] is True
        assert result["files"][0]["file_name"] == "rel.html"

    @pytest.mark.asyncio
    async def test_missing_path_reports_error(self, tmp_path):
        result = await deliver_file({"file_path": str(tmp_path / "nope.xlsx")}, {})

        assert result["success"] is False
        assert "不存在" in result["error"]

    @pytest.mark.asyncio
    async def test_directory_path_rejected(self, tmp_path):
        result = await deliver_file({"file_path": str(tmp_path)}, {})

        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_invalid_path_does_not_raise(self, tmp_path):
        """NUL 字节等非法路径 → 返回可读错误而不是抛异常。"""
        result = await deliver_file({"file_path": "/tmp/evil\x00.xlsx"}, {})

        assert result["success"] is False
        assert "错误" in result["error"] or "失败" in result["error"]


class TestDeliverByName:
    """按 file_name 查找发送（用户只给了文件名——本次 bug 的真实场景）。"""

    @pytest.mark.asyncio
    async def test_find_by_exact_name(self, search_in_tmp):
        path = _mk(search_in_tmp, "R+_Wave2_Wave3_Patch.xlsx")

        result = await deliver_file({"file_name": "R+_Wave2_Wave3_Patch.xlsx"}, {})

        assert result["success"] is True
        assert [f["file_path"] for f in result["files"]] == [path]

    @pytest.mark.asyncio
    async def test_exact_match_preferred_over_partial(self, search_in_tmp):
        _mk(search_in_tmp, "report_2026_09_04.html")   # 模糊命中
        exact = _mk(search_in_tmp, "report.html")      # 精确命中

        result = await deliver_file({"file_name": "report.html"}, {})

        assert [f["file_path"] for f in result["files"]] == [exact]

    @pytest.mark.asyncio
    async def test_partial_match_fallback(self, search_in_tmp):
        path = _mk(search_in_tmp, "weekly_report_2026_09_04.html")

        result = await deliver_file({"file_name": "weekly_report"}, {})

        assert [f["file_path"] for f in result["files"]] == [path]

    @pytest.mark.asyncio
    async def test_same_name_copies_deduped_to_newest(self, search_in_tmp):
        """同名多份副本（如 /tmp 与项目目录各一份）只发最新的一个，避免多张同名卡片。"""
        os.makedirs(search_in_tmp / "sub", exist_ok=True)
        _mk(search_in_tmp, "dup.xlsx", "old", mtime=1_600_000_000)
        new = _mk(search_in_tmp, "sub/dup.xlsx", "new", mtime=1_700_000_000)

        result = await deliver_file({"file_name": "dup.xlsx"}, {})

        assert [f["file_path"] for f in result["files"]] == [new]

    @pytest.mark.asyncio
    async def test_different_names_all_returned(self, search_in_tmp):
        """模糊匹配到不同文件名时按最新优先返回多个（受 max_files 限制）。"""
        os.makedirs(search_in_tmp / "sub", exist_ok=True)
        old = _mk(search_in_tmp, "weekly_report_a.html", "a", mtime=1_600_000_000)
        new = _mk(search_in_tmp, "sub/weekly_report_b.html", "b", mtime=1_700_000_000)

        result = await deliver_file({"file_name": "weekly_report"}, {})
        assert [f["file_path"] for f in result["files"]] == [new, old]

        capped = await deliver_file({"file_name": "weekly_report", "max_files": 1}, {})
        assert [f["file_path"] for f in capped["files"]] == [new]

    @pytest.mark.asyncio
    async def test_not_found_lists_searched_roots(self, search_in_tmp):
        result = await deliver_file({"file_name": "不存在_xyz.xlsx"}, {})

        assert result["success"] is False
        assert "未找到文件" in result["error"]
        assert str(search_in_tmp) in result["error"]  # 如实告知搜索位置

    @pytest.mark.asyncio
    async def test_max_files_clamped(self, search_in_tmp):
        """max_files 超上限/非法值被安全夹取，不抛异常。"""
        for i in range(3):
            _mk(search_in_tmp, f"many_{i}.xlsx")

        r1 = await deliver_file({"file_name": "many_", "max_files": 999}, {})
        assert len(r1["files"]) == 3

        r2 = await deliver_file({"file_name": "many_", "max_files": "bad"}, {})
        assert r2["success"] is True


class TestDeliverFileValidation:
    """参数校验。"""

    @pytest.mark.asyncio
    async def test_requires_path_or_name(self):
        result = await deliver_file({}, {})
        assert result["success"] is False
        assert "file_path" in result["error"] and "file_name" in result["error"]

    @pytest.mark.asyncio
    async def test_none_params(self):
        result = await deliver_file(None, {})
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_blank_strings(self):
        result = await deliver_file({"file_path": "   ", "file_name": ""}, {})
        assert result["success"] is False


class TestEngineContract:
    """与引擎文件提取的契约：deliver_file 的返回必须能被识别成下载卡片。"""

    @pytest.mark.asyncio
    async def test_result_is_recognized_as_downloadable(self, tmp_path):
        path = _mk(tmp_path, "contract.xlsx")
        result = await deliver_file({"file_path": path}, {})

        assert [f["file_path"] for f in extract_file_outputs(result)] == [path]

        described = describe_file_outputs(result, user_id=1)
        assert described[0]["download_url"].startswith("/api/files/download?token=")

    @pytest.mark.asyncio
    async def test_error_result_yields_no_file(self, tmp_path):
        result = await deliver_file({"file_path": str(tmp_path / "gone.xlsx")}, {})

        assert extract_file_outputs(result) == []
