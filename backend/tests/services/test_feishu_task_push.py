"""飞书后台任务完成推送（2026-09-30）。

修复链路：飞书下达下载任务后，任务完成时主动向飞书推送结果。
  adapter 收到 task_submitted → 注册完成回调到全局 worker（闭包捕获 msg）
  → 任务到达终态 → _push_skill_result 推送结果（markdown 表格转纯文本）。

同时覆盖：
  - 引擎收到的 conversation_id 是真实 DB id（此前传 oc_xxx 静默失败）

monkeypatch ``task_queue.get_worker`` 为测试专用 worker，隔离全局单例。
``db_engine`` 必须参与（adapter 需要建会话、worker 落库隔离）。
"""

import asyncio

import pytest
import pytest_asyncio

from app.services import task_queue as tq
from app.services.agent.types import AgentResponse
from app.services.feishu.adapter import FeishuAdapter
from app.services.feishu.converter import ParsedMessage

_MAX_WAIT = 5.0
_POLL = 0.02


def _msg() -> ParsedMessage:
    return ParsedMessage(
        chat_id="oc_push", chat_type="p2p", open_id="ou_push",
        text="下载最新构建", message_id="om_1", message_type="text",
    )


class _FakeAgent:
    """按预设脚本产出 AgentResponse 的假引擎。"""

    def __init__(self, responses):
        self._responses = responses

    async def process_message(self, **kwargs):
        for r in self._responses:
            yield r


class _FakeClient:
    def __init__(self):
        self.sent = []

    async def send_message(self, receive_id, msg_type, content, receive_id_type):
        self.sent.append({"receive_id": receive_id, "msg_type": msg_type,
                          "content": content})
        return True


def _make_adapter(responses):
    client = _FakeClient()
    adapter = FeishuAdapter(client=client, agent_engine=_FakeAgent(responses))
    return adapter, client


@pytest_asyncio.fixture
async def fresh_worker(monkeypatch):
    """测试专用全局 worker：adapter 内 late-import 的 get_worker 指向它。"""
    worker = tq.BackgroundTaskWorker(max_concurrent=2)
    worker.start()

    async def _get():
        return worker

    monkeypatch.setattr(tq, "get_worker", _get)
    yield worker
    await worker.stop()


async def _wait_until(predicate, timeout: float = _MAX_WAIT) -> bool:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(_POLL)
    return predicate()


class TestFeishuTaskPush:
    """任务完成 → 主动推送结果到飞书。"""

    @pytest.mark.asyncio
    async def test_completed_task_pushes_immediately(self, fresh_worker, db_engine):
        """任务已完成再注册（竞态兜底）→ 立即推送结果。"""

        async def _handler(params, ctx):
            return {"message": "构建 16453281 上传 FTP 完成"}

        task_id = await fresh_worker.submit(_handler, {})
        loop = asyncio.get_event_loop()
        deadline = loop.time() + _MAX_WAIT
        while loop.time() < deadline:
            if (await fresh_worker.get_status(task_id))["status"] == "done":
                break
            await asyncio.sleep(_POLL)

        adapter, client = _make_adapter([
            AgentResponse(type="task_submitted", data={"task_id": task_id}),
            AgentResponse(type="complete"),
        ])
        await adapter._process_and_reply(_msg())

        texts = [c["content"] for c in client.sent]
        assert any("任务已提交到后台执行" in t for t in texts)
        assert any("构建 16453281 上传 FTP 完成" in t for t in texts)

    @pytest.mark.asyncio
    async def test_callback_fires_when_task_completes_later(self, fresh_worker, db_engine):
        """注册时任务未完成 → 等任务跑完后推送。"""

        gate = asyncio.Event()

        async def _slow(params, ctx):
            await gate.wait()
            return {"message": "后台任务结果：5/5 产物已上传"}

        task_id = await fresh_worker.submit(_slow, {})
        # 等进入 running，保证注册发生在执行期间
        loop = asyncio.get_event_loop()
        deadline = loop.time() + _MAX_WAIT
        while loop.time() < deadline:
            if (await fresh_worker.get_status(task_id))["status"] == "running":
                break
            await asyncio.sleep(_POLL)

        adapter, client = _make_adapter([
            AgentResponse(type="task_submitted", data={"task_id": task_id}),
            AgentResponse(type="complete"),
        ])
        await adapter._process_and_reply(_msg())

        # 注册后、任务完成前：只有"已提交"一条
        assert len(client.sent) == 1

        gate.set()
        assert await _wait_until(lambda: len(client.sent) >= 2)
        assert any("后台任务结果：5/5 产物已上传" in c["content"] for c in client.sent)

    @pytest.mark.asyncio
    async def test_push_result_converts_markdown_table(self, fresh_worker, db_engine):
        """ci_build 结果是 markdown 表格 → 推送时转成可读文本。"""

        async def _handler(params, ctx):
            return {"message": (
                "| 产物 | 大小 | 状态 |\n"
                "| --- | --- | --- |\n"
                "| CHIPX1.img | 6.0GB | 已上传 |\n"
                "| ota.zip | 1.2GB | 已上传 |\n"
                "全部完成"
            )}

        task_id = await fresh_worker.submit(_handler, {})
        loop = asyncio.get_event_loop()
        deadline = loop.time() + _MAX_WAIT
        while loop.time() < deadline:
            if (await fresh_worker.get_status(task_id))["status"] == "done":
                break
            await asyncio.sleep(_POLL)

        adapter, client = _make_adapter([
            AgentResponse(type="task_submitted", data={"task_id": task_id}),
            AgentResponse(type="complete"),
        ])
        await adapter._process_and_reply(_msg())

        pushed = client.sent[-1]["content"]
        assert "产物: CHIPX1.img  大小: 6.0GB  状态: 已上传" in pushed
        assert "| --- |" not in pushed
        assert "全部完成" in pushed


class TestConversationIdResolution:
    """飞书 chat_id → 真实 DB conversation.id 传给引擎。"""

    @pytest.mark.asyncio
    async def test_engine_receives_db_conversation_id(self, fresh_worker, db_engine):
        captured: dict = {}

        class _CapturingAgent:
            async def process_message(self, **kwargs):
                captured.update(kwargs)
                yield AgentResponse(type="text", content="ok")
                yield AgentResponse(type="complete")

        client = _FakeClient()
        adapter = FeishuAdapter(client=client, agent_engine=_CapturingAgent())
        await adapter._process_and_reply(_msg())

        conv_id = captured.get("conversation_id")
        assert conv_id is not None
        assert str(conv_id).isdigit(), f"应为真实 DB id，实际: {conv_id!r}"
        # chat_id 已建立会话映射（引擎跑完后持久化阶段复用缓存）
        assert "oc_push" in adapter._conv_cache
        assert adapter._conv_cache["oc_push"] == int(conv_id)


class TestMdTableToText:
    """markdown 表格 → 飞书可读文本（纯函数）。"""

    def test_table_block_converted(self):
        md = (
            "构建信息\n\n"
            "| 项 | 值 |\n"
            "| --- | --- |\n"
            "| build | 16453281 |\n"
            "| 签名 | releasekey |\n"
            "\n全部完成"
        )
        out = FeishuAdapter._md_table_to_text(md)
        # 表头作为标签与每行数据配对
        assert "项: build  值: 16453281" in out
        assert "项: 签名  值: releasekey" in out
        assert "全部完成" in out
        assert "|" not in out          # 表格行全部转换，无残留管道符
        assert "---" not in out        # 分隔行被丢弃

    def test_non_table_lines_kept(self):
        md = "普通文本\n**加粗** 保留"
        assert FeishuAdapter._md_table_to_text(md) == md

    def test_single_row_table(self):
        md = "| 只有 | 表头 |\n| --- | --- |"
        out = FeishuAdapter._md_table_to_text(md)
        assert "|" not in out
