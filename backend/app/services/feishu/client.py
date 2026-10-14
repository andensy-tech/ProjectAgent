"""
Feishu REST API Client — 消息发送 + Token 管理

封装飞书开放平台 API：
  - tenant_access_token 自动获取/刷新
  - 发送文本消息、卡片消息
  - 回复消息

Base URL: https://open.feishu.cn/open-apis
"""

import json
import time
import logging

import httpx

logger = logging.getLogger(__name__)

_BASE = "https://open.feishu.cn/open-apis"


class FeishuApiClient:
    """Async Feishu API client with auto-refreshing tenant_access_token."""

    def __init__(self, app_id: str, app_secret: str):
        self._app_id = app_id
        self._app_secret = app_secret
        self._token: str = ""
        self._token_expire_at: float = 0.0
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(15.0))

    # ── Token 管理 ──

    async def ensure_token(self) -> str:
        """Get a valid tenant_access_token, refreshing if expired."""
        if self._token and time.time() < self._token_expire_at - 60:
            return self._token
        return await self._refresh_token()

    async def _refresh_token(self) -> str:
        """POST /auth/v3/tenant_access_token/internal — 获取新的 token."""
        try:
            resp = await self._http.post(
                f"{_BASE}/auth/v3/tenant_access_token/internal",
                json={"app_id": self._app_id, "app_secret": self._app_secret},
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != 0:
                raise RuntimeError(f"Feishu auth failed: {data.get('msg', '')}")
            self._token = data["tenant_access_token"]
            self._token_expire_at = time.time() + data.get("expire", 7200)
            logger.info("Feishu tenant_access_token refreshed, expires in %ds", data.get("expire", 7200))
            return self._token
        except Exception as e:
            logger.error("Feishu token refresh failed: %s", e)
            raise

    # ── 消息发送 ──

    async def send_message(
        self,
        receive_id: str,
        msg_type: str,
        content: str,
        receive_id_type: str = "open_id",
    ) -> bool:
        """POST /im/v1/messages — 发送消息给用户或群聊.

        Args:
            receive_id: 接收者 ID (open_id / chat_id)
            msg_type: 消息类型 (text / interactive / post 等)
            content: JSON 字符串格式的消息内容
            receive_id_type: ID 类型 (open_id / chat_id / user_id)

        Returns:
            True if sent successfully.
        """
        token = await self.ensure_token()
        try:
            resp = await self._http.post(
                f"{_BASE}/im/v1/messages",
                params={"receive_id_type": receive_id_type},
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "receive_id": receive_id,
                    "msg_type": msg_type,
                    "content": content,
                },
            )
            body = resp.json()
            if body.get("code") != 0:
                logger.error("Feishu send_message failed: code=%s msg=%s",
                             body.get("code"), body.get("msg"))
                return False
            return True
        except Exception as e:
            logger.error("Feishu send_message error: %s", e)
            return False

    async def reply_message(
        self,
        message_id: str,
        msg_type: str,
        content: str,
    ) -> bool:
        """POST /im/v1/messages/{message_id}/reply — 回复消息.

        在群聊中回复会带上 @提及，私聊回复等同于 send_message.
        """
        token = await self.ensure_token()
        try:
            resp = await self._http.post(
                f"{_BASE}/im/v1/messages/{message_id}/reply",
                headers={"Authorization": f"Bearer {token}"},
                json={"msg_type": msg_type, "content": content},
            )
            body = resp.json()
            if body.get("code") != 0:
                logger.error("Feishu reply_message failed: code=%s msg=%s",
                             body.get("code"), body.get("msg"))
                return False
            return True
        except Exception as e:
            logger.error("Feishu reply_message error: %s", e)
            return False

    # ── 应用信息 ──

    def get_app_id(self) -> str:
        """Return the configured App ID."""
        return self._app_id

    async def close(self):
        """Close the underlying HTTP client."""
        await self._http.aclose()
