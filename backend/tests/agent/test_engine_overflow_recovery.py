"""
上下文溢出压缩恢复测试（层 B/C，2026-09-08）。

覆盖：
- _is_context_overflow：details 打标 / 特征串兜底 / 普通错误不误判
- _compact_messages_for_overflow：骨架+尾轮保留、中间压成摘要消息、tool 无孤儿、
  keep_recent=0 强制为 1、无可压缩返回 None、LLM 失败降级机械摘要
- 主循环端到端：首轮工具 → 次轮溢出 → 压缩重试成功（thinking 通知/spill/memory/无 error）
- 压缩耗尽（一直溢出）→ 明确报错建议新对话
"""

import pytest

from app.core.exceptions import LLMException
from app.services.agent.engine import AgentEngine



def _overflow_error() -> LLMException:
    return LLMException(
        "maximum context length exceeded。原始错误: context_length_exceeded",
        provider="commercial",
        details={"model": "deepseek-chat", "overflow": True},
    )


def _mk_messages(n_rounds: int = 4) -> list:
    """构造 system + user + n 轮 [assistant(tool_calls)+tool]。"""
    msgs = [
        {"role": "system", "content": "你是助手"},
        {"role": "user", "content": "帮我分析 TV-946422"},
    ]
    for i in range(n_rounds):
        msgs.append({
            "role": "assistant", "content": None,
            "tool_calls": [{"id": f"call_{i}", "type": "function",
                            "function": {"name": "jira__search_issues", "arguments": "{}"}}],
        })
        msgs.append({"role": "tool", "tool_call_id": f"call_{i}", "content": "查询结果 " * 100})
    return msgs


class TestIsContextOverflow:
    """溢出错误分类。"""

    def test_details_overflow_flag(self):
        assert AgentEngine._is_context_overflow(_overflow_error()) is True

    def test_keyword_fallback_without_details(self):
        e = Exception("Error: maximum context length is 65536 tokens")
        assert AgentEngine._is_context_overflow(e) is True

    def test_normal_error_not_overflow(self):
        assert AgentEngine._is_context_overflow(Exception("connection refused")) is False
        assert AgentEngine._is_context_overflow(Exception("认证失败 unauthorized")) is False


class TestCompactMessages:
    """压缩核心逻辑。"""

    async def _compact(self, engine, msgs, keep_recent=2, user_id=1):
        # 隔离副作用：spill / memory 打补丁捕获
        import app.services.context_spill as cs
        captured = {"spill": [], "memory": []}
        orig_save, orig_mem = cs.save_spill, engine._save_memory

        async def fake_spill(**kw):
            captured["spill"].append(kw)
            return "testspill001"

        async def fake_mem(key, content, category, **kw):
            captured["memory"].append((key, content))
            return {"success": True}

        cs.save_spill, engine._save_memory = fake_spill, fake_mem
        try:
            new_msgs = await engine._compact_messages_for_overflow(
                msgs, keep_recent=keep_recent, user_id=user_id, db=None, llm_override={}
            )
        finally:
            cs.save_spill, engine._save_memory = orig_save, orig_mem
        return new_msgs, captured

    @pytest.mark.asyncio
    async def test_compact_preserves_skeleton_and_tail(self):
        engine = AgentEngine()
        msgs = _mk_messages(4)
        new_msgs, captured = await self._compact(engine, msgs, keep_recent=1)

        assert new_msgs is not None
        # 骨架：system + user
        assert new_msgs[0]["role"] == "system"
        assert new_msgs[1]["role"] == "user"
        # 中间压成 1 条 assistant 摘要
        assert any(
            m["role"] == "assistant" and "已压缩" in str(m.get("content", ""))
            for m in new_msgs
        )
        # 尾部保留 1 轮（assistant tool_calls + tool）
        tail_assistant = [m for m in new_msgs if m.get("tool_calls")]
        assert len(tail_assistant) == 1
        # 无孤儿 tool：每条 tool 前是带 tool_calls 的 assistant
        for i, m in enumerate(new_msgs):
            if m["role"] == "tool":
                assert new_msgs[i - 1].get("tool_calls"), f"orphan tool at index {i}"
        # 摘要进 memory + 原文 spill
        assert len(captured["memory"]) == 1
        assert len(captured["spill"]) == 1

    @pytest.mark.asyncio
    async def test_keep_recent_zero_forced_to_one(self):
        """keep_recent=0 强制保留最后 1 轮（保护执行中的 tool_calls）。"""
        engine = AgentEngine()
        msgs = _mk_messages(3)
        new_msgs, _ = await self._compact(engine, msgs, keep_recent=0)
        assert new_msgs is not None
        # 最后 1 轮仍在
        assert new_msgs[-1]["role"] == "tool"
        assert new_msgs[-2].get("tool_calls")

    @pytest.mark.asyncio
    async def test_nothing_compactable_returns_none(self):
        engine = AgentEngine()
        # 只有 system + user + 1 轮（当前轮），中间为空
        msgs = _mk_messages(1)
        result, _ = await self._compact(engine, msgs, keep_recent=1)
        assert result is None

    @pytest.mark.asyncio
    async def test_llm_failure_falls_back_to_mechanical(self):
        """LLM 压缩失败 → 机械摘要兜底（恢复流程不依赖 LLM 可用）。"""
        engine = AgentEngine()
        msgs = _mk_messages(3)
        from app.services.llm import router as llm_router_module

        async def failing_summary(task_type, messages, **kw):
            raise Exception("LLM unavailable")

        orig = llm_router_module.llm_router.route_and_complete
        llm_router_module.llm_router.route_and_complete = failing_summary
        try:
            new_msgs, captured = await self._compact(engine, msgs, keep_recent=1)
        finally:
            llm_router_module.llm_router.route_and_complete = orig
        assert new_msgs is not None
        note = next(m for m in new_msgs if "已压缩" in str(m.get("content", "")))
        assert note["content"]  # 机械摘要非空


class TestOverflowRecoveryE2E:
    """主循环端到端：溢出 → 压缩 → 重试。"""

    @pytest.mark.asyncio
    async def test_compact_retry_recovers(self, mock_llm_router):
        """首轮工具 → 次轮溢出 → 压缩重试成功：thinking 通知 + 无 error + spill + memory。"""
        from app.services.llm import router as rm
        import app.services.context_spill as cs

        engine = AgentEngine()
        calls = {"n": 0}
        spills, memories = [], []

        async def mock_with_tools(task_type, messages, tools, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                # 第 1 轮：发起工具调用（产生工具轮，供压缩）
                return None, [{"id": "call_x", "type": "function",
                               "function": {"name": "memory_search", "arguments": '{"query": "x"}'}}]
            if calls["n"] == 2:
                # 第 2 轮：上下文溢出
                raise _overflow_error()
            return "分析完成：原因是 Y，下一步做 Z", None

        async def fake_tool_exec(func_name, args, context, **kw):
            return {"success": True, "matches": [], "message": "ok"}

        async def fake_spill(**kw):
            spills.append(kw)
            return "e2espill001"

        async def fake_mem(key, content, category, **kw):
            memories.append(content)
            return {"success": True}

        orig_router = rm.llm_router.route_and_complete_with_tools
        orig_exec = engine._execute_function_call
        orig_save = cs.save_spill
        orig_mem = engine._save_memory
        rm.llm_router.route_and_complete_with_tools = mock_with_tools
        engine._execute_function_call = fake_tool_exec
        cs.save_spill = fake_spill
        engine._save_memory = fake_mem
        try:
            responses = []
            async for resp in engine.process_message("分析 TV-946422", user_id=1):
                responses.append(resp)
        finally:
            rm.llm_router.route_and_complete_with_tools = orig_router
            engine._execute_function_call = orig_exec
            cs.save_spill = orig_save
            engine._save_memory = orig_mem

        types = [r.type for r in responses]
        # 压缩通知可见（不静默原则）
        assert any(r.type == "thinking" and "摘要压缩" in str(r.content) for r in responses)
        # 最终成功回答，无 error
        assert any(r.type == "text" and "分析完成" in str(r.content) for r in responses)
        assert "error" not in types
        assert types[-1] == "complete"
        # spill + memory 发生
        assert len(spills) >= 1
        assert len(memories) >= 1

    @pytest.mark.asyncio
    async def test_unrecoverable_overflow_advises_new_conversation(self, mock_llm_router):
        """一直溢出 → 明确报错建议新对话（压缩耗尽后优雅退出）。"""
        from app.services.llm import router as rm
        import app.services.context_spill as cs

        engine = AgentEngine()
        calls = {"n": 0}

        async def mock_with_tools(task_type, messages, tools, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return None, [{"id": "call_x", "type": "function",
                               "function": {"name": "memory_search", "arguments": '{"query": "x"}'}}]
            raise _overflow_error()  # 之后永远溢出

        async def fake_tool_exec(func_name, args, context, **kw):
            return {"success": True, "matches": [], "message": "ok"}

        async def fake_spill(**kw):
            return "e2espill002"

        orig_router = rm.llm_router.route_and_complete_with_tools
        orig_exec = engine._execute_function_call
        orig_save = cs.save_spill
        rm.llm_router.route_and_complete_with_tools = mock_with_tools
        engine._execute_function_call = fake_tool_exec
        cs.save_spill = fake_spill
        try:
            responses = []
            async for resp in engine.process_message("分析大任务", user_id=1):
                responses.append(resp)
        finally:
            rm.llm_router.route_and_complete_with_tools = orig_router
            engine._execute_function_call = orig_exec
            cs.save_spill = orig_save

        types = [r.type for r in responses]
        assert "error" in types
        err = next(r for r in responses if r.type == "error")
        assert "新开会话" in str(err.content) or "上限" in str(err.content)
        assert types[-1] == "complete"
