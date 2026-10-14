"""
Feishu Adapter — 飞书事件 → AgentEngine → 回复 的核心编排

处理流程:
  1. ws_client 收到事件 → 调用 handle_event()
  2. 解析事件为 ParsedMessage (文本/chat_id/open_id/chat_type)
  3. 调用 AgentEngine.process_message() (收集模式 — 消费完完整流)
  4. 过滤 thinking 消息，保留 text/card/error
  5. 保存对话到 DB (conversation + messages)
  6. 转换成飞书消息格式 → 通过 client API 发送回复，根据 chat_type 选择
     私聊(P2P) → receive_id=open_id, receive_id_type=open_id
     群聊(Group) → receive_id=chat_id, receive_id_type=chat_id
"""

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.services.feishu.client import FeishuApiClient
from app.services.feishu.converter import (
    ParsedMessage,
    parse_event,
    estimate_card_or_text,
    collect_response_text,
    split_long_text,
    to_feishu_text,
)
from app.db.session import AsyncSessionLocal
from app.crud.conversation import create_conversation, create_message
from app.models.feishu_bind import FeishuBindCode
from app.models.user import User

logger = logging.getLogger(__name__)

# Dev user ID used for Feishu conversation persistence (same as web chat)
_FEISHU_USER_ID = 1


class FeishuAdapter:
    """飞书消息适配器 — 飞书 ↔ AgentEngine 之间的桥梁."""

    def __init__(self, client: FeishuApiClient, agent_engine):
        """
        Args:
            client: FeishuApiClient 实例
            agent_engine: AgentEngine 实例
        """
        self._client = client
        self._agent = agent_engine
        # Cache: feishu chat_id → DB conversation.id (persisted across messages)
        self._conv_cache: Dict[str, int] = {}

    # ── 辅助: 根据 chat_type 确定 receive_id ──

    @staticmethod
    def _receive_info(msg: ParsedMessage) -> tuple:
        """根据 chat_type 返回 (receive_id, receive_id_type).

        私聊 → open_id (open_id 类型)
        群聊 → chat_id (chat_id 类型)
        """
        if msg.chat_type == "group":
            return msg.chat_id, "chat_id"
        return msg.open_id, "open_id"

    # ── 对话持久化 ──

    async def _ensure_conversation(self, db, chat_id: str, first_message: str, user_id: int) -> Optional[int]:
        """获取飞书 chat_id 对应的 DB conversation.id，不存在则创建.

        缓存命中 → 直接返回
        缓存未命中 → DB 创建新 conversation → 写入缓存 → 返回
        """
        if chat_id in self._conv_cache:
            return self._conv_cache[chat_id]

        try:
            title = first_message[:100] if first_message else "飞书对话"
            conv = await create_conversation(db, user_id=user_id, title=title)
            self._conv_cache[chat_id] = conv.id
            return conv.id
        except Exception as e:
            logger.error("Failed to create feishu conversation: %s", e)
            return None

    # ── 事件处理 ──

    async def handle_event(self, event: dict):
        """处理一个飞书 WebSocket 事件.

        由 ws_client 在收到 im.message.receive_v1 时调用.

        Args:
            event: 完整事件 dict (已经过 ws_client 的 ACK 处理)
        """
        # 1. 解析事件
        parsed = parse_event(event)
        if parsed is None:
            return

        logger.info("Feishu msg from %s (%s): %.60s",
                     parsed.open_id, parsed.chat_type, parsed.text)

        # 2. 异步执行 Agent 处理
        await self._process_and_reply(parsed)

    async def _resolve_user_id(self, open_id: str) -> int:
        """Map Feishu open_id to DB user_id, falling back to default Feishu user."""
        if not open_id:
            return _FEISHU_USER_ID
        try:
            async with AsyncSessionLocal() as db:
                from sqlalchemy import select
                from app.models import User
                result = await db.execute(select(User.id).where(
                    User.feishu_open_id == open_id, User.is_active == True
                ))
                user = result.scalar_one_or_none()
                if user:
                    return user.id
        except Exception as e:
            logger.warning("Failed to resolve feishu user %s: %s", open_id, e)
        return _FEISHU_USER_ID

    # ── 飞书账号绑定 ──

    async def _handle_bind_code(self, msg: ParsedMessage) -> bool:
        """处理飞书绑定验证码.

        验证流程：
        1. 查找 FeishuBindCode 表中未使用、未过期的匹配 code
        2. 更新对应用户的 feishu_open_id
        3. 标记验证码为已使用
        4. 回复飞书消息

        Returns:
            True 表示已处理（验证码有效），False 表示未匹配或异常
        """
        code = msg.text.strip()
        try:
            async with AsyncSessionLocal() as db:
                # 查找有效验证码
                from sqlalchemy import select
                result = await db.execute(
                    select(FeishuBindCode).where(
                        FeishuBindCode.code == code,
                        FeishuBindCode.used == False,
                        FeishuBindCode.expires_at > datetime.now(timezone.utc),
                    )
                )
                bind_code = result.scalar_one_or_none()

                if not bind_code:
                    await self._send(msg, "❌ 验证码无效或已过期，请在 Web 端重新获取。")
                    return True

                # 更新用户 feishu_open_id
                user = await db.get(User, bind_code.user_id)
                if not user or not user.is_active:
                    await self._send(msg, "❌ 关联用户不存在或已禁用。")
                    return True

                # 检查该用户是否已被其他飞书账号绑定
                if user.feishu_open_id:
                    await self._send(msg, "❌ 你的 Web 账号已经绑定了其他飞书账号。")
                    return True

                # 检查该 open_id 是否已被其他用户绑定
                existing = await db.execute(
                    select(User).where(
                        User.feishu_open_id == msg.open_id,
                        User.is_active == True,
                    )
                )
                if existing.scalar_one_or_none():
                    await self._send(msg, "❌ 你的飞书账号已经被其他用户绑定了，请先解绑。")
                    return True

                # 执行绑定
                user.feishu_open_id = msg.open_id
                bind_code.used = True
                await db.commit()

                logger.info(
                    "Feishu bind success: user=%s, open_id=%s",
                    bind_code.user_id, msg.open_id,
                )

                await self._send(
                    msg,
                    "🎉 飞书账号绑定成功！你可以开始使用 AI 助手了。\n"
                    "现在你的数据和配置将与 Web 端同步。",
                )
                return True

        except Exception as e:
            logger.error("Feishu bind code error: %s", e, exc_info=True)
            await self._send(msg, "❌ 绑定处理出错，请稍后重试。")
            return True

    async def _process_and_reply(self, msg: ParsedMessage):
        """调用 AgentEngine 并发送回复.

        流程: AgentEngine 消费 → 收集全部 → 持久化 → 转换格式 → 发送飞书消息.
        """
        # ★ 检测是否为飞书绑定验证码（6位纯数字，且不是群聊消息）
        if msg.chat_type != "group" and re.match(r'^\d{6}$', msg.text.strip()):
            handled = await self._handle_bind_code(msg)
            if handled:
                return  # 绑定流程已处理完毕，不再走 AgentEngine

        user_id = await self._resolve_user_id(msg.open_id)

        # 预解析 chat_id → 真实 DB conversation.id 再传给引擎。
        # 此前直接传飞书 chat_id（oc_xxx），引擎 int() 解析静默失败，
        # 导致后台任务（ci_build 下载等）完成结果无法写回会话。
        conv_id: Optional[int] = None
        try:
            async with AsyncSessionLocal() as db:
                conv_id = await self._ensure_conversation(db, msg.chat_id, msg.text, user_id)
        except Exception as e:
            logger.warning("Failed to resolve feishu conversation for %s: %s",
                           msg.chat_id, e)

        # 使用 async with 安全获取 DB session，自动 cleanup
        async with AsyncSessionLocal() as db:
            responses = []
            try:
                async for resp in self._agent.process_message(
                    message=msg.text,
                    conversation_id=str(conv_id) if conv_id else None,
                    user_id=user_id,
                    db=db,
                ):
                    responses.append(resp)
            except Exception as e:
                logger.error("Feishu AgentEngine error: %s", e, exc_info=True)
                await self._send(msg, f"❌ 处理出错: {str(e)}")
                return

            # ── 持久化对话 ──
            if responses:
                conv_id = await self._ensure_conversation(db, msg.chat_id, msg.text, user_id)
                if conv_id:
                    try:
                        await create_message(db, conversation_id=conv_id, role="user",
                                             content=msg.text, message_type="text")
                        for resp in responses:
                            if resp.type in ("text", "card"):
                                await create_message(db, conversation_id=conv_id, role="assistant",
                                                     content=resp.content or "",
                                                     message_type=resp.type,
                                                     extra_data=resp.data or {})
                    except Exception as e:
                        logger.warning("Failed to persist feishu messages: %s", e)

        # DB session 已自动关闭，后续发送不依赖 db

        # 检查是否有 task_submitted（长任务已提交后台执行）
        # 先给用户一个即时反馈，但**不能提前 return** —— 引擎在提交后还会给出最终答复，
        # 提前返回会把那段答复吞掉（改动前 task_submitted 从未到达这里，因此无此问题）。
        # 随后注册完成回调：任务跑完后主动把结果推送到飞书（推送制渠道，
        # 不能像 Web 那样靠前端轮询/会话历史送达）。
        task_submitted = next((r for r in responses if r.type == "task_submitted"), None)
        if task_submitted is not None:
            await self._send(
                msg,
                (task_submitted.data or {}).get("message") or "⏳ 任务已提交到后台执行。",
            )
            # 同轮可能提交多个后台任务：每个都注册完成回调
            for resp in responses:
                if resp.type == "task_submitted":
                    await self._register_completion_callback(resp.data or {}, msg)
            # 没有其它可发送内容时结束，避免再补一条"处理完成。"占位
            if not any(
                r.type in ("text", "card", "error") and (r.content or r.data)
                for r in responses
            ):
                return

        # 处理空响应
        if not responses:
            await self._send(msg, "处理完成。")
            return

        # 判断用卡片还是文本发送
        msg_type, content = estimate_card_or_text(responses)

        # 发送
        receive_id, receive_id_type = self._receive_info(msg)
        if msg_type == "interactive":
            ok = await self._client.send_message(
                receive_id=receive_id,
                msg_type="interactive",
                content=content,
                receive_id_type=receive_id_type,
            )
            if not ok:
                # 卡片发送失败 → 降级为文本（可能分多条）
                text = collect_response_text(responses)
                for chunk in split_long_text(text):
                    await self._send(msg, chunk)
        else:
            # 文本消息：超过2000字时分多条发送
            raw_text = collect_response_text(responses)
            for chunk in split_long_text(raw_text):
                await self._send(msg, chunk)

    async def _send(self, msg: ParsedMessage, content: str):
        """发送纯文本回复到飞书，自动处理私聊/群聊的目标地址."""
        if isinstance(content, dict):
            import json
            content = json.dumps(content, ensure_ascii=False)

        receive_id, receive_id_type = self._receive_info(msg)
        text_content = content if content.startswith("{") else to_feishu_text(content)
        await self._client.send_message(
            receive_id=receive_id,
            msg_type="text",
            content=text_content,
            receive_id_type=receive_id_type,
        )

    async def _register_completion_callback(self, data: Dict[str, Any],
                                            msg: ParsedMessage) -> None:
        """注册后台任务完成回调 → 结果主动推送到飞书。

        闭包捕获 ``msg``（含 open_id/chat_id，可定位发送目标）。
        必须注册到全局单例 worker（get_worker）：队列是进程内的，
        注册与执行任务在同一进程，回调才会被触发。
        注册失败只记日志，不影响"已提交"的即时反馈。
        """
        task_id = data.get("task_id")
        if not task_id:
            return
        try:
            from app.services.task_queue import get_worker
            worker = await get_worker()

            async def _push(result: Dict[str, Any]) -> None:
                await self._push_skill_result(result, msg)

            await worker.set_completion_callback(task_id, _push)
            logger.info("Feishu completion callback registered for task %s", task_id)
        except Exception:
            logger.warning("Failed to register feishu completion callback (task=%s)",
                           task_id, exc_info=True)

    @staticmethod
    def _md_table_to_text(content: str) -> str:
        """把 markdown 表格转成「列: 值」可读文本（飞书纯文本消息不支持表格）。

        非表格行原样保留；表格块中分隔行（| --- |）丢弃，
        每个数据行展开为 `表头: 值` 的对齐文本。
        """
        lines = content.splitlines()
        out: List[str] = []
        i = 0
        while i < len(lines):
            stripped = lines[i].strip()
            if stripped.startswith("|") and stripped.endswith("|"):
                block: List[List[str]] = []
                while i < len(lines) and lines[i].strip().startswith("|"):
                    block.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                    i += 1
                # 去掉分隔行（单元格仅由 - : 空格组成）
                rows = [r for r in block
                        if not all(set(c) <= set("-: ") for c in r if c)]
                if len(rows) >= 2:
                    header = rows[0]
                    for row in rows[1:]:
                        pairs = [f"{h}: {v}" for h, v in zip(header, row) if h]
                        out.append("  ".join(pairs))
                elif rows:
                    out.append("  ".join(c for c in rows[0] if c))
            else:
                out.append(lines[i])
                i += 1
        return "\n".join(out)

    async def _push_skill_result(self, result: dict, msg: ParsedMessage):
        """Push background task execution result to Feishu.

        Called by BackgroundTaskWorker completion callback when a
        background task (ci_build download / long-running skill) completes.
        Sends the result as a new Feishu message (not a reply).
        """
        # Extract readable text from various result formats
        content_text = ""
        if isinstance(result, dict):
            content_text = (
                result.get("message")
                or (str(result.get("data")) if result.get("data") else "")
                or result.get("error")
                or ""
            )
        if not content_text:
            content_text = str(result)[:2000]
        else:
            content_text = self._md_table_to_text(content_text)

        await self._send(msg, content_text[:2000])
