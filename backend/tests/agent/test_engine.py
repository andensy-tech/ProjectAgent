"""
Tests for app.services.agent.engine module (new single-brain architecture).

Test Cases:
- ENG-01: LLM returns text → text response + complete
- ENG-02: LLM returns error → error + complete
- ENG-03: handle_confirmation cancel
- ENG-04: Memory update failure doesn't break response
- ENG-05: Tool definition building
- ENG-06: Tool call results follow assistant tool_calls message
"""

import pytest
from app.services.agent.engine import AgentEngine, AgentResponse
from app.services.agent.types import AgentResponse


class TestLLMResponse:
    """Test main LLM response flow."""

    @pytest.fixture
    def engine(self, mock_llm_router):
        return AgentEngine()

    @pytest.mark.asyncio
    async def test_llm_returns_text(self, engine, monkeypatch):
        """ENG-01: LLM returns text → 'text' response followed by 'complete'."""
        # Mock the router to return simple text
        async def mock_complete_with_tools(task_type, messages, tools, **kwargs):
            return "你好！我是项目管理AI助手，有什么可以帮您的？", None

        from app.services.llm import router as llm_router_module
        monkeypatch.setattr(
            llm_router_module.llm_router,
            "route_and_complete_with_tools",
            mock_complete_with_tools,
        )

        responses = []
        async for resp in engine.process_message("你好"):
            responses.append(resp)

        types = [r.type for r in responses]
        assert "thinking" in types
        assert "text" in types
        assert types[-1] == "complete"

    @pytest.mark.asyncio
    async def test_llm_failure(self, engine, monkeypatch):
        """ENG-02: LLM exception → 'error' response."""
        async def mock_fail(task_type, messages, tools, **kwargs):
            raise Exception("LLM connection failed")

        from app.services.llm import router as llm_router_module
        monkeypatch.setattr(
            llm_router_module.llm_router,
            "route_and_complete_with_tools",
            mock_fail,
        )

        responses = []
        async for resp in engine.process_message("test"):
            responses.append(resp)

        assert any(r.type == "error" for r in responses)
        assert responses[-1].type == "complete"


class TestConfirmationHandling:
    """Test user confirmation handling (ENG-03)."""

    @pytest.fixture
    def engine(self, mock_llm_router):
        return AgentEngine()

    @pytest.mark.asyncio
    async def test_cancel_confirmation(self, engine):
        """ENG-03: Cancel → '已取消' message."""
        responses = []
        async for resp in engine.handle_confirmation("exec-1", 1, confirmed=False):
            responses.append(resp)

        assert any(r.type == "text" and "已取消" in r.content for r in responses)
        assert responses[-1].type == "complete"

    @pytest.mark.asyncio
    async def test_confirm_no_pending(self, engine):
        """ENG-03: Confirm without pending → re-enters LLM loop (text response)."""
        responses = []
        async for resp in engine.handle_confirmation("nonexistent", 1, confirmed=True):
            responses.append(resp)

        assert any(r.type == "text" for r in responses), "Should get text via LLM re-entry"
        assert responses[-1].type == "complete"


class TestErrorHandling:
    """Test error handling (ENG-04)."""

    @pytest.fixture
    def engine(self, mock_llm_router):
        return AgentEngine()

    @pytest.mark.asyncio
    async def test_memory_update_failure_isolated(self, engine, monkeypatch):
        """ENG-04: Memory update failure doesn't break the response."""
        # Make memory load fail
        monkeypatch.setattr(
            "app.services.agent.engine.memory_system.get_context",
            lambda *args, **kwargs: (_ for _ in ()).throw(Exception("Memory error")),
        )

        async def mock_complete_with_tools(task_type, messages, tools, **kwargs):
            return "Hello!", None

        from app.services.llm import router as llm_router_module
        monkeypatch.setattr(
            llm_router_module.llm_router,
            "route_and_complete_with_tools",
            mock_complete_with_tools,
        )

        responses = []
        async for resp in engine.process_message("你好"):
            responses.append(resp)

        # Should still get a response despite memory error
        assert any(r.type == "text" for r in responses)
        assert responses[-1].type == "complete"


class TestToolDefinitions:
    """Test tool definition building and tool calling flow (ENG-05~06)."""

    @pytest.fixture
    def engine(self, mock_llm_router):
        return AgentEngine()

    @pytest.mark.asyncio
    async def test_build_tool_definitions(self):
        """ENG-05: Tool list contains expected function definitions."""
        engine = AgentEngine()
        # _build_tool_definitions 已改为 async（MCP 工具定义经 MCPCapabilityService 按用户获取）。
        # 无 user_id 时不注入 MCP 工具，故仍为 6 个 meta-tool（含 ask_user）。
        tools = await engine._build_tool_definitions({})

        tool_names = [t["function"]["name"] for t in tools]
        assert "execute_skill" in tool_names
        assert "call_mcp_tool" in tool_names
        assert "call_builtin_tool" in tool_names
        assert "memory_search" in tool_names
        assert "memory_save" in tool_names
        assert "ask_user" in tool_names  # Phase 2 用户协作工具
        assert len(tools) == 6

    @pytest.mark.asyncio
    async def test_tool_calls_precede_tool_results_in_messages(self, engine, monkeypatch):
        """ENG-06: Tool results must follow assistant tool_calls message (API compliance)."""
        second_call_messages = []

        async def mock_with_tools(task_type, messages, tools, **kwargs):
            nonlocal second_call_messages
            if len(messages) > 5:  # Second call has tool results appended
                second_call_messages = list(messages)
                return "最终回复", None
            # First call: LLM returns a tool_call
            return None, [
                {
                    "id": "call_456",
                    "type": "function",
                    "function": {
                        "name": "call_builtin_tool",
                        "arguments": '{"tool_name": "read_docx", "params": {"file_path": "/nonexistent/test.docx"}}',
                    },
                }
            ]

        from app.services.llm import router as llm_router_module
        monkeypatch.setattr(
            llm_router_module.llm_router,
            "route_and_complete_with_tools",
            mock_with_tools,
        )

        responses = []
        async for resp in engine.process_message("test"):
            responses.append(resp)

        types = [r.type for r in responses]
        assert "text" in types
        assert responses[-1].type == "complete"

        # Verify messages structure: assistant tool_calls msg precedes tool result msgs
        tool_call_msg = None
        tool_result_msgs = []
        for m in second_call_messages:
            if m["role"] == "assistant" and "tool_calls" in m:
                tool_call_msg = m
            if m["role"] == "tool":
                tool_result_msgs.append(m)

        assert tool_call_msg is not None, (
            "Missing assistant message with tool_calls before tool results"
        )
        assert len(tool_result_msgs) > 0, "Expected at least one tool result message"
        idx_tool_call = second_call_messages.index(tool_call_msg)
        for tr_msg in tool_result_msgs:
            assert second_call_messages.index(tr_msg) > idx_tool_call, (
                f"Tool result message appears before assistant tool_calls message"
            )


class TestMemoryPersistence:
    """Test memory persistence operations (ENG-07~08)."""

    @pytest.fixture
    def engine(self):
        return AgentEngine()

    @pytest.mark.asyncio
    async def test_save_memory_no_db_fallback(self, engine):
        """ENG-07: Without DB, _save_memory returns not-saved message."""
        result = await engine._save_memory("test_key", "test content", "note")
        assert result["saved"] is False
        assert "未连接" in result["message"]

    @pytest.mark.asyncio
    async def test_search_memory_no_db_fallback(self, engine):
        """ENG-08: Without DB, _search_memory returns empty results."""
        result = await engine._search_memory("test query")
        assert result["entries"] == []
        assert "未连接" in result["message"]


class TestTruncateResult:
    """Test _truncate_result edge cases (ENG-09~12)."""

    def test_short_result_passthrough(self):
        """ENG-09: Result under max_len passes through unchanged."""
        data = {"key": "value", "number": 42}
        result = AgentEngine._truncate_result(data)
        import json
        assert json.loads(result) == data

    def test_long_list_truncated(self):
        """ENG-10: Long lists get trimmed with _total annotation."""
        # Each item ~100 chars, so 100 items > 5000 default max_len
        data = {"items": ["x" * 100 for _ in range(100)]}
        result = AgentEngine._truncate_result(data)
        import json
        parsed = json.loads(result)
        assert len(parsed["items"]) == 10  # trimmed to 10
        assert parsed["_items_total"] == 100

    def test_long_string_truncated_to_json_note(self):
        """ENG-11: Very long result becomes structured JSON note."""
        data = {"big": "x" * 10000}
        result = AgentEngine._truncate_result(data)
        import json
        parsed = json.loads(result)
        assert parsed["_note"] == "结果过长，已截断"
        assert parsed["original_length"] > 10000

    def test_non_dict_long_result(self):
        """ENG-12: Non-dict long result also becomes JSON note."""
        long_str = "a" * 10000
        result = AgentEngine._truncate_result(long_str)
        import json
        parsed = json.loads(result)
        assert "_note" in parsed


class TestFormatToolResult:
    """Test _format_tool_result with various MCP response structures (ENG-13~17)."""

    @pytest.fixture
    def engine(self):
        return AgentEngine()

    def test_jira_issues_list(self, engine):
        """ENG-13: Jira issues list formatted correctly."""
        data = {
            "issues": [
                {"key": "TV-123", "summary": "Test issue", "status": "Open",
                 "assignee": "Alice", "priority": "High"},
            ]
        }
        result = engine._format_tool_result(data)
        assert "TV-123" in result
        assert "Test issue" in result
        assert "Open" in result
        assert "Alice" in result

    def test_single_jira_issue(self, engine):
        """ENG-14: Single Jira issue dict formatted."""
        data = {"key": "TV-456", "summary": "Single issue", "status": "In Progress"}
        result = engine._format_tool_result(data)
        assert "TV-456" in result
        assert "In Progress" in result

    def test_confluence_pages(self, engine):
        """ENG-15: Confluence pages list formatted."""
        data = {
            "pages": [
                {"title": "Project Plan", "space": "AML", "url": "/wiki/123"},
            ]
        }
        result = engine._format_tool_result(data)
        assert "Project Plan" in result
        assert "AML" in result

    def test_gerrit_changes(self, engine):
        """ENG-16: Gerrit changes list formatted."""
        data = {
            "changes": [
                {"changeId": "Iabc123", "subject": "Fix bug", "status": "NEW",
                 "owner": {"name": "Bob"}},
            ]
        }
        result = engine._format_tool_result(data)
        assert "Iabc123" in result
        assert "Fix bug" in result
        assert "Bob" in result

    def test_generic_dict(self, engine):
        """ENG-17: Generic dict formatted as key-value."""
        data = {"name": "test", "count": 42, "enabled": True}
        result = engine._format_tool_result(data)
        assert "name" in result
        assert "test" in result
        assert "count" in result

    def test_null_or_empty(self, engine):
        """ENG-18: Null/empty returns fallback message."""
        assert "空结果" in engine._format_tool_result(None)
        assert "空结果" in engine._format_tool_result({})

    def test_epic_with_creation_guide(self, engine):
        """ENG-19: Epic output includes sub-task creation guide with customfield_10102.

        This test mirrors the REAL business logic path: _format_tool_result output
        is what the LLM sees when creating sub-tasks. If the guide is wrong
        (e.g. missing customfield_10102 or telling LLM to use create_links),
        the web chat will create issues with "Relates To" instead of under the Epic.
        """
        data = {
            "key": "TV-123",
            "summary": "Test Epic",
            "status": "Open",
            "assignee": "user1",
            "fields": {
                "summary": "Test Epic",
                "status": {"name": "Open"},
                "assignee": {"displayName": "user1", "name": "user1"},
                "issuetype": {"name": "Epic"},
                "project": "TV Projects",
                "components": ["System-Android"],
                "customfield_10107": ["TV Reference"],
                "customfield_10300": ["Android U-14"],
                "customfield_10407": ["PROJECTX-CHIP-X"],
                "customfield_10700": "Tellen.yu",
                "customfield_11002": "niko.he",
                "customfield_10409": "jiaojiao.wang",
                "customfield_12506": ["Xiaokang.Yang"],
                "customfield_12507": ["guohua.chen"],
                "customfield_11104": ["someone"],
                "customfield_11703": ["Can't Compare"],
            },
        }
        result = engine._format_tool_result(data)

        # Must contain the creation guide
        assert "创建子任务(Task)指南" in result, "Epic output must include creation guide"

        # Must include customfield_10102 (Epic Link) — REAL Epic Link for this Jira instance
        assert 'customfield_10102' in result, "Guide must include customfield_10102 for Epic linking"
        assert '"TV-123"' in result, "customfield_10102 must reference the Epic key"
        assert '将Task挂到此Epic下' in result, "Guide must explain customfield_10102 purpose"

        # Must include required field values
        assert 'TV Reference' in result
        assert 'Tellen.yu' in result
        assert 'System-Android' in result

        # Must flag cannot-set fields as warnings (not as API格式)
        assert '⚠️' in result, "Cannot-set fields must be flagged with ⚠️"
        assert 'customfield_12506' in result
        assert 'customfield_12507' in result


class TestPhase1Cognitive:
    """Phase 1 认知脚手架：错误分类 / 元认知提示 / 优雅退出。"""

    @pytest.fixture
    def engine(self):
        return AgentEngine()

    def test_classify_error(self):
        """P1-01: 错误分类 —— config/auth/timeout/data/tool_unavailable/None。"""
        assert AgentEngine._classify_error({"error": "MCP 服务器未配置或不存在: jira"}) == "config_error"
        assert AgentEngine._classify_error({"error": "MCP 服务器未配置 URL: confluence"}) == "config_error"
        assert AgentEngine._classify_error({"error": "Confluence 认证失败（HTTP 401），请检查 MCP 配置中的用户名和密码"}) == "auth_error"
        assert AgentEngine._classify_error({"error": "MCP 服务器 jira 响应超时（超过 15 秒）"}) == "timeout"
        assert AgentEngine._classify_error({"error": "Confluence 页面不存在 (pageId=123)"}) == "data_error"
        assert AgentEngine._classify_error({"error": "未知的函数: foo"}) == "tool_unavailable"
        assert AgentEngine._classify_error({"success": True, "data": [1, 2, 3]}) is None
        assert AgentEngine._classify_error("普通文本结果") is None

    def test_meta_status_contains_state(self, engine):
        """P1-02: 元认知提示包含轮次/已完成/失败/停滞警告。"""
        state = {
            "done": ["confluence__get_page"],
            "failed": {"jira__search_issues:{}": 2},
            "stall_count": 3,
        }
        status = engine._build_meta_status(7, state)
        assert "第 7/40 轮" in status
        assert "confluence__get_page" in status
        assert "jira__search_issues(2次)" in status
        assert "连续 3 轮" in status
        assert "不要再重复相同尝试" in status

    def test_meta_status_no_stall_short(self, engine):
        """P1-03: 无停滞且预算充足时，提示保持简洁（不注入停滞警告）。"""
        state = {"done": [], "failed": {}, "stall_count": 1}
        status = engine._build_meta_status(5, state)
        assert "第 5/40 轮" in status
        assert "连续" not in status

    def test_graceful_exit(self, engine):
        """P1-04: 优雅退出包含已完成部分/障碍/建议方向（替代"40步报错"）。"""
        state = {"done": ["confluence__get_page", "confluence_get_page_content"], "failed": {"x:{}": 3}}
        msg = engine._build_graceful_exit(state, "Confluence 页面不存在 (pageId=123)")
        assert "已完成" in msg
        assert "confluence_get_page_content" in msg
        assert "遇到问题" in msg
        assert "建议" in msg
        assert "40 步" not in msg  # 不再是干巴巴的报错

    def test_graceful_exit_empty(self, engine):
        """P1-05: 无任何进展时的优雅退出仍给出建议方向。"""
        msg = engine._build_graceful_exit({"done": [], "failed": {}}, None)
        assert "尚未取得可交付的结果" in msg
        assert "建议" in msg


class TestPhase2AskUser:
    """Phase 2 用户协作：ask_user 工具。"""

    @pytest.fixture
    def engine(self):
        return AgentEngine()

    @pytest.mark.asyncio
    async def test_ask_user_interrupts_loop(self, engine, monkeypatch):
        """P2-01: LLM 调用 ask_user → 引擎 yield ask_user + complete，结束等待用户，不执行其他工具。"""
        async def mock_with_tools(task_type, messages, tools, **kwargs):
            return "需要确认范围", [{
                "id": "call_ask",
                "type": "function",
                "function": {"name": "ask_user", "arguments": '{"question": "查哪个项目?", "options": ["A", "B"]}'},
            }]
        from app.services.llm import router as llm_router_module
        monkeypatch.setattr(llm_router_module.llm_router, "route_and_complete_with_tools", mock_with_tools)

        responses = []
        async for resp in engine.process_message("查一下", user_id=None):
            responses.append(resp)

        ask = [r for r in responses if r.type == "ask_user"]
        assert len(ask) == 1
        assert ask[0].content == "查哪个项目?"
        assert ask[0].actions == [{"label": "A", "value": "A"}, {"label": "B", "value": "B"}]
        assert any(r.type == "complete" for r in responses)
        assert not any(r.type == "text" for r in responses)  # 未给出最终回答

    @pytest.mark.asyncio
    async def test_ask_user_no_options(self, engine, monkeypatch):
        """P2-02: 无 options 时 actions 为空，仍触发 ask_user + complete。"""
        async def mock_with_tools(task_type, messages, tools, **kwargs):
            return "需要补充", [{
                "id": "call_ask",
                "type": "function",
                "function": {"name": "ask_user", "arguments": '{"question": "请提供目标Epic"}'},
            }]
        from app.services.llm import router as llm_router_module
        monkeypatch.setattr(llm_router_module.llm_router, "route_and_complete_with_tools", mock_with_tools)

        responses = []
        async for resp in engine.process_message("创建任务", user_id=None):
            responses.append(resp)
        ask = [r for r in responses if r.type == "ask_user"]
        assert len(ask) == 1
        assert ask[0].actions == []

    @pytest.mark.asyncio
    async def test_ask_user_defensive_branch(self, engine):
        """P2-03: _execute_function_call 对 ask_user 防御兜底。"""
        result = await engine._execute_function_call("ask_user", {"question": "q"}, {})
        assert result.get("success") is True
        assert "等待用户答复" in result.get("message", "")


class TestPhase3Planning:
    """Phase 3 规划增强：自我验证钩子。"""

    @pytest.fixture
    def engine(self):
        return AgentEngine()

    @pytest.mark.asyncio
    async def test_self_verify_triggers_on_complex_task(self, engine, monkeypatch):
        """P3-01: 多步任务最终回答前触发自检，采用自检后的回答。"""
        calls = []
        async def mock_with_tools(task_type, messages, tools, **kwargs):
            calls.append(len(messages))
            if len(calls) == 1:
                # 第一轮：调用一个工具
                return "执行中", [{
                    "id": "c1", "type": "function",
                    "function": {"name": "call_builtin_tool", "arguments": '{"tool_name": "read_docx", "params": {"file_path": "/x.docx"}}'},
                }]
            if len(calls) == 2:
                return "最终回答初稿（可能遗漏）", None
            return "最终回答（自检后已补充完整）", None  # 第3次：自检调用

        from app.services.llm import router as llm_router_module
        monkeypatch.setattr(llm_router_module.llm_router, "route_and_complete_with_tools", mock_with_tools)

        async def mock_exec(func_name, args, context, **kw):
            return {"type": "document", "success": True, "title": "文档", "content": "内容"}
        engine._execute_function_call = mock_exec

        responses = []
        async for resp in engine.process_message("帮我整理文档并总结", user_id=None):
            responses.append(resp)

        text = [r for r in responses if r.type == "text"]
        assert len(calls) == 3, f"应触发自检（第3次调用），实际 {len(calls)} 次"
        assert any("自检后已补充完整" in r.content for r in text), "应采用自检后的回答"

    @pytest.mark.asyncio
    async def test_self_verify_can_continue_tools(self, engine, monkeypatch):
        """P3-02: 自检后 LLM 认为需要补充 → 继续调用工具而非直接回答。"""
        calls = []
        async def mock_with_tools(task_type, messages, tools, **kwargs):
            calls.append(len(messages))
            if len(calls) == 1:
                return "执行中", [{
                    "id": "c1", "type": "function",
                    "function": {"name": "call_builtin_tool", "arguments": '{"tool_name": "read_docx", "params": {"file_path": "/x.docx"}}'},
                }]
            if len(calls) == 2:
                return "初稿", None
            if len(calls) == 3:
                return "补充数据", [{
                    "id": "c2", "type": "function",
                    "function": {"name": "call_builtin_tool", "arguments": '{"tool_name": "read_docx", "params": {"file_path": "/y.docx"}}'},
                }]  # 自检后决定补充查询
            return "最终回答（补充后）", None  # 第4次：补充完给出最终回答

        from app.services.llm import router as llm_router_module
        monkeypatch.setattr(llm_router_module.llm_router, "route_and_complete_with_tools", mock_with_tools)

        async def mock_exec(func_name, args, context, **kw):
            return {"type": "document", "success": True, "title": "文档", "content": "内容"}
        engine._execute_function_call = mock_exec

        responses = []
        async for resp in engine.process_message("整理文档并总结", user_id=None):
            responses.append(resp)

        # 自检（第3次调用）返回 tool_calls → 继续执行工具 → 第4次给出最终回答
        assert len(calls) >= 4, f"自检后应继续执行工具，实际 {len(calls)} 次"
        text = [r for r in responses if r.type == "text"]
        assert any("补充后" in r.content for r in text), "应采用补充后的最终回答"

    @pytest.mark.asyncio
    async def test_no_verify_on_simple_chat(self, engine, monkeypatch):
        """P3-03: 简单对话（无工具调用）不触发自检，节省一次 LLM 调用。"""
        calls = []
        async def mock_with_tools(task_type, messages, tools, **kwargs):
            calls.append(len(messages))
            return "你好！有什么可以帮您？", None
        from app.services.llm import router as llm_router_module
        monkeypatch.setattr(llm_router_module.llm_router, "route_and_complete_with_tools", mock_with_tools)

        responses = []
        async for resp in engine.process_message("你好", user_id=None):
            responses.append(resp)
        assert len(calls) == 1, "简单对话不应触发自检"


class TestToolResultCache:
    """会话级工具结果缓存（Phase 1.5）。"""

    @pytest.fixture
    def engine(self):
        return AgentEngine()

    @pytest.mark.asyncio
    async def test_cache_reuses_result(self, engine, monkeypatch):
        """缓存：相同 tool+args 第二次命中缓存，不重复执行。"""
        calls = []
        async def mock_exec(func, args, ctx, **kw):
            calls.append(func)
            return {"type": "document", "success": True, "content": "data"}
        monkeypatch.setattr(engine, "_execute_function_call", mock_exec)
        engine._result_cache = {}

        r1 = await engine._execute_with_cache("confluence__get_page", {"page_id": "1"}, {})
        r2 = await engine._execute_with_cache("confluence__get_page", {"page_id": "1"}, {})
        assert len(calls) == 1, "第二次应命中缓存不重复执行"
        assert r2.get("_cached") is True
        assert r1.get("_cached") is None

    @pytest.mark.asyncio
    async def test_write_not_cached(self, engine, monkeypatch):
        """写操作/状态变更工具不缓存。"""
        calls = []
        async def mock_exec(func, args, ctx, **kw):
            calls.append(func)
            return {"type": "text", "success": True, "message": "ok"}
        monkeypatch.setattr(engine, "_execute_function_call", mock_exec)
        engine._result_cache = {}

        await engine._execute_with_cache("confluence__patch_table_cells", {"page_id": "1"}, {})
        await engine._execute_with_cache("confluence__patch_table_cells", {"page_id": "1"}, {})
        assert len(calls) == 2, "写操作每次都应重新执行"

    @pytest.mark.asyncio
    async def test_failed_not_cached(self, engine, monkeypatch):
        """失败结果不缓存（便于重试）。"""
        calls = []
        async def mock_exec(func, args, ctx, **kw):
            calls.append(func)
            return {"type": "error", "success": False, "error": "失败"}
        monkeypatch.setattr(engine, "_execute_function_call", mock_exec)
        engine._result_cache = {}

        await engine._execute_with_cache("confluence__get_page", {"page_id": "1"}, {})
        await engine._execute_with_cache("confluence__get_page", {"page_id": "1"}, {})
        assert len(calls) == 2, "失败不缓存，应重新执行"


class TestGerritBatchTool:
    """gerrit_batch_get_changes 批量查询工具。"""

    @pytest.mark.asyncio
    async def test_missing_change_ids(self):
        """缺 change_ids 参数返回错误。"""
        from app.tools.gerrit_tool import gerrit_batch_get_changes
        r = await gerrit_batch_get_changes({}, {"_user_id": 1})
        assert r.get("success") is False
        assert "change_ids" in r.get("error", "")

    @pytest.mark.asyncio
    async def test_string_change_ids(self, monkeypatch):
        """字符串形式的 change_ids 被正确解析；未配置 MCP 时返回配置引导。"""
        from app.tools.gerrit_tool import gerrit_batch_get_changes
        async def fake_auth(uid):
            return {}
        monkeypatch.setattr("app.services.settings_service.get_mcp_auth_dict", fake_auth)
        r = await gerrit_batch_get_changes({"change_ids": "12345,12346"}, {"_user_id": 1})
        assert r.get("success") is False
        assert "未配置" in r.get("error", "") or "认证" in r.get("error", "")
