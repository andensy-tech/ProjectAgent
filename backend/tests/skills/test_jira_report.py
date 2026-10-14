"""
Tests for app.skills.jira_report_skill module.

当前实现是 subprocess 适配器：调用 skills/jira-confluence-report 脚本并解析其输出。
- SKR-01~03: _parse_output 解析脚本 stdout
- SKR-04~05: jira_confluence_report_handler 集成（mock 子进程，不发起真实网络调用）
"""

import asyncio

import pytest

from app.skills.jira_report_skill import (
    _parse_output,
    jira_confluence_report_handler,
)


class TestParseOutput:
    """Test _parse_output (SKR-01~03)."""

    def test_parse_full_output(self):
        """SKR-01: 完整 stdout 正确解析 page_id/url/分类统计。"""
        stdout = (
            "📄 704114884\n"
            "🔗 https://confluence.example.com/pages/viewpage.action?pageId=704114884\n"
            "P0: 1, ETA: 20, stale: 3, remaining: 23\n"
            "总计: 46 个Jira\n"
        )
        parsed = _parse_output(stdout)

        assert parsed["page_id"] == "704114884"
        assert parsed["page_url"].startswith("https://confluence.example.com")
        assert parsed["total_issues"] == 46
        assert parsed["p0_count"] == 1
        assert parsed["eta_count"] == 20
        assert parsed["stale_count"] == 3
        assert parsed["remaining_count"] == 23

    def test_parse_empty_output(self):
        """SKR-02: 空输出返回空默认值。"""
        parsed = _parse_output("")
        assert parsed["page_id"] is None
        assert parsed["page_url"] is None
        assert parsed["total_issues"] == 0
        assert parsed["p0_count"] == 0

    def test_parse_partial_output(self):
        """SKR-03: 只有页面 ID、无统计行时，统计归零。"""
        parsed = _parse_output("📄 123456\n")
        assert parsed["page_id"] == "123456"
        assert parsed["total_issues"] == 0
        assert parsed["remaining_count"] == 0


class TestHandler:
    """Test jira_confluence_report_handler (SKR-04~05)."""

    @pytest.mark.asyncio
    async def test_handler_success(self, monkeypatch):
        """SKR-04: 脚本成功输出 → report 类型 + 解析后的统计数据。"""
        stdout = (
            "📄 704114884\n"
            "🔗 https://confluence.example.com/x\n"
            "P0: 0, ETA: 0, stale: 0, remaining: 1\n"
            "总计: 1 个Jira\n"
        )

        class FakeProc:
            returncode = 0

            async def communicate(self):
                return stdout.encode(), b""

        async def fake_create_subprocess_exec(*args, **kwargs):
            return FakeProc()

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

        result = await jira_confluence_report_handler({}, {})

        assert result["type"] == "report"
        assert result["data"]["total_issues"] == 1
        assert result["confluence"]["success"] is True
        assert result["confluence"]["page_id"] == "704114884"
        assert result["actions"][0]["label"] == "查看Confluence报告"

    @pytest.mark.asyncio
    async def test_handler_error_nonzero_exit(self, monkeypatch):
        """SKR-05: 脚本退出码非 0 → error 类型。"""
        class FakeProc:
            returncode = 1

            async def communicate(self):
                return b"", b"boom"

        async def fake_create_subprocess_exec(*args, **kwargs):
            return FakeProc()

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

        result = await jira_confluence_report_handler({}, {})

        assert result["type"] == "error"
        assert "error" in result

    @pytest.mark.asyncio
    async def test_handler_error_unparseable_output(self, monkeypatch):
        """SKR-05b: 脚本输出中无页面 ID → error。"""
        class FakeProc:
            returncode = 0

            async def communicate(self):
                return b"some unexpected output", b""

        async def fake_create_subprocess_exec(*args, **kwargs):
            return FakeProc()

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

        result = await jira_confluence_report_handler({}, {})

        assert result["type"] == "error"
        assert "未能从输出中解析页面ID" in result["error"]

    @pytest.mark.asyncio
    async def test_handler_error_subprocess_failure(self, monkeypatch):
        """SKR-05c: 子进程启动失败（脚本缺失等）→ error。"""
        async def fake_create_subprocess_exec(*args, **kwargs):
            raise FileNotFoundError("script not found")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

        result = await jira_confluence_report_handler({}, {})

        assert result["type"] == "error"
        assert "error" in result
