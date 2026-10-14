"""
Jira filter 工具 — 解析 Jira filter 并拉取成员列表（P-B：monitoring_optimization）。

**不依赖 server_url / REST**：`server_url` 不是标准 MCP 字段（MCP 标准配置为
url 代理地址 + auth），前端设置页也无法配置它，jira 标准配置不含该字段。
Jira JQL 原生支持 `filter = {id}` 语法，因此直接用 MCP `search_issues` 执行
该 JQL 拉取成员（Agent 已在会话中验证成功）。

真正的配置错误（url 缺失 / 认证缺失 / MCP 不可达）明确提醒用户修正，不做代码规避。
"""

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


async def jira_resolve_filter(params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """解析 Jira filter：用 JQL `filter = {id}` 通过 MCP 拉取成员列表。

    Args:
        params: {"filter_id": "60513" 或 "https://jira.../issues/?filter=60513", "name": 可选}
        context: 含 _user_id

    Returns:
        {"success": True, "name": ..., "jql": "filter = {id}", "issue_count": N, "issues": [...]}
        或 {"success": False, "error": ...}（真正的配置错误明确提醒用户修正）
    """
    user_id = context.get("_user_id") if isinstance(context, dict) else None
    filter_id = str(params.get("filter_id", "") or params.get("filter", "")).strip()

    # 兼容完整链接：https://jira.example.com/issues/?filter=60513
    if "filter=" in filter_id:
        filter_id = filter_id.split("filter=")[-1].split("&")[0]
    filter_id = filter_id.strip().strip("/")
    if not filter_id:
        return {"success": False, "error": "缺少 filter_id"}
    if not user_id:
        return {"success": False, "error": "缺少用户身份信息"}

    # 配置校验：url / 认证是否完整（真正的配置错误才提醒，不做代码规避）
    from app.services.settings_service import get_mcp_auth_dict

    mcp_config = await get_mcp_auth_dict(user_id)
    cfg = mcp_config.get("jira") or {}
    if not cfg.get("url"):
        return {"success": False, "error": "Jira 未配置服务器，请在「设置 → MCP 服务」中配置 Jira"}
    auth = cfg.get("auth") or {}
    if not auth.get("username") or not auth.get("password"):
        return {"success": False, "error": "Jira 认证信息不完整，请在 MCP 设置中检查 username/password"}

    # 用 JQL filter = {id} 通过 MCP 拉取成员（不依赖 REST/server_url）。
    # review #7：字段解析复用 focus_set_service.fetch_members_with_meta（单一事实来源）；
    # review #1：truncated 时透传截断提示，不静默丢数据。
    from app.services.focus_set_service import fetch_members_with_meta

    jql = f"filter = {filter_id}"
    try:
        issues, meta = await fetch_members_with_meta(user_id, jql)
    except Exception as e:
        return {
            "success": False,
            "error": f"拉取 filter {filter_id} 成员失败（Jira MCP 响应异常）: {str(e)[:100]}。"
                     f"请检查 Jira MCP 服务是否可达",
        }

    name = params.get("name") or f"filter-{filter_id}"
    result: Dict[str, Any] = {
        "success": True,
        "filter_id": filter_id,
        "name": name,
        "jql": jql,
        "issue_count": len(issues),
        "issues": issues,
    }
    if meta.get("truncated"):
        result["truncation_warning"] = (
            f"⚠️ 该 filter 共 {meta.get('total')} 个成员，本次仅取回 {meta.get('fetched')} 个"
            f"（Jira MCP 不支持分页），关注集合/fix rate 可能不完整"
        )
    return result
