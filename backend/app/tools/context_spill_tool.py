"""Context Spill 取回工具 — 按需切片读取先前因过大而暂存的原始数据（层 A）。

背景（2026-09-08）：MCP 工具（如 get_issue_comments）不分页、一次全量返回大数据。
当结果超过单次展示预算时，Agent 会把**原始全量**存入 context_spill 暂存区，展示区只放
摘要/最新条目。本工具供 LLM 在需要细节时**精确取回** spill 中的条目：
  - 有界切片（offset/limit），杜绝整读再次撑爆 context
  - 可选关键词过滤（query），对评论/表格按内容精确筛
本工具是普通内置工具（走 call_builtin_tool），**不新增第 7 个 meta-tool**。
"""

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


async def context_spill_read(params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """有界读取先前暂存（spill）的原始数据切片。

    Args:
        params:
          spill_id: 必填，save 时返回的暂存标识。
          offset: 起始下标（默认 0）。
          limit: 最多返回条数（默认 30，**必须传具体值，勿整读**）。
          query: 可选关键词，逐条内容做大小写不敏感子串过滤后再切片。
        context: 含 _user_id。

    Returns:
        {"success": True, "data": {...read_spill_slice 结果...}}
        或 {"success": False, "error": 中文提示}
    """
    from app.services.context_spill import read_spill_slice

    user_id = context.get("_user_id") if isinstance(context, dict) else None
    if not user_id:
        return {"success": False, "error": "缺少用户身份信息"}

    spill_id = str(params.get("spill_id", "") or "").strip()
    if not spill_id:
        return {"success": False, "error": "缺少 spill_id（先前数据暂存时返回的标识）"}

    try:
        result = await read_spill_slice(
            user_id=user_id,
            spill_id=spill_id,
            offset=params.get("offset", 0),
            limit=params.get("limit", 30),
            query=params.get("query"),
        )
    except Exception as e:  # noqa: BLE001 — 工具边界，向 LLM 返回可读错误
        logger.warning("context_spill_read(%s) failed: %s", spill_id, e)
        return {"success": False, "error": f"读取暂存数据失败: {str(e)[:120]}"}

    if not result.get("success"):
        return {"success": False, "error": result.get("error", "读取暂存数据失败")}

    return {"success": True, "data": result}
