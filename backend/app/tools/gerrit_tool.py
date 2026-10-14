"""
Gerrit REST API Tools — merge/submit/review operations via direct REST API.

Wraps Gerrit's native REST API for write operations (submit merge, review)
that are not available through the MCP layer. Reuses the user's MCP config
credentials transparently, so no additional configuration is needed.

Endpoints used:
  - POST /a/changes/{change-id}/submit       — Submit change for merge
  - POST /a/changes/{change-id}/revisions/current/review  — Post review scores
"""

import base64
import json
import logging
import re
from typing import Dict, Any, Optional, Tuple

from app.services.gerrit_service import (
    DEFAULT_GERRIT_INSTANCE,
    GERRIT_INSTANCE_MAP,
)

logger = logging.getLogger(__name__)

# Gerrit instance hostname → MCP client name mapping（单一事实来源在 gerrit_service.py，
# 由 GERRIT_HOSTS 环境变量或中性示例默认值提供）
GERRIT_CLIENT_PREFIX = "gerrit_"


def _get_gerrit_client_name(instance: str) -> str:
    """Resolve Gerrit instance hostname to MCP client name."""
    suffix = GERRIT_INSTANCE_MAP.get(instance, instance)
    return f"{GERRIT_CLIENT_PREFIX}{suffix}"


def _resolve_auth(params: Dict[str, Any],
                  mcp_auth: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """Extract Gerrit REST API base URL and HTTP Basic auth from user MCP config.

    The MCP config stores two URLs for Gerrit:
      - MCP proxy URL:  cfg["url"]          (e.g. http://10.0.0.1:8118/mcp)
      - Gerrit web URL:  auth["server_url"]  (e.g. https://gerrit-1.example.com)

    The REST API must use the **Gerrit web URL** (server_url), NOT the MCP proxy URL.
    Falls back to the derived MCP URL if server_url is not set (legacy configs).

    Returns:
        Tuple of (base_url, username, password, server_url) or (None, None, None, None) if not found.
        - base_url:    the MCP proxy URL (for reference)
        - server_url:  the actual Gerrit web URL to use for REST API calls
    """
    instance = params.get("instance") or DEFAULT_GERRIT_INSTANCE
    client_name = _get_gerrit_client_name(instance)

    cfg = mcp_auth.get(client_name) or mcp_auth.get(instance) or {}
    # 模糊匹配：处理非标准服务器名（如用户配置为 gerrit_<hostname> 而非 gerrit_<instance>）。
    # 精确匹配失败时，扫描含实例名/客户端名的 gerrit 配置键。
    if not cfg:
        for key, val in mcp_auth.items():
            if "gerrit" in key and (instance.lower() in key.lower() or client_name.lower() in key.lower()):
                cfg = val
                logger.info("Gerrit 配置模糊匹配: %s → %s", client_name, key)
                break
    auth = cfg.get("auth") or {}
    base_url = cfg.get("url", "")

    username = auth.get("username") or auth.get("user") or ""
    password = auth.get("password") or auth.get("pass") or ""

    # Extract the actual Gerrit web URL from auth dict
    # server_url is stored inside the Authorization JSON by the settings page
    server_url = auth.get("server_url", "")

    return base_url, username, password, server_url


def _get_rest_base(base_url: str, server_url: str) -> str:
    """Resolve the Gerrit REST API base URL.

    Prefers the explicit server_url (actual Gerrit web URL) over the derived
    MCP URL, because the MCP proxy and Gerrit HTTP daemon often run on
    different ports.

    Returns:
        URL base string without trailing slash, ready for /a/changes/... path.
    """
    if server_url:
        return server_url.rstrip("/")

    # Fallback: derive from MCP URL by stripping the /mcp path suffix
    rest = base_url.rstrip("/")
    if rest.endswith("/mcp"):
        rest = rest[:-4]
    return rest


def _build_auth_header(username: str, password: str) -> Dict[str, str]:
    """Build HTTP Basic Authorization header."""
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


async def gerrit_submit_change(params: Dict[str, Any],
                                context: Dict[str, Any]) -> Dict[str, Any]:
    """Submit a Gerrit change for merge via REST API.

    Args:
        params:
            change_id: Gerrit change number (e.g. "12345") or full path.
            instance: Gerrit instance name, default the first configured instance.
            wait_for_merge: If true (default), waits for merge to complete.
            submit_type: Optional submit strategy:
                         "MERGE_IF_NECESSARY", "FAST_FORWARD_ONLY",
                         "REBASE_IF_NECESSARY", "MERGE_ALWAYS", "CHERRY_PICK".
        context: Execution context with _user_id.

    Returns:
        Standard tool result dict.
    """
    change_id = params.get("change_id", "").strip()
    if not change_id:
        return {"type": "error", "error": "缺少参数: change_id", "success": False}

    # Load user MCP config for credentials
    user_id = context.get("_user_id") if isinstance(context, dict) else None
    if not user_id:
        return {"type": "error", "error": "缺少用户身份信息，无法获取MCP配置", "success": False}

    try:
        from app.services.settings_service import get_mcp_auth_dict
        mcp_config = await get_mcp_auth_dict(user_id)
    except Exception as e:
        logger.error("Failed to load MCP config for user %s: %s", user_id, e)
        return {"type": "error", "error": f"加载MCP配置失败: {e}", "success": False}

    base_url, username, password, server_url = _resolve_auth(params, mcp_config)
    if not base_url or not username or not password:
        instance = params.get("instance") or DEFAULT_GERRIT_INSTANCE
        client_name = _get_gerrit_client_name(instance)
        return {
            "type": "error",
            "error": f"Gerrit MCP服务器「{client_name}」未配置或缺少认证信息，"
                     f"请先在设置页面的「MCP 服务」中配置后再使用",
            "success": False,
        }

    # Resolve REST API base URL — use server_url (actual Gerrit web URL) if available
    rest_base = _get_rest_base(base_url, server_url)

    # Build full REST API URL
    url = f"{rest_base}/a/changes/{change_id}/submit"

    # Build request body
    body: Dict[str, Any] = {}
    wait_for_merge = params.get("wait_for_merge", True)
    if isinstance(wait_for_merge, bool):
        body["wait_for_merge"] = wait_for_merge

    submit_type = params.get("submit_type", "")
    if submit_type:
        body["submit_type"] = submit_type

    headers = _build_auth_header(username, password)
    headers["Content-Type"] = "application/json; charset=UTF-8"

    import httpx
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, json=body, headers=headers)
    except httpx.TimeoutException:
        return {
            "type": "error",
            "error": f"Gerrit提交请求超时（30s），请检查Gerrit服务器状态或稍后重试",
            "success": False,
        }
    except httpx.ConnectError as e:
        return {
            "type": "error",
            "error": f"无法连接到Gerrit服务器 ({rest_base}): {e}",
            "success": False,
        }
    except Exception as e:
        logger.error("Gerrit submit HTTP request failed: %s", e)
        return {"type": "error", "error": f"Gerrit提交请求失败: {e}", "success": False}

    if resp.status_code == 200 or resp.status_code == 409:
        # 200 = success; 409 = conflict (already merged or merge failed)
        text = resp.text
        # Gerrit REST wraps JSON response in )]}' prefix
        if text.startswith(")]}'"):
            text = text[4:]
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = {"raw": text}

        if resp.status_code == 200:
            new_status = data.get("status", "MERGED")
            return {
                "type": "text",
                "success": True,
                "message": f"✓ 变更 {change_id} 已成功提交合并，状态: {new_status}",
                "data": {
                    "change_id": change_id,
                    "status": new_status,
                    "instance": params.get("instance") or DEFAULT_GERRIT_INSTANCE,
                },
            }
        else:
            # 409 Conflict
            gerrit_type = data.get("type", "")
            return {
                "type": "text",
                "success": False,
                "message": (
                    f"变更 {change_id} 提交失败 (HTTP 409)\n"
                    f"类型: {gerrit_type}\n"
                    f"详情: {data.get('message', text[:500])}"
                ),
                "data": data,
            }

    elif resp.status_code == 403:
        return {
            "type": "error",
            "error": f"Gerrit拒绝提交: 没有权限。需要 Code-Review+2 和 Verified+1 才能提交。",
            "success": False,
        }
    else:
        try:
            err_text = resp.text
            if err_text.startswith(")]}'"):
                err_text = err_text[4:]
            err_data = json.loads(err_text)
            err_msg = err_data.get("message", err_text[:500])
        except Exception:
            err_msg = resp.text[:500]

        return {
            "type": "error",
            "error": f"Gerrit提交失败 (HTTP {resp.status_code}): {err_msg}",
            "success": False,
        }


async def gerrit_review_change(params: Dict[str, Any],
                                context: Dict[str, Any]) -> Dict[str, Any]:
    """Post a review on a Gerrit change via REST API.

    Args:
        params:
            change_id: Gerrit change number (e.g. "12345") or full path.
            instance: Gerrit instance name, default the first configured instance.
            labels: Dict of label→score, e.g. {"Code-Review": 2, "Verified": 1}.
            message: Optional review comment.
        context: Execution context with _user_id.

    Returns:
        Standard tool result dict.
    """
    change_id = params.get("change_id", "").strip()
    if not change_id:
        return {"type": "error", "error": "缺少参数: change_id", "success": False}

    # Load user MCP config
    user_id = context.get("_user_id") if isinstance(context, dict) else None
    if not user_id:
        return {"type": "error", "error": "缺少用户身份信息，无法获取MCP配置", "success": False}

    try:
        from app.services.settings_service import get_mcp_auth_dict
        mcp_config = await get_mcp_auth_dict(user_id)
    except Exception as e:
        logger.error("Failed to load MCP config for user %s: %s", user_id, e)
        return {"type": "error", "error": f"加载MCP配置失败: {e}", "success": False}

    base_url, username, password, server_url = _resolve_auth(params, mcp_config)
    if not base_url or not username or not password:
        instance = params.get("instance") or DEFAULT_GERRIT_INSTANCE
        client_name = _get_gerrit_client_name(instance)
        return {
            "type": "error",
            "error": f"Gerrit MCP服务器「{client_name}」未配置或缺少认证信息，"
                     f"请先在设置页面的「MCP 服务」中配置后再使用",
            "success": False,
        }

    rest_base = _get_rest_base(base_url, server_url)
    url = f"{rest_base}/a/changes/{change_id}/revisions/current/review"

    # Build review body
    body: Dict[str, Any] = {}
    labels = params.get("labels")
    if labels:
        body["labels"] = labels
    message = params.get("message", "")
    if message:
        body["message"] = message

    headers = _build_auth_header(username, password)
    headers["Content-Type"] = "application/json; charset=UTF-8"

    import httpx
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, json=body, headers=headers)
    except httpx.TimeoutException:
        return {
            "type": "error",
            "error": "Gerrit评审请求超时（30s），请检查Gerrit服务器状态",
            "success": False,
        }
    except httpx.ConnectError as e:
        return {
            "type": "error",
            "error": f"无法连接到Gerrit服务器 ({rest_base}): {e}",
            "success": False,
        }
    except Exception as e:
        logger.error("Gerrit review HTTP request failed: %s", e)
        return {"type": "error", "error": f"Gerrit评审请求失败: {e}", "success": False}

    if resp.status_code == 200:
        text = resp.text
        if text.startswith(")]}'"):
            text = text[4:]
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = {"raw": text}

        return {
            "type": "text",
            "success": True,
            "message": f"✓ 变更 {change_id} 评审已提交",
            "data": {
                "change_id": change_id,
                "labels": data.get("labels", labels),
                "instance": params.get("instance") or DEFAULT_GERRIT_INSTANCE,
            },
        }
    else:
        try:
            err_text = resp.text
            if err_text.startswith(")]}'"):
                err_text = err_text[4:]
            err_data = json.loads(err_text)
            err_msg = err_data.get("message", err_text[:500])
        except Exception:
            err_msg = resp.text[:500]

        return {
            "type": "error",
            "error": f"Gerrit评审提交失败 (HTTP {resp.status_code}): {err_msg}",
            "success": False,
        }


async def gerrit_get_change_detail(params: Dict[str, Any],
                                    context: Dict[str, Any]) -> Dict[str, Any]:
    """Get detailed information about a Gerrit change via REST API.

    Provides richer data than the MCP get_change tool (includes labels,
    reviewers, commit info). Useful for checking if conditions are met
    before attempting a submit.

    Args:
        params:
            change_id: Gerrit change number (e.g. "12345") or full path.
            instance: Gerrit instance name, default the first configured instance.
        context: Execution context with _user_id.

    Returns:
        Standard tool result dict with change details.
    """
    change_id = params.get("change_id", "").strip()
    if not change_id:
        return {"type": "error", "error": "缺少参数: change_id", "success": False}

    user_id = context.get("_user_id") if isinstance(context, dict) else None
    if not user_id:
        return {"type": "error", "error": "缺少用户身份信息", "success": False}

    try:
        from app.services.settings_service import get_mcp_auth_dict
        mcp_config = await get_mcp_auth_dict(user_id)
    except Exception as e:
        return {"type": "error", "error": f"加载MCP配置失败: {e}", "success": False}

    base_url, username, password, server_url = _resolve_auth(params, mcp_config)
    if not base_url:
        return {"type": "error", "error": "Gerrit MCP未配置", "success": False}

    rest_base = _get_rest_base(base_url, server_url)
    url = f"{rest_base}/a/changes/{change_id}/detail"

    headers = _build_auth_header(username, password) if username and password else {}

    import httpx
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers=headers)
    except Exception as e:
        return {"type": "error", "error": f"查询失败: {e}", "success": False}

    if resp.status_code == 200:
        text = resp.text
        if text.startswith(")]}'"):
            text = text[4:]
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = {"raw": text}

        # Extract key fields for the LLM
        labels = data.get("labels", {})
        current_revision = data.get("current_revision", "")
        revisions = data.get("revisions", {})
        rev_info = revisions.get(current_revision, {}) if current_revision else {}

        return {
            "type": "text",
            "success": True,
            "data": {
                "change_id": data.get("change_id", ""),
                "subject": data.get("subject", ""),
                "status": data.get("status", ""),
                "branch": data.get("branch", ""),
                "project": data.get("project", ""),
                "owner": data.get("owner", {}).get("name", "") if isinstance(data.get("owner"), dict) else "",
                "created": data.get("created", ""),
                "updated": data.get("updated", ""),
                "labels": {
                    label: {
                        "value": info.get("value", 0),
                        "all": [
                            {"name": v.get("name", ""), "value": v.get("value", 0)}
                            for v in info.get("all", [])
                        ] if isinstance(info.get("all"), list) else [],
                    }
                    for label, info in labels.items()
                    if isinstance(info, dict)
                },
                "mergeable": data.get("mergeable", False),
                "commit_message": rev_info.get("commit", {}).get("message", "") if isinstance(rev_info.get("commit"), dict) else "",
                "instance": params.get("instance") or DEFAULT_GERRIT_INSTANCE,
            },
            "message": f"已获取变更 {change_id} 的详细信息",
        }
    else:
        return {
            "type": "error",
            "error": f"查询变更失败 (HTTP {resp.status_code}): {resp.text[:300]}",
            "success": False,
        }


async def gerrit_batch_get_changes(params: Dict[str, Any],
                                    context: Dict[str, Any]) -> Dict[str, Any]:
    """批量查询多个 Gerrit change 的状态（替代逐个 get_change 循环）。

    用 Gerrit REST API 的批量查询（q=change:A OR change:B ...），一次返回多个
    CL 的状态/标题/owner/label 评分，避免 LLM 对每个 CL 单独调用一次。

    Args:
        params:
            change_ids: CL 编号列表，如 ["12345", "12346"] 或 "12345,12346"（必填）
            instance: Gerrit 实例名，默认第一个配置的实例
        context: 执行上下文，需含 _user_id。

    Returns:
        标准工具结果，changes 为每条 CL 的状态摘要列表。
    """
    raw_ids = params.get("change_ids") or []
    if isinstance(raw_ids, str):
        raw_ids = [x.strip() for x in re.split(r"[,;\s]+", raw_ids) if x.strip()]
    change_ids = [str(x).strip() for x in raw_ids if str(x).strip()]
    if not change_ids:
        return {"type": "error", "error": "缺少参数: change_ids（CL 编号列表，如 [12345, 12346]）", "success": False}
    if len(change_ids) > 100:
        change_ids = change_ids[:100]

    user_id = context.get("_user_id") if isinstance(context, dict) else None
    if not user_id:
        return {"type": "error", "error": "缺少用户身份信息", "success": False}

    try:
        from app.services.settings_service import get_mcp_auth_dict
        mcp_config = await get_mcp_auth_dict(user_id)
    except Exception as e:
        logger.error("Failed to load MCP config for user %s: %s", user_id, e)
        return {"type": "error", "error": f"加载MCP配置失败: {e}", "success": False}

    base_url, username, password, server_url = _resolve_auth(params, mcp_config)
    if not base_url or not username or not password:
        instance = params.get("instance") or DEFAULT_GERRIT_INSTANCE
        client_name = _get_gerrit_client_name(instance)
        return {
            "type": "error",
            "error": f"Gerrit MCP服务器「{client_name}」未配置或缺少认证信息，请先在设置页面的「MCP 服务」中配置后再使用",
            "success": False,
        }

    rest_base = _get_rest_base(base_url, server_url)
    headers = _build_auth_header(username, password)
    headers["Accept"] = "application/json"

    import httpx
    import urllib.parse

    all_results: list = []
    missing: list = []
    BATCH_SIZE = 20  # 每批 20 个，避免 URL 过长
    try:
        for i in range(0, len(change_ids), BATCH_SIZE):
            batch = change_ids[i:i + BATCH_SIZE]
            q = " OR ".join(f"change:{cid}" for cid in batch)
            url = (
                f"{rest_base}/a/changes/?q={urllib.parse.quote(q)}"
                "&o=CURRENT_REVISION&o=LABELS&o=DETAILED_ACCOUNTS&n=200"
            )
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.get(url, headers=headers)
            except httpx.TimeoutException:
                return {"type": "error", "error": "Gerrit 批量查询超时（30s），请稍后重试", "success": False}
            except httpx.ConnectError as e:
                return {"type": "error", "error": f"无法连接到 Gerrit 服务器 ({rest_base}): {e}", "success": False}
            except Exception as e:
                logger.error("Gerrit batch HTTP request failed: %s", e)
                return {"type": "error", "error": f"Gerrit 批量查询请求失败: {e}", "success": False}

            if resp.status_code != 200:
                return {"type": "error", "error": f"Gerrit 批量查询失败 (HTTP {resp.status_code}): {resp.text[:200]}", "success": False}

            text = resp.text
            if text.startswith(")]}'"):
                text = text[4:]
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = []

            for item in data if isinstance(data, list) else []:
                num = item.get("_number", "")
                labels = item.get("labels", {})
                cr = labels.get("Code-Review", {}) if isinstance(labels.get("Code-Review"), dict) else {}
                vt = labels.get("Verified", {}) if isinstance(labels.get("Verified"), dict) else {}
                all_results.append({
                    "_number": num,
                    "subject": item.get("subject", ""),
                    "status": item.get("status", ""),
                    "project": item.get("project", ""),
                    "branch": item.get("branch", ""),
                    "owner": item.get("owner", {}).get("name", "") if isinstance(item.get("owner"), dict) else "",
                    "code_review": cr.get("value"),
                    "verified": vt.get("value"),
                    "mergeable": item.get("mergeable", False),
                    "instance": params.get("instance") or DEFAULT_GERRIT_INSTANCE,
                })
            # 标记本批未找到的
            found_in_batch = {str(r["_number"]) for r in all_results if r["_number"]}
            for cid in batch:
                if cid not in found_in_batch:
                    missing.append(cid)
    except Exception as e:
        logger.error("Gerrit batch query unexpected error: %s", e, exc_info=True)
        return {"type": "error", "error": f"Gerrit 批量查询出错: {e}", "success": False}

    if not all_results:
        return {
            "type": "error",
            "success": False,
            "error": f"批量查询未找到任何 CL（共 {len(change_ids)} 个，缺失: {missing[:10] or '全部'}）",
        }

    msg = f"批量查询 {len(change_ids)} 个 CL，返回 {len(all_results)} 条"
    if missing:
        msg += f"，未找到 {len(missing)} 条: {missing[:8]}"

    return {
        "type": "table",
        "success": True,
        "message": msg,
        "changes": all_results,          # 顶层，_format_gerrit_table 直接渲染
        "found_count": len(all_results),
        "missing": missing,
        "requested": len(change_ids),
    }
