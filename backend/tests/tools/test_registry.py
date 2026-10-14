"""Tests for the Tool Registry infrastructure."""

import pytest
from app.tools.registry import ToolDef, ToolRegistry, tool_registry


class TestToolDef:
    """ToolDef dataclass behavior."""

    def test_create_tool_def(self):
        """Create a ToolDef with required fields."""
        tool = ToolDef(
            name="test_tool",
            display_name="Test Tool",
            description="A test tool",
            handler=lambda p, c: {"result": "ok"},
        )
        assert tool.name == "test_tool"
        assert tool.display_name == "Test Tool"
        assert tool.handler is not None

    def test_tool_def_defaults(self):
        """ToolDef default values."""
        tool = ToolDef(
            name="test",
            display_name="Test",
            description="desc",
            handler=lambda p, c: {},
        )
        assert tool.input_types == []
        assert tool.output_types == []
        assert tool.keywords == []
        assert tool.timeout == 120


class TestToolRegistry:
    """ToolRegistry behavior."""

    def setup_method(self):
        self.registry = ToolRegistry()

    def test_register_and_get(self):
        """Register a tool and retrieve it."""
        handler = lambda p, c: {"result": "ok"}
        tool = ToolDef(name="greeter", display_name="Greeter", description="Says hello", handler=handler)
        self.registry.register(tool)
        assert self.registry.get("greeter") is tool

    def test_unregister(self):
        """Unregister a tool."""
        tool = ToolDef(name="temp", display_name="Temp", description="temporary", handler=lambda p, c: {})
        self.registry.register(tool)
        self.registry.unregister("temp")
        assert self.registry.get("temp") is None

    def test_list_tools(self):
        """List all registered tools."""
        t1 = ToolDef(name="a", display_name="A", description="tool a", handler=lambda p, c: {})
        t2 = ToolDef(name="b", display_name="B", description="tool b", handler=lambda p, c: {})
        self.registry.register(t1)
        self.registry.register(t2)
        tools = self.registry.list_tools()
        assert len(tools) == 2
        assert {t.name for t in tools} == {"a", "b"}

    def test_get_descriptions(self):
        """Get descriptions string for LLM context."""
        t1 = ToolDef(name="read_pdf", display_name="Read PDF", description="Extract text from PDFs",
                     handler=lambda p, c: {}, input_types=["pdf"])
        self.registry.register(t1)
        desc = self.registry.get_descriptions()
        assert "read_pdf" in desc
        assert "Extract text from PDFs" in desc

    def test_execute_success(self):
        """Execute a registered tool."""
        async def handler(params, context):
            return {"result": f"hello {params.get('name', 'world')}"}

        tool = ToolDef(name="greeter", display_name="Greeter", description="desc", handler=handler)
        self.registry.register(tool)

        import asyncio
        result = asyncio.run(self.registry.execute("greeter", {"name": "test"}, {}))
        assert result["result"] == "hello test"

    def test_execute_not_found(self):
        """Execute an unregistered tool raises ValueError."""
        import asyncio
        with pytest.raises(ValueError, match="Tool not found"):
            asyncio.run(self.registry.execute("nonexistent", {}, {}))


class TestGlobalRegistry:
    """Global tool_registry singleton."""

    def test_global_registry_is_singleton(self):
        """tool_registry is a ToolRegistry instance."""
        from app.tools.registry import tool_registry
        assert isinstance(tool_registry, ToolRegistry)

    def test_register_all_tools_populates_global(self):
        """register_all_tools() registers all built-in tools."""
        from app.tools import register_all_tools
        # Clear and re-register
        for t in tool_registry.list_tools():
            tool_registry.unregister(t.name)
        register_all_tools()
        all_tools = tool_registry.list_tools()
        names = {t.name for t in all_tools}

        # 31 = 15 文档工具 + 4 Gerrit + 2 Confluence + 1 Jira filter + 1 FocusSet + 1 judge_progress
        #      + 1 risk_analyze + 1 context_spill_read + 2 scheduled_task + 1 deliver_file（P-B/E/G/A/定时任务/文件回传）
        #      + 2 ci_build（CI 构建查询/下载上传，2026-09-25）
        assert len(all_tools) == 31
        assert "read_docx" in names
        assert "write_docx" in names
        assert "read_pdf" in names
        assert "extract_pdf_metadata" in names
        assert "read_xlsx" in names
        assert "write_xlsx" in names
        assert "read_pptx" in names
        assert "write_pptx" in names
        assert "read_image_metadata" in names
        assert "ocr_image" in names
        assert "read_eml" in names
        assert "write_eml" in names
        assert "read_html" in names
        assert "write_html" in names
        assert "read_text_file" in names
        assert "gerrit_submit_change" in names
        assert "gerrit_review_change" in names
        assert "gerrit_get_change_detail" in names
        assert "gerrit_batch_get_changes" in names
        assert "confluence_get_page_content" in names
        assert "confluence_ocr_page_image" in names
        assert "context_spill_read" in names
        assert "create_scheduled_task" in names
        assert "list_scheduled_tasks" in names
        assert "deliver_file" in names
        assert "ci_build_query" in names
        assert "ci_build_download" in names

    def test_tool_keywords(self):
        """Each tool has appropriate keywords for intent matching."""
        from app.tools import register_all_tools
        for t in tool_registry.list_tools():
            tool_registry.unregister(t.name)
        register_all_tools()

        read_pdf = tool_registry.get("read_pdf")
        assert read_pdf is not None
        assert "pdf" in read_pdf.keywords
        assert "pdf" in read_pdf.input_types
