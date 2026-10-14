"""
端到端场景测试：真实 agent 引擎执行用户高频操作。

原则：**不 mock process_message**，走真实 `/api/chat/message` → AgentEngine
function-calling 循环。只 mock 两个外部依赖：
  1. **LLM 决策**（route_and_complete_with_tools 返回特定 tool_call 序列）
  2. **外部系统**（MCP capability.call 返回模拟的 jira/gerrit/confluence 数据）

这样验证完整闭环：chat 输入 → 引擎解析 → 真实工具调度 → 外部查询 → 结果格式化
→ LLM 二次推理 → 最终回复。与真实用户使用场景一致。

覆盖场景（用户高频）：
  - web chat 查询 Jira 问题状态
  - web chat 执行 Skill 生成报告
  - web chat 查询 CL（Gerrit）合并状态
  - web chat 读取 Confluence 页面
"""

import json

import pytest
import pytest_asyncio

from app.services.agent.executor import action_executor


@pytest_asyncio.fixture(autouse=True)
async def _seed_user_llm_config(db_session):
    """预置 admin 用户的 LLM 配置，使 agent 引擎进入真实 LLM 循环。

    engine 在启动时检查用户是否配置了大模型（api_key），未配置则直接返回
    中文错误提示。预置 UserSetting(llm) 后引擎才会真正走 function-calling 循环。
    """
    from app.models import UserSetting

    db_session.add(UserSetting(user_id=1, key="llm", value=json.dumps({
        "default_llm": "commercial",
        "commercial_api_key": "test-key-12345",
        "commercial_api_base": "http://localhost:9999/v1",
        "commercial_model": "deepseek-chat",
    })))
    await db_session.commit()


def _mock_llm_tool_sequence(monkeypatch, tool_call, final_text):
    """mock LLM：首轮返回 tool_call；工具结果回传后返回 final_text。"""
    from app.services.llm import router as router_module

    async def fake_route(task_type, messages, tools, **kwargs):
        has_tool_result = any(m.get("role") == "tool" for m in messages)
        if has_tool_result:
            return final_text, None
        return None, [tool_call]

    monkeypatch.setattr(router_module.llm_router, "route_and_complete_with_tools", fake_route)


def _mock_mcp_calls(monkeypatch, handler):
    """mock MCP capability.call：按 (server, tool) 分发模拟外部系统数据。"""
    from app.mcp.capability import capability

    async def fake_call(server, tool, params, user_id=None):
        return handler(server, tool, params)

    monkeypatch.setattr(capability, "call", fake_call)


def _parse_sse(text: str) -> list:
    return [json.loads(line[6:]) for line in text.strip().splitlines() if line.startswith("data: ")]


def _text_contents(events: list) -> list:
    return [e.get("content", "") for e in events if e.get("type") in ("text", "card")]


class TestWebChatQueryJira:
    """用户高频：在 web chat 查询 Jira 问题状态。"""

    @pytest.mark.asyncio
    async def test_query_jira_issue(self, async_client, mock_llm_router, monkeypatch):
        """输入"查一下 TV-925009 的状态" → 引擎真实发起 Jira MCP 查询 → 回复状态。"""
        tool_call = {
            "id": "c1", "type": "function",
            "function": {
                "name": "call_mcp_tool",
                "arguments": json.dumps({
                    "server": "jira", "tool": "search_issues",
                    "params": {"jql": "key = TV-925009"},
                }),
            },
        }
        _mock_llm_tool_sequence(monkeypatch, tool_call, "TV-925009 当前状态为 In Progress")

        captured = {}

        def jira_handler(server, tool, params):
            captured["server"] = server
            captured["tool"] = tool
            return {
                "issues": [{
                    "key": "TV-925009",
                    "fields": {"summary": "测试问题", "status": "In Progress"},
                }],
            }

        _mock_mcp_calls(monkeypatch, jira_handler)

        resp = await async_client.post("/api/chat/message", json={"message": "查一下 TV-925009 的状态"})
        assert resp.status_code == 200

        events = _parse_sse(resp.text)
        assert any(e.get("type") == "complete" for e in events)
        assert any("TV-925009" in t for t in _text_contents(events))
        # 验证引擎真实发起了 Jira MCP 查询
        assert captured.get("server") == "jira"
        assert captured.get("tool") == "search_issues"

    @pytest.mark.asyncio
    async def test_query_jira_not_found(self, async_client, mock_llm_router, monkeypatch):
        """查询不存在的 Jira 问题 → 引擎拿到空结果 → 回复未找到。"""
        tool_call = {
            "id": "c1", "type": "function",
            "function": {
                "name": "call_mcp_tool",
                "arguments": json.dumps({
                    "server": "jira", "tool": "get_issue",
                    "params": {"issue_key": "TV-99999"},
                }),
            },
        }
        _mock_llm_tool_sequence(monkeypatch, tool_call, "未找到 TV-99999 对应的 Jira 问题")

        _mock_mcp_calls(monkeypatch, lambda server, tool, params: {"issues": []})

        resp = await async_client.post("/api/chat/message", json={"message": "TV-99999 是什么状态"})
        assert resp.status_code == 200
        events = _parse_sse(resp.text)
        assert any("未找到" in t for t in _text_contents(events))


class TestWebChatExecuteSkill:
    """用户高频：在 web chat 执行 Skill。"""

    @pytest.mark.asyncio
    async def test_execute_report_skill(self, async_client, mock_llm_router, monkeypatch):
        """输入"执行 jira-confluence-report 汇总" → 引擎真实调用 execute_skill → 返回报告。"""
        tool_call = {
            "id": "c1", "type": "function",
            "function": {
                "name": "execute_skill",
                "arguments": json.dumps({
                    "name": "jira-confluence-report",
                    "params": {"config_name": "default"},
                }),
            },
        }
        _mock_llm_tool_sequence(
            monkeypatch, tool_call,
            "报告已生成：共 46 个 Jira，其中 P0 1 个，ETA 即将到期 20 个。",
        )

        async def fake_skill(params, context):
            return {
                "type": "report",
                "success": True,
                "title": "Jira项目管理报告",
                "data": {"total_issues": 46, "p0_issues": 1, "eta_expiring": 20},
            }

        action_executor.register_skill("jira-confluence-report", fake_skill)
        try:
            resp = await async_client.post(
                "/api/chat/message", json={"message": "执行 jira-confluence-report 汇总一下"},
            )
            assert resp.status_code == 200
            events = _parse_sse(resp.text)
            assert any("报告已生成" in t for t in _text_contents(events))
        finally:
            action_executor.skill_registry.pop("jira-confluence-report", None)

    @pytest.mark.asyncio
    async def test_execute_unknown_skill(self, async_client, mock_llm_router, monkeypatch):
        """执行不存在的 skill → 引擎返回错误而非崩溃。"""
        tool_call = {
            "id": "c1", "type": "function",
            "function": {
                "name": "execute_skill",
                "arguments": json.dumps({"name": "no-such-skill", "params": {}}),
            },
        }
        _mock_llm_tool_sequence(monkeypatch, tool_call, "技能执行失败：Skill not found: no-such-skill")

        resp = await async_client.post("/api/chat/message", json={"message": "执行 no-such-skill"})
        assert resp.status_code == 200
        events = _parse_sse(resp.text)
        assert any("失败" in t for t in _text_contents(events))


class TestWebChatQueryCl:
    """用户高频：在 web chat 查询 CL（Gerrit）合并状态。"""

    @pytest.mark.asyncio
    async def test_query_cl_status(self, async_client, mock_llm_router, monkeypatch):
        """查询 CL 合并状态 → 引擎真实发起 Gerrit MCP 查询 → 回复状态。"""
        tool_call = {
            "id": "c1", "type": "function",
            "function": {
                "name": "call_mcp_tool",
                "arguments": json.dumps({
                    "server": "gerrit_gerrit1", "tool": "get_change",
                    "params": {"change_id": "67736"},
                }),
            },
        }
        _mock_llm_tool_sequence(monkeypatch, tool_call, "CL 67736 当前状态为 MERGED")

        captured = {}

        def gerrit_handler(server, tool, params):
            captured["server"] = server
            captured["tool"] = tool
            return {"change_id": "67736", "status": "MERGED", "project": "test-project"}

        _mock_mcp_calls(monkeypatch, gerrit_handler)

        resp = await async_client.post("/api/chat/message", json={"message": "查一下 CL 67736 合并了吗"})
        assert resp.status_code == 200
        events = _parse_sse(resp.text)
        assert any("67736" in t for t in _text_contents(events))
        assert captured.get("server") == "gerrit_gerrit1"


class TestWebChatReadConfluence:
    """用户高频：在 web chat 读取 Confluence 页面。"""

    @pytest.mark.asyncio
    async def test_read_confluence_page_mcp(self, async_client, mock_llm_router, monkeypatch):
        """读取 Confluence 页面 → 引擎真实发起 MCP 查询 → 返回正文摘要。"""
        tool_call = {
            "id": "c1", "type": "function",
            "function": {
                "name": "call_mcp_tool",
                "arguments": json.dumps({
                    "server": "confluence", "tool": "get_page",
                    "params": {"page_id": "734329354"},
                }),
            },
        }
        _mock_llm_tool_sequence(
            monkeypatch, tool_call,
            "页面内容：项目分支链接包括 feature/tv-925009 等。",
        )

        captured = {}

        def confluence_handler(server, tool, params):
            captured["tool"] = tool
            return {"id": "734329354", "title": "项目分支链接", "content": "feature/tv-925009"}

        _mock_mcp_calls(monkeypatch, confluence_handler)

        resp = await async_client.post(
            "/api/chat/message",
            json={"message": "读取 Confluence 页面 734329354 的分支链接"},
        )
        assert resp.status_code == 200
        events = _parse_sse(resp.text)
        assert any("分支链接" in t for t in _text_contents(events))
        assert captured.get("tool") == "get_page"

    @pytest.mark.asyncio
    async def test_read_confluence_page_builtin(self, async_client, mock_llm_router, monkeypatch):
        """读取 Confluence 走内置工具（confluence_get_page_content）路径。"""
        tool_call = {
            "id": "c1", "type": "function",
            "function": {
                "name": "call_builtin_tool",
                "arguments": json.dumps({
                    "tool_name": "confluence_get_page_content",
                    "params": {"page_id": "734329354"},
                }),
            },
        }
        _mock_llm_tool_sequence(
            monkeypatch, tool_call,
            "Confluence 页面正文：项目分支链接这一列的 CL 状态见下。",
        )

        captured = {}

        async def fake_execute_tool(tool_name, params, context):
            captured["tool"] = tool_name
            return {"success": True, "content": "项目分支链接这一列的 CL 状态见下。"}

        monkeypatch.setattr(action_executor, "_execute_tool", fake_execute_tool)

        resp = await async_client.post(
            "/api/chat/message",
            json={"message": "读取 Confluence 页面 734329354 项目分支链接这一列的 cl"},
        )
        assert resp.status_code == 200
        events = _parse_sse(resp.text)
        assert any("分支链接" in t for t in _text_contents(events))
        assert captured.get("tool") == "confluence_get_page_content"


class TestWebChatDeliverFile:
    """用户高频：在 chat 里说「把 XXX 文件发给我」→ 文件以下载卡片回传。

    回归背景（2026-09-15）：用户在 chat 索要一个**已存在**的文件，Agent 无工具可调，
    回复「我无法直接在聊天框发附件」并反问交付方式。修复：新增内置工具 deliver_file
    （按路径/文件名定位文件）→ 引擎识别产出文件 → SSE 回传 type=file 下载卡片。
    """

    @staticmethod
    def _register_deliver_tool():
        """把真实 deliver_file 工具注册进执行器（测试环境不跑 app 启动流程）。"""
        from app.tools.file_delivery_tool import deliver_file
        action_executor.register_tool("deliver_file", deliver_file)

    @pytest.mark.asyncio
    async def test_deliver_existing_file_by_name(self, async_client, mock_llm_router, monkeypatch, tmp_path):
        """用户只给文件名 → 工具定位文件 → SSE 出现下载卡片，且链接可下载。"""
        target = tmp_path / "R+_Wave2_Wave3_Patch_TV-941901.xlsx"
        target.write_text("xlsx-bytes", encoding="utf-8")
        # 限定查找根目录到 tmp（避免扫描真实文件系统）
        monkeypatch.setattr("app.tools.file_delivery_tool._primary_roots", lambda: [str(tmp_path)])
        monkeypatch.setattr("app.tools.file_delivery_tool._fallback_roots", lambda: [])
        self._register_deliver_tool()

        tool_call = {
            "id": "c1", "type": "function",
            "function": {
                "name": "call_builtin_tool",
                "arguments": json.dumps({
                    "tool_name": "deliver_file",
                    "params": {"file_name": "R+_Wave2_Wave3_Patch_TV-941901.xlsx"},
                }),
            },
        }
        _mock_llm_tool_sequence(monkeypatch, tool_call, "文件已发送，请点击下方卡片下载。")

        resp = await async_client.post(
            "/api/chat/message",
            json={"message": "帮我把文件 R+_Wave2_Wave3_Patch_TV-941901.xlsx 直接发给我"},
        )

        assert resp.status_code == 200
        events = _parse_sse(resp.text)
        file_events = [e for e in events if e.get("type") == "file"]
        assert len(file_events) == 1
        assert file_events[0]["data"]["file_name"] == "R+_Wave2_Wave3_Patch_TV-941901.xlsx"
        assert file_events[0]["data"]["file_path"] == str(target)

        # 回传的下载链接真实可用（前端点击即走这里）
        download = await async_client.get(file_events[0]["data"]["download_url"])
        assert download.status_code == 200
        assert download.text == "xlsx-bytes"

    @pytest.mark.asyncio
    async def test_deliver_missing_file_reports_honestly(self, async_client, mock_llm_router, monkeypatch, tmp_path):
        """文件不存在 → 不产生下载卡片，工具返回可读错误交给 LLM 如实告知。"""
        monkeypatch.setattr("app.tools.file_delivery_tool._primary_roots", lambda: [str(tmp_path)])
        monkeypatch.setattr("app.tools.file_delivery_tool._fallback_roots", lambda: [])
        self._register_deliver_tool()

        tool_call = {
            "id": "c1", "type": "function",
            "function": {
                "name": "call_builtin_tool",
                "arguments": json.dumps({
                    "tool_name": "deliver_file",
                    "params": {"file_name": "不存在_xyz.xlsx"},
                }),
            },
        }
        _mock_llm_tool_sequence(monkeypatch, tool_call, "未找到该文件，请确认文件名或提供完整路径。")

        resp = await async_client.post(
            "/api/chat/message", json={"message": "把 不存在_xyz.xlsx 发给我"},
        )

        assert resp.status_code == 200
        events = _parse_sse(resp.text)
        assert not [e for e in events if e.get("type") == "file"]
        assert any("未找到该文件" in t for t in _text_contents(events))
