"""
Feishu WebSocket 长连接事件监听 (基于 lark-oapi SDK)

使用 lark-oapi 官方 SDK 处理飞书 WebSocket 协议，在后台线程中运行。
通过 asyncio.run_coroutine_threadsafe() 将事件桥接到主事件循环。

lark_oapi.ws.Client 负责:
  - 通过 POST /callback/ws/endpoint 发现 WebSocket URL (无需手动获取 token)
  - protobuf 二进制帧协议的编码/解码
  - ping/pong 心跳保持
  - 消息分片组合 (combine)
  - 自动断线重连 (指数退避)
"""

import asyncio
import logging
import threading
from typing import Callable, Optional

from lark_oapi import EventDispatcherHandler, LogLevel
from lark_oapi.api.im.v1 import P2ImMessageReceiveV1
from lark_oapi.ws import Client as LarkWsClient

logger = logging.getLogger(__name__)


class FeishuWsClient:
    """飞书 WebSocket 长连接客户端 (基于 lark-oapi SDK).

    在后台线程中运行 lark_oapi.ws.Client (其 start() 是阻塞的).
    事件通过 asyncio.run_coroutine_threadsafe() 桥接到主事件循环.
    """

    def __init__(self, app_id: str, app_secret: str,
                 loop: asyncio.AbstractEventLoop,
                 event_callback: Callable[[dict], None]):
        """
        Args:
            app_id: 飞书应用 App ID
            app_secret: 飞书应用 App Secret
            loop: 主事件循环 (asyncio) — 用于桥接事件到 async 世界
            event_callback: 异步事件处理回调，接收事件 dict (将被 schedule 到主循环)
        """
        self._app_id = app_id
        self._app_secret = app_secret
        self._loop = loop
        self._event_callback = event_callback
        self._thread: Optional[threading.Thread] = None
        self._ws_loop: Optional[asyncio.AbstractEventLoop] = None
        self._running = False

    # ── 事件处理 ──

    def _build_event_handler(self) -> EventDispatcherHandler:
        """构建事件分发器，注册 im.message.receive_v1 处理."""

        def _sync_handler(evt: P2ImMessageReceiveV1):
            """lark-oapi 同步事件处理器 → 桥接到 async 主循环.

            在 lark-oapi 的 ws 线程中被同步调用.
            将 lark-oapi 事件对象转换为统一 dict 后，通过
            asyncio.run_coroutine_threadsafe 调度到主事件循环.
            """
            try:
                event_dict = self._event_to_dict(evt)
                asyncio.run_coroutine_threadsafe(
                    self._event_callback(event_dict), self._loop
                )
            except RuntimeError:
                # 主循环已停止 (shutdown 场景)，忽略
                pass
            except Exception as e:
                logger.error("Feishu WS event bridge failed: %s", e)

        return (EventDispatcherHandler.builder("", "")
                .register_p2_im_message_receive_v1(_sync_handler)
                .build())

    @staticmethod
    def _event_to_dict(evt: P2ImMessageReceiveV1) -> dict:
        """将 lark-oapi 事件对象转为统一 dict 格式，兼容 parse_event().

        parse_event() 期望的格式:
        {
            "schema": "2.0",
            "header": {"event_type": "im.message.receive_v1"},
            "event": {
                "sender": {"sender_id": {"open_id": "..."}, "sender_type": "..."},
                "message": {
                    "message_id": "...", "chat_id": "...", "chat_type": "...",
                    "message_type": "...", "content": "...",
                }
            }
        }
        """
        d: dict = {"schema": evt.schema or "", "header": {}, "event": {}}

        if evt.header:
            d["header"]["event_type"] = evt.header.event_type or ""

        if evt.event:
            event_part: dict = {}

            if evt.event.sender:
                sender_id: dict = {}
                if evt.event.sender.sender_id:
                    sender_id["open_id"] = evt.event.sender.sender_id.open_id or ""
                event_part["sender"] = {
                    "sender_id": sender_id,
                    "sender_type": evt.event.sender.sender_type or "",
                }

            if evt.event.message:
                msg = evt.event.message
                event_part["message"] = {
                    "message_id": msg.message_id or "",
                    "root_id": msg.root_id or "",
                    "parent_id": msg.parent_id or "",
                    "chat_id": msg.chat_id or "",
                    "chat_type": msg.chat_type or "",
                    "message_type": msg.message_type or "",
                    "content": msg.content or "",
                }

            d["event"] = event_part

        return d

    # ── 生命周期 ──

    def start(self):
        """启动 WebSocket 后台线程."""
        if self._thread is not None:
            logger.warning("Feishu WS thread already running")
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run_ws_client,
            name="feishu-ws",
            daemon=True,
        )
        self._thread.start()
        logger.info("Feishu WebSocket client thread started")

    def stop(self):
        """停止 WebSocket 连接 (优雅关闭)."""
        self._running = False
        if self._ws_loop and self._ws_loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self._shutdown_ws(), self._ws_loop
            )
        if self._thread:
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                logger.warning("Feishu WS thread did not exit cleanly")
        self._thread = None
        self._ws_loop = None
        logger.info("Feishu WebSocket client stopped")

    async def _shutdown_ws(self):
        """优雅关闭 ws 事件循环：cancel 待处理任务 → 关闭 async generators → 停止循环."""
        try:
            tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            for t in tasks:
                t.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            self._ws_loop.shutdown_asyncgens()
        except Exception as e:
            logger.debug("WS shutdown error: %s", e)
        finally:
            if self._ws_loop and not self._ws_loop.is_closed():
                self._ws_loop.stop()

    def _run_ws_client(self):
        """在后台线程中运行 lark-oapi ws.Client (阻塞).

        lark_oapi.ws.Client.start() 会阻塞当前线程，内部使用事件循环处理
        WebSocket 连接、消息接收、心跳、重连等。我们在线程中创建独立的事件循环，
        并 patch lark-oapi 的模块级 loop 引用指向它。
        """
        ws_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(ws_loop)
        self._ws_loop = ws_loop

        # Patch lark-oapi ws.client 模块级 loop 引用指向新创建的循环
        # 因为 lark_oapi.ws.client 在 import 时已捕获了主线程的 loop
        import lark_oapi.ws.client as _lark_ws_mod  # pylint: disable=import-outside-toplevel
        _lark_ws_mod.loop = ws_loop

        try:
            event_handler = self._build_event_handler()
            client = LarkWsClient(
                app_id=self._app_id,
                app_secret=self._app_secret,
                event_handler=event_handler,
                log_level=LogLevel.INFO,
                auto_reconnect=True,
            )
            # 阻塞：连接 + 事件接收 + 自动重连，直到连接彻底失败
            client.start()
        except Exception as e:
            logger.error("Feishu WS client exited: %s", e)
