"""飞书适配器对新响应类型的处理（2026-09-15 文件回传改动的相关模块回归）。

背景：引擎新增了两类 AgentResponse —— ``task_submitted``（长任务提交后台）与
``file``（Agent 产出的文件卡片）。飞书侧必须：
  - 收到 task_submitted 时给出即时反馈，**但不能吞掉引擎随后的最终答复**；
  - 只有 task_submitted 时不要补发"处理完成。"占位消息；
  - file 类型响应不影响文本/卡片发送（飞书暂不支持文件回传）。

注意：``app.services.feishu.adapter`` 在 conftest 的全局 AsyncSessionLocal 替换清单中，
本测试的 DB 写入落在隔离的临时库。
"""

import pytest

from app.services.agent.types import AgentResponse
from app.services.feishu.adapter import FeishuAdapter
from app.services.feishu.converter import ParsedMessage


def _msg() -> ParsedMessage:
    return ParsedMessage(
        chat_id="oc_test", chat_type="p2p", open_id="ou_test",
        text="生成项目周报", message_id="om_1", message_type="text",
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
        self.sent.append({"receive_id": receive_id, "msg_type": msg_type, "content": content})
        return True


@pytest.fixture
def adapter_factory():
    def _make(responses):
        client = _FakeClient()
        adapter = FeishuAdapter(client=client, agent_engine=_FakeAgent(responses))
        return adapter, client
    return _make


class TestTaskSubmitted:
    """task_submitted（长任务提交后台）处理。"""

    @pytest.mark.asyncio
    async def test_submit_notice_does_not_swallow_final_answer(self, adapter_factory, db_engine):
        """提交后台后，引擎的最终答复仍需发给用户（改动前 return 会吞掉）。"""
        adapter, client = adapter_factory([
            AgentResponse(type="thinking", content="正在执行 execute_skill..."),
            AgentResponse(type="task_submitted", content="已提交",
                          data={"task_id": "t-1", "skill_name": "jira-confluence-report"}),
            AgentResponse(type="text", content="报告任务已提交，稍后完成。"),
            AgentResponse(type="complete"),
        ])

        await adapter._process_and_reply(_msg())

        texts = [c["content"] for c in client.sent]
        assert len(texts) == 2
        assert "已提交到后台执行" in texts[0]
        assert "报告任务已提交，稍后完成。" in texts[1]

    @pytest.mark.asyncio
    async def test_submit_only_sends_single_message(self, adapter_factory, db_engine):
        """只有 task_submitted（无其他内容）→ 只发一条，不补"处理完成。"占位。"""
        adapter, client = adapter_factory([
            AgentResponse(type="task_submitted", data={"task_id": "t-2"}),
            AgentResponse(type="complete"),
        ])

        await adapter._process_and_reply(_msg())

        assert len(client.sent) == 1
        assert "任务已提交到后台执行" in client.sent[0]["content"]

    @pytest.mark.asyncio
    async def test_submit_notice_uses_data_message_when_present(self, adapter_factory, db_engine):
        """data 里带 message 时优先使用（便于后续接入完成推送时自定义文案）。"""
        adapter, client = adapter_factory([
            AgentResponse(type="task_submitted", data={"task_id": "t-3", "message": "周报任务已排队"}),
            AgentResponse(type="complete"),
        ])

        await adapter._process_and_reply(_msg())

        assert "周报任务已排队" in client.sent[0]["content"]


class TestFileResponseType:
    """file（Agent 产出文件）类型响应。"""

    @pytest.mark.asyncio
    async def test_file_response_does_not_break_text_delivery(self, adapter_factory, db_engine):
        """file 类型被忽略（飞书不回传文件），同轮的文本答复照常发送。"""
        adapter, client = adapter_factory([
            AgentResponse(type="file", content="已生成文件：周报.html",
                          data={"file_name": "周报.html", "download_url": "/api/files/download?token=x"}),
            AgentResponse(type="text", content="周报已生成，可在 Web Chat 下载。"),
            AgentResponse(type="complete"),
        ])

        await adapter._process_and_reply(_msg())

        texts = [c["content"] for c in client.sent]
        assert any("周报已生成，可在 Web Chat 下载。" in t for t in texts)
