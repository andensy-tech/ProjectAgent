"""引擎产出文件回传测试（Web Chat 文件发送链路）。

Agent 在 chat 里生成文件后，必须把文件卡片（含签名下载链接）回传前端，否则用户
只能看到一段"文件已生成在服务器 /tmp/xxx"的文本，拿不到文件。

覆盖：
  - 内置写入工具（write_xlsx/write_html）产出 → yield type=file
  - 技能执行产出（file_path 在 data 中）→ yield type=file，并带上技能名
  - 同一文件多轮重复产出只回传一次
  - 非文件类工具结果不产生 file 事件
  - 后台任务提交结果 → yield type=task_submitted（前端据此轮询取文件）
"""

import pytest

from app.core.security import verify_file_token
from app.services.agent.engine import AgentEngine


def _tool_call(call_id, name, arguments):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


@pytest.fixture
def engine():
    return AgentEngine()


async def _collect(engine, message="导出表格", user_id=1):
    responses = []
    async for resp in engine.process_message(message, user_id=user_id):
        responses.append(resp)
    return responses


class TestFileDelivery:
    """产出文件 → SSE file 卡片。"""

    @pytest.mark.asyncio
    async def test_builtin_write_tool_emits_file_card(self, engine, monkeypatch, tmp_path):
        """write_xlsx 生成文件 → yield type=file（带签名下载链接）。"""
        out = tmp_path / "result.xlsx"
        out.write_text("dummy", encoding="utf-8")

        calls = []

        async def mock_llm(task_type, messages, tools, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                return "开始导出", [_tool_call(
                    "c1", "call_builtin_tool",
                    '{"tool_name": "write_xlsx", "params": {"file_path": "%s"}}' % out,
                )]
            return "已导出 Excel，可点击下方卡片下载", None

        from app.services.llm import router as llm_router_module
        monkeypatch.setattr(llm_router_module.llm_router, "route_and_complete_with_tools", mock_llm)

        async def mock_exec(func_name, args, context, **kw):
            return {
                "type": "document", "tool": "write_xlsx", "content": str(out),
                "metadata": {"file_name": "result.xlsx"}, "error": None,
            }

        engine._execute_function_call = mock_exec

        responses = await _collect(engine)

        file_cards = [r for r in responses if r.type == "file"]
        assert len(file_cards) == 1
        assert file_cards[0].data["file_name"] == "result.xlsx"
        # 下载链接可校验，且绑定当前用户
        token = file_cards[0].data["download_url"].split("token=", 1)[1]
        assert verify_file_token(token, 1) == str(out)

    @pytest.mark.asyncio
    async def test_skill_result_emits_file_card(self, engine, monkeypatch, tmp_path):
        """技能产出（file_path 在 data 中，weekly_report 的真实形态）→ yield type=file。"""
        report = tmp_path / "weekly.html"
        report.write_text("<html>周报</html>", encoding="utf-8")

        calls = []

        async def mock_llm(task_type, messages, tools, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                return "生成周报中", [_tool_call("c1", "execute_skill", '{"skill_name": "weekly_report", "params": {}}')]
            return "周报已生成", None

        from app.services.llm import router as llm_router_module
        monkeypatch.setattr(llm_router_module.llm_router, "route_and_complete_with_tools", mock_llm)

        async def mock_exec(func_name, args, context, **kw):
            return {
                "type": "success", "title": "报告已生成",
                "data": {"success": True, "message": "报告已生成",
                         "file_path": str(report), "skill_name": "weekly_report"},
                "message": "报告已生成", "error": None,
            }

        engine._execute_function_call = mock_exec

        responses = await _collect(engine, "生成周报")

        file_cards = [r for r in responses if r.type == "file"]
        assert len(file_cards) == 1
        assert file_cards[0].data["skill_name"] == "weekly_report"
        assert file_cards[0].data["file_path"] == str(report)

    @pytest.mark.asyncio
    async def test_same_file_emitted_once(self, engine, monkeypatch, tmp_path):
        """同一文件被多轮重复写入 → 只回传一次，避免刷屏。"""
        out = tmp_path / "same.xlsx"
        out.write_text("dummy", encoding="utf-8")

        calls = []

        async def mock_llm(task_type, messages, tools, **kwargs):
            calls.append(1)
            if len(calls) <= 2:
                return "写入中", [_tool_call(
                    f"c{len(calls)}", "call_builtin_tool",
                    '{"tool_name": "write_xlsx", "params": {}}',
                )]
            return "完成", None

        from app.services.llm import router as llm_router_module
        monkeypatch.setattr(llm_router_module.llm_router, "route_and_complete_with_tools", mock_llm)

        async def mock_exec(func_name, args, context, **kw):
            return {"type": "document", "tool": "write_xlsx", "content": str(out), "metadata": {}, "error": None}

        engine._execute_function_call = mock_exec

        responses = await _collect(engine)

        assert len([r for r in responses if r.type == "file"]) == 1

    @pytest.mark.asyncio
    async def test_query_result_emits_no_file_card(self, engine, monkeypatch):
        """普通查询结果（无文件产出）不产生 file 卡片。"""
        calls = []

        async def mock_llm(task_type, messages, tools, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                return "查询中", [_tool_call("c1", "call_mcp_tool", '{"server": "jira", "tool": "search_issues", "params": {}}')]
            return "共 3 条", None

        from app.services.llm import router as llm_router_module
        monkeypatch.setattr(llm_router_module.llm_router, "route_and_complete_with_tools", mock_llm)

        async def mock_exec(func_name, args, context, **kw):
            return {"issues": [{"key": "TV-1"}]}

        engine._execute_function_call = mock_exec

        responses = await _collect(engine)

        assert not [r for r in responses if r.type == "file"]

    @pytest.mark.asyncio
    async def test_multiple_files_emit_multiple_cards(self, engine, monkeypatch, tmp_path):
        """一次产出多个文件 → 每个文件一张下载卡片。"""
        a = tmp_path / "a.xlsx"
        a.write_text("a", encoding="utf-8")
        b = tmp_path / "b.html"
        b.write_text("b", encoding="utf-8")

        calls = []

        async def mock_llm(task_type, messages, tools, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                return "导出中", [_tool_call("c1", "execute_skill", '{"skill_name": "multi", "params": {}}')]
            return "两个文件都生成了", None

        from app.services.llm import router as llm_router_module
        monkeypatch.setattr(llm_router_module.llm_router, "route_and_complete_with_tools", mock_llm)

        async def mock_exec(func_name, args, context, **kw):
            return {"data": {"files": [{"file_path": str(a)}, {"file_path": str(b)}]}, "message": "ok"}

        engine._execute_function_call = mock_exec

        responses = await _collect(engine, "导出两个文件")

        names = sorted(r.data["file_name"] for r in responses if r.type == "file")
        assert names == ["a.xlsx", "b.html"]

    @pytest.mark.asyncio
    async def test_nonexistent_file_emits_no_card(self, engine, monkeypatch, tmp_path):
        """技能声称产出文件但文件不存在（写失败/被删）→ 不回传死链。"""
        calls = []

        async def mock_llm(task_type, messages, tools, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                return "导出中", [_tool_call("c1", "execute_skill", '{"skill_name": "ghost", "params": {}}')]
            return "完成", None

        from app.services.llm import router as llm_router_module
        monkeypatch.setattr(llm_router_module.llm_router, "route_and_complete_with_tools", mock_llm)

        async def mock_exec(func_name, args, context, **kw):
            return {"success": True, "data": {"file_path": str(tmp_path / "gone.xlsx")}, "message": "ok"}

        engine._execute_function_call = mock_exec

        responses = await _collect(engine, "导出")

        assert not [r for r in responses if r.type == "file"]

    @pytest.mark.asyncio
    async def test_invalid_path_does_not_break_tool_loop(self, engine, monkeypatch):
        """LLM 生成的非法路径（NUL 字节）不能让工具结果处理循环中断，后续轮次照常。"""
        calls = []

        async def mock_llm(task_type, messages, tools, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                return "写入中", [_tool_call("c1", "call_builtin_tool",
                                             '{"tool_name": "write_html", "params": {"file_path": "/tmp/x\\u0000.html"}}')]
            if len(calls) == 2:
                return "重试中", [_tool_call("c2", "call_builtin_tool",
                                             '{"tool_name": "write_html", "params": {"file_path": "/tmp/y.html"}}')]
            return "完成", None

        from app.services.llm import router as llm_router_module
        monkeypatch.setattr(llm_router_module.llm_router, "route_and_complete_with_tools", mock_llm)

        async def mock_exec(func_name, args, context, **kw):
            return {"type": "document", "tool": "write_html", "content": args.get("params", {}).get("file_path"),
                    "metadata": {}, "error": None}

        engine._execute_function_call = mock_exec

        responses = await _collect(engine, "写文件")

        # 非法路径被静默忽略：不产生 file 卡片，且**不打断工具结果处理循环**
        # （旧代码会在 _as_existing_file 抛 ValueError → 外层 except 记"工具结果处理出错"并 break，
        #  导致同轮后续工具结果被丢弃）
        assert not [r for r in responses if r.type == "file"]
        assert not [r for r in responses if "工具结果处理出错" in (r.content or "")]
        assert not [r for r in responses if r.type == "error"]
        assert len(calls) >= 3  # 两轮工具 + 最终答复（自检钩子可能再加一轮）
        assert any(r.type == "text" for r in responses)

    @pytest.mark.asyncio
    async def test_no_user_id_emits_card_without_download_url(self, engine, monkeypatch, tmp_path):
        """无用户上下文（系统/后台路径）时仍回传文件卡片，但不签发下载链接。"""
        out = tmp_path / "sys.html"
        out.write_text("x", encoding="utf-8")

        calls = []

        async def mock_llm(task_type, messages, tools, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                return "写入中", [_tool_call("c1", "call_builtin_tool", '{"tool_name": "write_html", "params": {}}')]
            return "完成", None

        from app.services.llm import router as llm_router_module
        monkeypatch.setattr(llm_router_module.llm_router, "route_and_complete_with_tools", mock_llm)

        async def mock_exec(func_name, args, context, **kw):
            return {"type": "document", "tool": "write_html", "content": str(out), "metadata": {}, "error": None}

        engine._execute_function_call = mock_exec

        responses = await _collect(engine, "写文件", user_id=None)

        cards = [r for r in responses if r.type == "file"]
        assert len(cards) == 1
        assert "download_url" not in cards[0].data

    @pytest.mark.asyncio
    async def test_task_submitted_surfaces_to_frontend(self, engine, monkeypatch):
        """长任务提交后台 → yield task_submitted（前端据此轮询，完成后回传文件）。"""
        calls = []

        async def mock_llm(task_type, messages, tools, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                return "提交中", [_tool_call("c1", "execute_skill", '{"skill_name": "jira-confluence-report", "params": {}}')]
            return "任务已提交后台执行", None

        from app.services.llm import router as llm_router_module
        monkeypatch.setattr(llm_router_module.llm_router, "route_and_complete_with_tools", mock_llm)

        async def mock_exec(func_name, args, context, **kw):
            return {"type": "task_submitted", "task_id": "task-abc",
                    "message": "技能「jira-confluence-report」已提交到后台执行"}

        engine._execute_function_call = mock_exec

        responses = await _collect(engine, "生成项目报告")

        submitted = [r for r in responses if r.type == "task_submitted"]
        assert len(submitted) == 1
        assert submitted[0].data["task_id"] == "task-abc"
        assert submitted[0].data["skill_name"] == "jira-confluence-report"
