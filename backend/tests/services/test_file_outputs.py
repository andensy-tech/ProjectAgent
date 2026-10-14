"""Agent 产出文件提取测试（app/services/file_outputs.py）。

覆盖真实结果形态：
  - 脚本技能（script_runner 归一化后 file_path 落在 data 里，weekly_report 实际输出）
  - 技能结果顶层 file_path
  - 内置文档写入工具（write_xlsx / write_docx → type=document + content=路径）
  - files 列表 / 重复路径去重 / 不存在的文件 / 出错结果 / 非 dict 结果
"""

import os

from app.core.security import verify_file_token
from app.services.file_outputs import describe_file_outputs, extract_file_outputs


def _mk_file(tmp_path, name="report.html", content="<html>report</html>"):
    f = tmp_path / name
    f.write_text(content, encoding="utf-8")
    return str(f)


class TestExtractFileOutputs:
    """从各类结果结构中识别生成文件。"""

    def test_skill_result_file_in_data(self, tmp_path):
        """脚本技能：file_path 在 data 中（weekly_report 的真实输出形态）。"""
        path = _mk_file(tmp_path, "weekly.html")
        result = {
            "type": "success",
            "title": "报告已生成",
            "data": {"success": True, "message": "报告已生成", "file_path": path, "skill_name": "weekly_report"},
            "message": "报告已生成",
            "error": None,
        }

        files = extract_file_outputs(result)

        assert len(files) == 1
        assert files[0]["file_path"] == path
        assert files[0]["file_name"] == "weekly.html"
        assert files[0]["skill_name"] == "weekly_report"
        assert files[0]["size"] == os.path.getsize(path)

    def test_skill_result_file_at_top_level(self, tmp_path):
        """技能结果顶层 file_path（脚本输出未经归一化的形态）。"""
        path = _mk_file(tmp_path, "top.xlsx")
        files = extract_file_outputs({"success": True, "file_path": path, "skill_name": "exporter"})

        assert [f["file_path"] for f in files] == [path]
        assert files[0]["skill_name"] == "exporter"

    def test_builtin_write_tool_result(self, tmp_path):
        """内置文档工具：type=document + tool=write_xlsx + content=写入路径。"""
        path = _mk_file(tmp_path, "out.xlsx")
        result = {
            "type": "document",
            "tool": "write_xlsx",
            "content": path,
            "metadata": {"file_name": "out.xlsx", "file_size": 10},
            "error": None,
        }

        files = extract_file_outputs(result)

        assert [f["file_path"] for f in files] == [path]

    def test_read_tool_result_is_not_a_file_output(self, tmp_path):
        """读取类工具（read_xlsx）的 content 是正文而非产出文件，不应被当作可下载文件。"""
        path = _mk_file(tmp_path, "in.xlsx")
        result = {"type": "document", "tool": "read_xlsx", "content": path, "metadata": {}}

        assert extract_file_outputs(result) == []

    def test_files_list_and_dedup(self, tmp_path):
        """files 列表被识别；同一路径多形态出现只保留一条。"""
        path = _mk_file(tmp_path, "a.xlsx")
        result = {"files": [{"file_path": path}], "data": {"file_path": path}}

        files = extract_file_outputs(result)

        assert [f["file_path"] for f in files] == [path]

    def test_missing_file_ignored(self, tmp_path):
        """路径不存在（被删/写失败）→ 不产出下载卡片。"""
        result = {"data": {"file_path": str(tmp_path / "gone.xlsx")}}

        assert extract_file_outputs(result) == []

    def test_error_result_ignored(self, tmp_path):
        """出错结果即使带路径也不回传文件。"""
        path = _mk_file(tmp_path, "e.xlsx")
        assert extract_file_outputs({"error": "boom", "file_path": path}) == []

    def test_non_dict_result(self):
        """非 dict 结果安全返回空列表。"""
        assert extract_file_outputs("plain text") == []
        assert extract_file_outputs(None) == []

    def test_skill_name_fallback_from_caller(self, tmp_path):
        """结果里没有 skill_name 时用调用方传入的技能名兜底。"""
        path = _mk_file(tmp_path, "f.xlsx")
        files = extract_file_outputs({"data": {"file_path": path}}, skill_name="weekly_report")

        assert files[0]["skill_name"] == "weekly_report"

    def test_multiple_files_in_list(self, tmp_path):
        """一次产出多个文件 → 全部返回（前端逐个渲染下载卡片）。"""
        a = _mk_file(tmp_path, "a.xlsx")
        b = _mk_file(tmp_path, "b.html")
        files = extract_file_outputs({"data": {"files": [{"file_path": a}, {"file_path": b}]}})

        assert sorted(f["file_name"] for f in files) == ["a.xlsx", "b.html"]

    def test_data_list_of_dicts(self, tmp_path):
        """data 本身是 [{file_path: ...}] 列表的形态。"""
        path = _mk_file(tmp_path, "c.xlsx")
        files = extract_file_outputs({"data": [{"file_path": path, "skill_name": "s"}]})

        assert [f["file_path"] for f in files] == [path]
        assert files[0]["skill_name"] == "s"

    def test_output_path_key_alias(self, tmp_path):
        """技能用 output_path 表达产出文件时同样识别。"""
        path = _mk_file(tmp_path, "d.html")
        files = extract_file_outputs({"success": True, "output_path": path})

        assert [f["file_path"] for f in files] == [path]

    def test_directory_path_ignored(self, tmp_path):
        """目录不是可下载文件。"""
        assert extract_file_outputs({"data": {"file_path": str(tmp_path)}}) == []

    def test_invalid_path_does_not_raise(self, tmp_path):
        """NUL 字节/非法路径来自 LLM 生成的参数，必须静默跳过而不是打断引擎循环。"""
        assert extract_file_outputs({"data": {"file_path": "/tmp/evil\x00.html"}}) == []
        assert extract_file_outputs({"data": {"file_path": "\x00"}}) == []

    def test_relative_path_resolved_from_cwd(self, tmp_path, monkeypatch):
        """相对路径按进程工作目录解析（技能/工具写相对路径时也能回传）。"""
        (tmp_path / "rel.xlsx").write_text("x", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        files = extract_file_outputs({"data": {"file_path": "rel.xlsx"}})

        assert [f["file_path"] for f in files] == [str(tmp_path / "rel.xlsx")]

    def test_symlink_resolved_to_target(self, tmp_path):
        """软链接归一化为真实文件（下载时按真实文件返回）。"""
        real = _mk_file(tmp_path, "real.html")
        link = tmp_path / "link.html"
        link.symlink_to(real)

        files = extract_file_outputs({"data": {"file_path": str(link)}})

        assert [f["file_path"] for f in files] == [real]


class TestDescribeFileOutputs:
    """下载描述（含签名下载链接）。"""

    def test_download_url_is_signed(self, tmp_path):
        """描述里带可校验的签名下载链接（绑定用户）。"""
        path = _mk_file(tmp_path, "report.html")
        result = {"data": {"file_path": path, "skill_name": "weekly_report"}}

        described = describe_file_outputs(result, user_id=7)

        assert len(described) == 1
        url = described[0]["download_url"]
        assert url.startswith("/api/files/download?token=")
        token = url.split("token=", 1)[1]
        assert verify_file_token(token, 7) == path
        assert verify_file_token(token, 8) is None  # 其它用户不可用

    def test_no_user_id_no_url(self, tmp_path):
        """无用户上下文（后台/系统调用）时不生成下载链接。"""
        path = _mk_file(tmp_path, "report.html")

        described = describe_file_outputs({"data": {"file_path": path}}, user_id=None)

        assert "download_url" not in described[0]
