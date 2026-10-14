"""
Feishu 消息格式转换器

职责:
  1. 飞书事件 JSON → ParsedMessage (内部统一格式)
  2. AgentResponse 流 → 飞书消息内容 (text / interactive card)

飞书文本消息 content 格式:  {"text": "消息内容"}
飞书卡片消息 content 格式:  {"config":..., "elements":..., "header":...}
"""

import json
import logging
from dataclasses import dataclass, field
from typing import List, Optional

from app.services.agent.types import AgentResponse

logger = logging.getLogger(__name__)


@dataclass
class ParsedMessage:
    """从飞书事件中提取的统一消息结构."""
    chat_id: str               # 会话 ID (oc_xxx)
    chat_type: str             # p2p | group
    open_id: str               # 发送者 open_id
    text: str                  # 纯文本内容
    message_id: str            # 消息 ID (om_xxx)
    message_type: str          # text | image | file 等
    raw_event: dict = field(default_factory=dict)  # 原始事件 JSON


def parse_event(event: dict) -> Optional[ParsedMessage]:
    """解析飞书 WebSocket 推送的 im.message.receive_v1 事件.

    Args:
        event: WebSocket 收到的完整事件 dict

    Returns:
        ParsedMessage 或 None (无法解析时)
    """
    try:
        header = event.get("header", {})
        event_type = header.get("event_type", "")
        if event_type != "im.message.receive_v1":
            logger.debug("Ignored event type: %s", event_type)
            return None

        evt = event.get("event", {})
        msg = evt.get("message", {})

        chat_id = msg.get("chat_id", "")
        chat_type = msg.get("chat_type", "p2p")
        message_id = msg.get("message_id", "")
        message_type = msg.get("message_type", "text")

        # 发送者
        sender = evt.get("sender", {})
        sender_id = sender.get("sender_id", {})
        open_id = sender_id.get("open_id", "")

        # 消息内容 — message_type 决定解析方式
        raw_content = msg.get("content", "{}")
        if isinstance(raw_content, str):
            content_dict = json.loads(raw_content)
        elif isinstance(raw_content, dict):
            content_dict = raw_content
        else:
            content_dict = {}

        text = ""
        if message_type == "text":
            text = content_dict.get("text", "")
        elif message_type == "image":
            text = f"[图片: {content_dict.get('image_key', '')}]"
        elif message_type == "file":
            text = f"[文件: {content_dict.get('file_name', '未知文件')}]"
        else:
            text = str(content_dict)

        # 群聊 @机器人 消息: 移除 @ 前缀
        if chat_type == "group":
            # 飞书@格式: @_user_1 或 @xxx
            import re
            text = re.sub(r'@_user_\d+\s*', '', text).strip()
            text = re.sub(r'@[一-龥\w]+\s*', '', text).strip()

        if not text:
            logger.info("Empty text from feishu event, message_type=%s", message_type)
            return None

        return ParsedMessage(
            chat_id=chat_id,
            chat_type=chat_type,
            open_id=open_id,
            text=text,
            message_id=message_id,
            message_type=message_type,
            raw_event=event,
        )

    except Exception as e:
        logger.warning("Failed to parse feishu event: %s", e, exc_info=True)
        return None


# ── AgentResponse → 飞书消息格式 ──


def to_feishu_text(content: str) -> str:
    """转换纯文本为飞书 text 消息 content 字段值.

    飞书文本内容有格式限制:
      - 普通文本直接放在 {"text": "..."} 中
      - 支持简单 markdown: 加粗(**)、斜体(*)、换行(\n)
    """
    escaped = content.replace("\\", "\\\\").replace('"', '\\"')
    return json.dumps({"text": escaped}, ensure_ascii=False)


def to_feishu_card(responses: List[AgentResponse]) -> Optional[str]:
    """将多个 AgentResponse (含 card 类型) 转换为飞书 interactive 卡片.

    卡片用于展示结构化数据：表格、状态标签、风险警告等。
    如果 responses 中没有 card 类型则返回 None.
    """
    cards = [r for r in responses if r.type == "card"]
    if not cards:
        return None

    # 合并多个 card 内容为单一卡片
    elements = []
    for card in cards:
        data = card.data or {}
        title = data.get("title", card.content or "")
        body = data.get("content", data.get("body", ""))

        if title:
            elements.append({
                "tag": "markdown",
                "content": f"**{title}**",
            })
        if body:
            if isinstance(body, str):
                elements.append({"tag": "markdown", "content": body[:2000]})
            elif isinstance(body, list):
                for item in body[:10]:
                    elements.append({"tag": "markdown", "content": str(item)[:2000]})

    if not elements:
        return None

    card_payload = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "Project Agent 报告"},
            "template": "blue",
        },
        "elements": elements,
    }

    return json.dumps(card_payload, ensure_ascii=False)


def split_long_text(text: str, max_len: int = 2000) -> List[str]:
    """将长文本按段落边界拆分为多个不超过 max_len 的块."""
    if len(text) <= max_len:
        return [text]
    chunks = []
    while len(text) > max_len:
        cutoff = text[:max_len]
        # 尝试在段落边界处分割
        split_at = cutoff.rfind('\n\n')
        if split_at < max_len * 0.4:
            split_at = cutoff.rfind('\n')
        if split_at < max_len * 0.4:
            split_at = max_len
        chunks.append(text[:split_at].rstrip())
        text = text[split_at:].lstrip()
    if text:
        chunks.append(text)
    return chunks


def collect_response_text(responses: List[AgentResponse], max_len: int = 50000) -> str:
    """从 AgentResponse 列表中收集最终要发送的文本.

    规则:
      - 过滤掉 type=thinking (推理中间步骤)
      - 取 type=text 的组合
      - type=error 作为错误文本
      - 超长截断
    """
    parts = []
    for r in responses:
        if r.type == "text" and r.content:
            parts.append(r.content)
        elif r.type == "error" and r.content:
            parts.append(f"❌ {r.content}")

    text = "\n\n".join(parts) if parts else "处理完成。"
    if len(text) > max_len:
        text = text[:max_len] + f"\n\n...（内容过长，仅显示前{max_len}字）"
    return text


def estimate_card_or_text(responses: List[AgentResponse]) -> tuple:
    """判断用卡片还是文本发送，返回 (msg_type, content).

    Returns:
        (msg_type: str, content: str)
        msg_type 为 "interactive" 或 "text"
    """
    card_content = to_feishu_card(responses)
    if card_content:
        return "interactive", card_content

    text = collect_response_text(responses)
    return "text", to_feishu_text(text)
