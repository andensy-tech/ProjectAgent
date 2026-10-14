"""
Feishu IM Channel Adapter

提供飞书(Feishu/Lark)即时通讯渠道接入能力：
  - WebSocket 长连接接收消息事件
  - REST API 发送文本/卡片回复
  - AgentEngine 完整流程编排

使用方式 (在 main.py lifespan 中初始化):

    import asyncio
    from app.services.feishu import FeishuApiClient, FeishuWsClient, FeishuAdapter

    feishu_client = FeishuApiClient(app_id, app_secret)
    feishu_adapter = FeishuAdapter(feishu_client, agent_engine)
    feishu_ws = FeishuWsClient(
        app_id=app_id,
        app_secret=app_secret,
        loop=asyncio.get_event_loop(),
        event_callback=feishu_adapter.handle_event,
    )
    feishu_ws.start()  # 非阻塞，在后台线程运行
"""

from app.services.feishu.client import FeishuApiClient
from app.services.feishu.ws_client import FeishuWsClient
from app.services.feishu.adapter import FeishuAdapter
from app.services.feishu.converter import parse_event, estimate_card_or_text, collect_response_text, to_feishu_text

__all__ = [
    "FeishuApiClient",
    "FeishuWsClient",
    "FeishuAdapter",
    "parse_event",
    "estimate_card_or_text",
    "collect_response_text",
    "to_feishu_text",
]
