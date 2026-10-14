"""
Dashboard service — project metrics aggregation from Jira via MCP.

Provides:
  - get_dashboard_config() / update_dashboard_config() — persisted to user_settings.json
  - get_dashboard_summary() — JQL count queries, parallel execution, 60s in-memory cache
  - build_cl_jql() — CL 监控项目的检索条件（advanced_jql 优先，不再交叉回退）

CL 监控的扫描编排见 `cl_scan_service.run_daily_scan`（V1 的全量重查函数
get_cl_status_for_all_projects 已于 2026-09-16 删除）。
"""

import asyncio
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

# Beijing timezone (UTC+8)
_BJS_TZ = timezone(timedelta(hours=8))

# In-memory cache: dict keyed by user_id, each entry has "data" and "ts"
_cache: Dict[Optional[int], Dict[str, Any]] = {}
_CACHE_TTL = 60  # seconds (matches frontend auto-refresh threshold)


# ── MCP 统一接口（复用 MCPCapabilityService，消除重复的 client/auth 解析）──


async def _get_mcp_servers(user_id: Optional[int] = None) -> Dict[str, Dict]:
    """获取用户配置的 MCP 能力清单（热启动：实时读 DB）。"""
    from app.mcp.capability import capability
    return await capability.get_servers(user_id)


# Jira 查询熔断：连续失败达到阈值后暂停调用一段时间，防止后台轮询
# 在服务端（Jira/MCP）异常时持续重试、加剧故障（曾导致 MCP 服务器被拖死）
_JIRA_FAIL_STREAK = 0
_JIRA_CIRCUIT_OPEN_UNTIL = 0.0
_JIRA_CIRCUIT_THRESHOLD = 3
_JIRA_CIRCUIT_COOLDOWN = 900  # 秒（15 分钟）


async def _call_jira(tool: str, params: Dict, user_id: Optional[int] = None,
                     timeout: int = 60) -> Any:
    """通过统一 MCP 接口调用 Jira 工具（per-user auth 由 capability 内部解析）。

    Args:
        tool: Jira MCP 工具名（如 search_issues）
        params: 工具参数
        user_id: 用户 ID
        timeout: 请求超时（内网 Jira MCP 实测响应 15~50s，默认 60s 防误报超时）
    """
    global _JIRA_FAIL_STREAK, _JIRA_CIRCUIT_OPEN_UNTIL
    if time.monotonic() < _JIRA_CIRCUIT_OPEN_UNTIL:
        raise RuntimeError(
            f"Jira 查询临时熔断中（连续失败 {_JIRA_CIRCUIT_THRESHOLD} 次，"
            f"约 {int(_JIRA_CIRCUIT_OPEN_UNTIL - time.monotonic())} 秒后自动恢复）"
        )
    from app.mcp.capability import capability
    try:
        result = await capability.call("jira", tool, params, user_id, timeout=timeout)
    except Exception:
        _JIRA_FAIL_STREAK += 1
        if _JIRA_FAIL_STREAK >= _JIRA_CIRCUIT_THRESHOLD:
            _JIRA_CIRCUIT_OPEN_UNTIL = time.monotonic() + _JIRA_CIRCUIT_COOLDOWN
            _JIRA_FAIL_STREAK = 0
            logger.warning("Jira 查询连续失败 %d 次，熔断 %ds",
                           _JIRA_CIRCUIT_THRESHOLD, _JIRA_CIRCUIT_COOLDOWN)
        raise
    _JIRA_FAIL_STREAK = 0
    return result


# ── Config helpers (async, per-user) ────────────────────────────────────────


async def _get_user_id(user: Optional[Dict[str, Any]] = None) -> Optional[int]:
    """Extract user_id from current_user dict if available."""
    if user is None:
        return None
    return user.get("user_id")


_DEFAULT_CONFIG: Dict[str, Any] = {
    "projects": [],
    "thresholds": {"eta_expiring_days": 7, "stale_days": 7},
    "refresh_interval_seconds": 30,
    "jira_web_url": "",
}


async def get_dashboard_config(user_id: Optional[int] = None) -> Dict[str, Any]:
    """Load dashboard config for a user (file if no user_id)."""
    from app.services.settings_service import get_dashboard_config as _get_cfg
    return await _get_cfg(user_id)


async def update_dashboard_config(data: Dict[str, Any], user_id: Optional[int] = None) -> Dict[str, Any]:
    """Persist dashboard config (file if no user_id)."""
    from app.services.settings_service import update_dashboard_config as _upd_cfg
    result = await _upd_cfg(data, user_id)
    # Invalidate cache so next summary pull uses the new config
    _cache.clear()
    return result


# ── Independent CL monitoring config (separate from dashboard) ──────────────


_DEFAULT_CL_CONFIG: Dict[str, Any] = {
    "projects": [],
}


async def get_cl_monitoring_config(user_id: Optional[int] = None) -> Dict[str, Any]:
    """Load CL monitoring config (file if no user_id)."""
    from app.services.settings_service import get_cl_monitoring_config as _get_cfg
    return await _get_cfg(user_id)


async def save_cl_monitoring_config(data: Dict[str, Any], user_id: Optional[int] = None) -> Dict[str, Any]:
    """Persist CL monitoring config (file if no user_id)."""
    from app.services.settings_service import save_cl_monitoring_config as _sav_cfg
    return await _sav_cfg(data, user_id)


async def get_project_issues(project_name: str, max_results: int = 200, filter_type: str = "all",
                             user_id: Optional[int] = None) -> List[Dict]:
    """Fetch Jira issues for a specific project (by config name)."""
    config = await get_dashboard_config(user_id)
    projects = config.get("projects", [])

    if project_name == "all":
        enabled = [p for p in projects if p.get("enabled", True)]
        if not enabled:
            return []
        results = await asyncio.gather(
            *[_fetch_single_project_issues(p, max_results, filter_type, user_id) for p in enabled],
            return_exceptions=True,
        )
        combined: List[Dict] = []
        for r in results:
            if isinstance(r, list):
                combined.extend(r)
        return combined

    proj = next((p for p in projects if p.get("name") == project_name), None)
    if not proj:
        return []
    return await _fetch_single_project_issues(proj, max_results, filter_type, user_id)


async def _fetch_single_project_issues(proj: Dict, max_results: int = 200, filter_type: str = "all",
                                       user_id: Optional[int] = None) -> List[Dict]:
    """Build JQL from project config and fetch issues via MCP.

    If filter_type is specified (p0/eta_expiring/stale), applies client-side filtering.
    """
    advanced_jql = proj.get("advanced_jql", "").strip()
    if advanced_jql:
        jql = advanced_jql
    else:
        project_key = proj.get("jira_project_key", "")
        labels_val = proj.get("labels", "")
        epics_val = proj.get("epics", "")
        exclude = proj.get("exclude_statuses", "Closed,Verified,Done")
        base_conds = [f"status not in ({exclude})"]
        if project_key:
            base_conds.insert(0, f"project = {project_key}")
        base_filter = " AND ".join(base_conds)
        label_clause = _build_labels_clause(labels_val)
        epic_clause = _build_epic_clause(epics_val)
        if epic_clause and label_clause:
            jql = f"{base_filter} AND (({epic_clause}) OR ({label_clause}))"
        elif epic_clause:
            jql = f"{base_filter} AND {epic_clause}"
        elif label_clause:
            jql = f"{base_filter} AND ({label_clause})"
        else:
            jql = base_filter

    servers = await _get_mcp_servers(user_id)
    if "jira" not in servers:
        logger.warning("Jira MCP not configured for user %s", user_id)
        return []

    try:
        resp = await _call_jira("search_issues", {
            "jql": jql,
            "maxResults": max_results,
            "fields": ["key", "summary", "status", "priority", "assignee", "issuetype", "updated", "duedate"],
        }, user_id)
        issues = resp.get("issues", []) if isinstance(resp, dict) else []
        out = []
        for iss in issues:
            if not isinstance(iss, dict):
                continue
            f = iss.get("fields", {})
            if not isinstance(f, dict):
                f = {}
            assignee_raw = f.get("assignee")
            assignee_name = ""
            if isinstance(assignee_raw, dict):
                assignee_name = assignee_raw.get("displayName", "")
            elif isinstance(assignee_raw, str):
                assignee_name = assignee_raw
            out.append({
                "key": iss.get("key", ""),
                "summary": f.get("summary", ""),
                "status": _field_name(f, "status"),
                "priority": _field_name(f, "priority"),
                "issuetype": _field_name(f, "issuetype"),
                "assignee": assignee_name,
                "updated": f.get("updated", ""),
                "duedate": f.get("duedate", ""),
            })

        # Apply client-side filter if requested
        if filter_type and filter_type != "all":
            config = await get_dashboard_config(user_id)
            thresholds = config.get("thresholds", {})
            out = _apply_issue_filter(out, filter_type, proj, thresholds)

        return out
    except Exception as exc:
        logger.warning("Failed to fetch issues for project '%s': %s", proj.get("name", "?"), exc)
        return []



# ── JQL helpers (module-level, shared by summary & CL queries) ──────────────


def _field_name(obj: Any, key: str) -> str:
    """Safely extract a nested field name from a Jira API object."""
    if isinstance(obj, dict):
        val = obj.get(key)
        if isinstance(val, dict):
            return val.get("value", val.get("name", ""))
        if isinstance(val, str):
            return val
        if val is not None:
            return str(val)
    return ""


def _build_labels_clause(labels_val: str) -> Optional[str]:
    """Build JQL labels clause from comma-separated label values.

    Each label value is quoted to ensure Jira JQL parser treats it as
    a string literal rather than a field name or custom field reference.
    """
    parts = [l.strip() for l in labels_val.split(",") if l.strip()]
    if not parts:
        return None
    if len(parts) == 1:
        return f'labels = "{parts[0]}"'
    # Avoid JQL "IN" with empty items: use OR chain
    return " OR ".join(f'labels = "{p}"' for p in parts)


def _build_epic_clause(epics_val: str) -> Optional[str]:
    """Build JQL 'Epic Link' clause from comma-separated epic keys."""
    parts = [k.strip() for k in epics_val.split(",") if k.strip()]
    if not parts:
        return None
    keys = ",".join(parts)
    return f'"Epic Link" IN ({keys})'


# ── Client-side counting helpers ────────────────────────────────────────────
# These work around a Jira instance issue where `priority = P1` in JQL returns
# HTTP 400 despite issues having P1 priority value. We fetch issues and count
# in Python instead of relying on JQL subqueries.


def _is_date_before_window(date_str: str, window_days: int) -> bool:
    """Check if an absolute date is within the warning window (<= N days from now)."""
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
        except (ValueError, TypeError):
            return False
    now = datetime.now(_BJS_TZ)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_BJS_TZ)
    return (dt - now).days <= window_days


def _is_stale(updated_iso: str, stale_days: int) -> bool:
    """Check if an issue's updated time is older than stale_days."""
    if not updated_iso:
        return False
    try:
        updated = datetime.fromisoformat(updated_iso.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        try:
            updated = datetime.strptime(updated_iso, "%Y-%m-%dT%H:%M:%S.%f%z")
        except (ValueError, TypeError):
            return False
    now = datetime.now(_BJS_TZ)
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=_BJS_TZ)
    return (now - updated).days >= stale_days


def _is_eta_expiring(iss: Dict, eta_field: str, eta_days: int) -> bool:
    """Check if an issue's ETA/duedate is within the expiring window.

    Tries the configured eta_custom_field first, then falls back to
    the standard Jira 'duedate' field.
    """
    raw = iss.get(eta_field) if eta_field else None
    if raw and isinstance(raw, str) and raw.strip():
        return _is_date_before_window(raw.strip(), eta_days)
    raw = iss.get("duedate")
    if raw and isinstance(raw, str) and raw.strip():
        return _is_date_before_window(raw.strip(), eta_days)
    return False


# ── Summary ─────────────────────────────────────────────────────────────────


async def get_dashboard_summary(user_id: Optional[int] = None) -> Dict[str, Any]:
    """Aggregate project metrics from Jira via parallel JQL count queries.

    Returns:
        {
            "updated_at": "2026-06-15T10:30:00+08:00",
            "total": {"issues": N, "p1": N, "eta_expiring": N, "stale": N},
            "projects": [{...}],
            "errors": ["..."],
        }

    Note: This method does NOT query Gerrit CL status — 见 `cl_scan_service`.
    """
    # Check in-memory cache (per-user)
    user_cache = _cache.get(user_id)
    cached = user_cache.get("data") if user_cache else None
    cached_ts = user_cache.get("ts", 0) if user_cache else 0
    if cached and (time.time() - cached_ts) < _CACHE_TTL:
        logger.debug("Dashboard cache hit for user_id=%s", user_id)
        return cached

    config = await get_dashboard_config(user_id)
    projects = config.get("projects", [])
    thresholds = config.get("thresholds", {})
    stale_days = thresholds.get("stale_days", 7)
    eta_days = thresholds.get("eta_expiring_days", 7)

    # Resolve Jira MCP availability from user settings (统一 MCP 接口)
    servers = await _get_mcp_servers(user_id)
    if "jira" not in servers:
        result = _build_empty_result(["Jira MCP 未配置，请先在设置页面的「MCP 服务」中配置"])
        _set_cache(result, user_id)
        return result

    errors: List[str] = []
    project_results: List[Dict] = []

    # ── Inner helpers (统一走 MCPCapabilityService.call) ────

    async def _count(jql: str) -> Tuple[int, Optional[str]]:
        """Execute a JQL count query (maxResults=0) and return (total, error)."""
        try:
            resp = await _call_jira("search_issues", {
                "jql": jql,
                "maxResults": 0,
                "fields": ["summary"],
            }, user_id)
            if isinstance(resp, dict):
                return resp.get("total", 0), None
            return 0, f"Jira 返回异常格式: {type(resp).__name__}"
        except Exception as exc:
            err = f"JQL 查询失败: {str(exc)[:150]}"
            logger.warning("JQL count failed [%s]: %s [%s]", jql[:80], str(exc)[:150], type(exc).__name__)
            return 0, err

    async def _fetch_issues(jql: str, max_results: int = 50) -> Tuple[List[Dict], Optional[str]]:
        """Fetch issue list for a JQL query, return (issues, error)."""
        try:
            resp = await _call_jira("search_issues", {
                "jql": jql,
                "maxResults": max_results,
                "fields": ["key", "summary", "status", "priority", "assignee", "issuetype", "updated", "duedate"],
            }, user_id)
            issues = resp.get("issues", []) if isinstance(resp, dict) else []
            out = []
            for iss in issues:
                if not isinstance(iss, dict):
                    continue
                f = iss.get("fields", {})
                if not isinstance(f, dict):
                    f = {}
                assignee_raw = f.get("assignee")
                assignee_name = ""
                if isinstance(assignee_raw, dict):
                    assignee_name = assignee_raw.get("displayName", "")
                elif isinstance(assignee_raw, str):
                    assignee_name = assignee_raw
                out.append({
                    "key": iss.get("key", ""),
                    "summary": f.get("summary", ""),
                    "status": _field_name(f, "status"),
                    "priority": _field_name(f, "priority"),
                    "issuetype": _field_name(f, "issuetype"),
                    "assignee": assignee_name,
                    "updated": f.get("updated", ""),
                    "duedate": f.get("duedate", ""),
                })
            return out, None
        except Exception as exc:
            err = f"JQL 查询失败: {str(exc)[:150]}"
            logger.warning("JQL fetch issues failed [%s]: %s [%s]", jql[:80], str(exc)[:150], type(exc).__name__)
            return [], err

    async def _query_project(proj: Dict[str, Any]) -> Dict[str, Any]:
        name = proj.get("name", "未命名项目")
        if not proj.get("enabled", True):
            return _project_skipped(name)

        advanced_jql = proj.get("advanced_jql", "").strip()
        eta_field = proj.get("eta_custom_field", "")

        # ── Advanced JQL mode ──────────────────────────────────────────
        if advanced_jql:
            return await _query_project_advanced(name, advanced_jql, eta_field, eta_days, stale_days)

        # ── Standard structured mode ───────────────────────────────────
        project_key = proj.get("jira_project_key", "")
        labels_val = proj.get("labels", "")
        epics_val = proj.get("epics", "")
        exclude = proj.get("exclude_statuses", "Closed,Verified,Done")

        # Build base filter conditions (project + status)
        base_conds = [f"status not in ({exclude})"]
        if project_key:
            base_conds.insert(0, f"project = {project_key}")
        base_filter = " AND ".join(base_conds)

        # Labels clause
        labels_clause = _build_labels_clause(labels_val) if labels_val else None
        # Epic clause
        epic_clause = _build_epic_clause(epics_val) if epics_val else None

        has_both = bool(epic_clause and labels_clause)

        # Build counting JQL:
        #   - If both epic + labels: use UNION "(epic) OR (labels)"
        #   - If only labels: use labels filter
        #   - If only epics (no labels): use epic filter
        #   - If neither: use project filter only
        if has_both:
            or_parts = [f"({epic_clause})", f"({labels_clause})"]
            count_jql = f"{base_filter} AND ({' OR '.join(or_parts)})"
        elif labels_clause:
            count_jql = f"{base_filter} AND ({labels_clause})"
        elif epic_clause:
            count_jql = f"{base_filter} AND {epic_clause}"
        else:
            count_jql = base_filter

        # Labels-only JQL (for "匹配标签" secondary stat)
        labels_only_jql = f"{base_filter} AND ({labels_clause})" if labels_clause else None

        # Epic-only JQL (for epic_total count + issue fetching)
        epic_only_jql = f"{base_filter} AND {epic_clause}" if epic_clause else None

        # ── Build coro list ────────────────────────────────────────────
        coros = []
        coros.append(_count(count_jql))                                           # 0: total
        # Fetch full-scope issues for client-side P1/stale/ETA counting
        # (replaces JQL subqueries that fail on some Jira instances for priority)
        coros.append(_fetch_issues(count_jql, 200))                                # 1: count_issues

        # Labels-only count (when both epics and labels exist)
        labels_idx: Optional[int] = None
        if has_both and labels_only_jql:
            labels_idx = len(coros)
            coros.append(_count(labels_only_jql))

        # Epic scope count + issues fetch (for display)
        epic_total_idx: Optional[int] = None
        epic_issues_idx: Optional[int] = None
        if epic_only_jql:
            epic_total_idx = len(coros)
            coros.append(_count(epic_only_jql))
            epic_issues_idx = len(coros)
            coros.append(_fetch_issues(epic_only_jql, 200))

        # Labels scope issues fetch (when no epics configured, for display)
        labels_issues_idx: Optional[int] = None
        if labels_clause and not epic_clause:
            labels_issues_idx = len(coros)
            coros.append(_fetch_issues(labels_only_jql or count_jql, 200))

        # ── Execute ──────────────────────────────────────────────
        raw = await asyncio.gather(*coros, return_exceptions=True)

        # Helper to extract (value, error) from coroutine results
        def _extract(idx: int) -> Tuple[Any, Optional[str]]:
            if idx >= len(raw):
                return None, "missing"
            v = raw[idx]
            if isinstance(v, Exception):
                return None, f"查询异常: {str(v)[:150]}"
            if isinstance(v, tuple) and len(v) == 2:
                return v[0], v[1]
            return v, None

        total_val, total_err = _extract(0)
        total_val = total_val if isinstance(total_val, int) and total_val >= 0 else 0

        count_issues, count_issues_err = _extract(1)
        count_issues = count_issues if isinstance(count_issues, list) else []
        p0_val = sum(1 for i in count_issues if i.get("priority") == "P0")
        stale_val = sum(1 for i in count_issues if _is_stale(i.get("updated", ""), stale_days))
        eta_val = sum(1 for i in count_issues if _is_eta_expiring(i, eta_field, eta_days))

        labels_count_val = 0
        if labels_idx is not None:
            labels_count_val, labels_count_err = _extract(labels_idx)
            labels_count_val = labels_count_val if isinstance(labels_count_val, int) and labels_count_val >= 0 else 0
        else:
            labels_count_err = None

        result = {
            "name": name,
            "enabled": True,
            "issues": total_val,
            "p0": p0_val,
            "stale": stale_val,
            "eta_expiring": eta_val,
            "labels_count": labels_count_val,
            "epic_total": 0,
            "epic_issues": [],
            "error": None,
            "_count_issues": count_issues,
        }

        # Collect query errors
        query_errors: List[str] = []
        if total_err:
            query_errors.append(f"总数查询: {total_err}")
        if count_issues_err:
            query_errors.append(f"问题列表查询: {count_issues_err}")
        if labels_count_err:
            query_errors.append(f"标签计数查询: {labels_count_err}")

        epic_total_val = 0
        if epic_total_idx is not None:
            epic_total_val, epic_total_err = _extract(epic_total_idx)
            epic_total_val = epic_total_val if isinstance(epic_total_val, int) and epic_total_val >= 0 else 0
            result["epic_total"] = epic_total_val
            if epic_total_err:
                query_errors.append(f"Epic 计数查询: {epic_total_err}")

        if epic_issues_idx is not None and epic_issues_idx < len(raw):
            issues_data, epic_issues_err = _extract(epic_issues_idx)
            if isinstance(issues_data, list):
                result["epic_issues"] = issues_data
            if epic_issues_err:
                query_errors.append(f"Epic 问题列表查询: {epic_issues_err}")

        # Labels-only issues (for display when no epics configured)
        if labels_issues_idx is not None and labels_issues_idx < len(raw):
            issues_data, labels_issues_err = _extract(labels_issues_idx)
            if isinstance(issues_data, list):
                result["epic_issues"] = issues_data
            if labels_issues_err:
                query_errors.append(f"标签问题列表查询: {labels_issues_err}")

        if query_errors:
            detail = "; ".join(query_errors)
            result["error"] = f"部分查询失败: {detail}"
            errors.append(f"[{name}] {detail}")

        return result

    async def _query_project_advanced(
        name: str, jql: str, eta_field: str, eta_days: int, stale_days: int,
    ) -> Dict[str, Any]:
        """Execute a project query using a user-provided custom JQL directly.

        P1/stale/ETA counts are computed client-side from fetched issues
        rather than via JQL subqueries, to work around Jira instances where
        `priority = P1` returns a 400 error despite P1 being a valid value.
        """
        total_tuple, issues_tuple = await asyncio.gather(
            _count(jql),
            _fetch_issues(jql, 200),
            return_exceptions=True,
        )

        total = 0
        total_err = None
        if isinstance(total_tuple, Exception):
            total_err = f"总数查询异常: {str(total_tuple)[:150]}"
            logger.warning("[%s] Total count query failed: %s", name, total_tuple)
        elif isinstance(total_tuple, tuple):
            total, total_err = total_tuple
            total = total if isinstance(total, int) and total >= 0 else 0

        issues: List[Dict] = []
        issues_err = None
        if isinstance(issues_tuple, Exception):
            issues_err = f"问题列表查询异常: {str(issues_tuple)[:150]}"
            logger.warning("[%s] Issues fetch failed: %s", name, issues_tuple)
        elif isinstance(issues_tuple, tuple):
            issues, issues_err = issues_tuple
            issues = issues if isinstance(issues, list) else []

        p0_val = sum(1 for i in issues if i.get("priority") == "P0")
        stale_val = sum(1 for i in issues if _is_stale(i.get("updated", ""), stale_days))
        eta_val = sum(1 for i in issues if _is_eta_expiring(i, eta_field, eta_days))

        query_errors: List[str] = []
        if total_err:
            query_errors.append(total_err)
        if issues_err:
            query_errors.append(issues_err)

        result: Dict[str, Any] = {
            "name": name,
            "enabled": True,
            "issues": total,
            "p0": p0_val,
            "stale": stale_val,
            "eta_expiring": eta_val,
            "labels_count": 0,
            "epic_total": 0,
            "epic_issues": issues,
            "error": "; ".join(query_errors) if query_errors else None,
            "_count_issues": issues,
        }
        if query_errors:
            errors.append(f"[{name}] " + "; ".join(query_errors))
        return result

    # ── Execute ─────────────────────────────────────────────────────────

    enabled = [p for p in projects if p.get("enabled", True)]
    if enabled:
        tasks = [_query_project(p) for p in enabled]
        for r in await asyncio.gather(*tasks, return_exceptions=True):
            if isinstance(r, Exception):
                errors.append(f"项目查询异常: {r}")
            else:
                project_results.append(r)

    # Aggregate totals across enabled projects
    total = {"issues": 0, "p0": 0, "eta_expiring": 0, "stale": 0, "epic_total": 0}
    for p in project_results:
        if p.get("enabled"):
            for k in total:
                total[k] += p.get(k, 0)

    # Build issue snapshot for trend comparison (grouped by project)
    issue_snapshot: Dict[str, Dict[str, Dict[str, str]]] = {}
    for p in project_results:
        proj_name = p.get("name", "unknown")
        proj_snap: Dict[str, Dict[str, str]] = {}
        raw_issues = p.pop("_count_issues", []) if isinstance(p, dict) else []
        if isinstance(raw_issues, list):
            for iss in raw_issues:
                if isinstance(iss, dict) and iss.get("key"):
                    key = iss["key"]
                    proj_snap[key] = {
                        "status": iss.get("status", ""),
                        "summary": iss.get("summary", ""),
                        "priority": iss.get("priority", ""),
                        "issuetype": iss.get("issuetype", ""),
                    }
        if proj_snap:
            issue_snapshot[proj_name] = proj_snap

    jira_web_url = config.get("jira_web_url", "").rstrip("/")

    result = {
        "updated_at": _now_iso(),
        "total": total,
        "projects": project_results,
        "errors": errors if errors else None,
        "jira_web_url": jira_web_url,
        "_issue_snapshot": issue_snapshot,
    }

    _set_cache(result, user_id)
    return result


# ── CL Status (for alerts page, independent of dashboard) ────────────────────


def build_cl_jql(proj: Dict[str, Any]) -> Optional[str]:
    """构建 CL 监控项目的检索条件。

    二选一，不再交叉回退（2026-09-16 修复）：
      - `advanced_jql` 有值 -> **原样使用**，代码不追加任何状态条件；
      - 否则 -> 由基本模式字段（jira_project_key / labels / epics）拼装。

    背景：旧实现只在基本字段全为空时才使用 advanced_jql，导致用户在 UI 上
    看到的是 JQL 模式，实际生效的却是隐藏残留的 labels，监控范围静默跑偏。

    Args:
        proj: CL 监控项目配置。

    Returns:
        JQL 字符串；无任何可用条件时返回 None。
    """
    advanced_jql = (proj.get("advanced_jql") or "").strip()
    if advanced_jql:
        return advanced_jql

    project_key = (proj.get("jira_project_key") or "").strip()
    labels_val = (proj.get("labels") or "").strip()
    epics_val = (proj.get("epics") or "").strip()

    conds = []
    if project_key:
        conds.append(f"project = {project_key}")

    labels_clause = _build_labels_clause(labels_val) if labels_val else None
    epic_clause = _build_epic_clause(epics_val) if epics_val else None
    # labels 与 epics 之间是 OR（与 dashboard 侧口径一致）
    scope_parts = [c for c in (labels_clause, epic_clause) if c]
    if len(scope_parts) == 1:
        conds.append(f"({scope_parts[0]})")
    elif scope_parts:
        conds.append("(" + " OR ".join(f"({c})" for c in scope_parts) + ")")

    return " AND ".join(conds) if conds else None



# ── Issue filter for project-issues endpoint ──────────────────────────────


def _apply_issue_filter(
    issues: List[Dict], filter_type: str,
    proj_config: Optional[Dict] = None,
    thresholds: Optional[Dict] = None,
) -> List[Dict]:
    """Client-side filter for the project-issues endpoint.

    filter_type: "p0" | "eta_expiring" | "stale"
    """
    if filter_type == "all" or not filter_type:
        return issues
    if filter_type == "p0":
        return [i for i in issues if i.get("priority") == "P0"]
    if filter_type == "eta_expiring":
        eta_field = proj_config.get("eta_custom_field", "") if proj_config else ""
        eta_days = thresholds.get("eta_expiring_days", 7) if thresholds else 7
        return [i for i in issues if _is_eta_expiring(i, eta_field, eta_days)]
    if filter_type == "stale":
        stale_days = thresholds.get("stale_days", 7) if thresholds else 7
        return [i for i in issues if _is_stale(i.get("updated", ""), stale_days)]
    return issues


# ── Admin aggregation (see all users' project data) ──────────────────────────


async def admin_get_all_users_summary() -> Dict[str, Any]:
    """Aggregate dashboard summaries for all non-superuser users.

    For each user, loads their dashboard config and fetches summary data.
    Results are merged into a single response with each project annotated
    by the username who configured it.

    Admin-only endpoint — intended for ?view=all query parameter.

    Returns:
        {
            "updated_at": "...",
            "users": [
                {
                    "user_id": 2,
                    "username": "张三",
                    "projects": [{...}, ...],
                    "total": {"issues": N, ...},
                },
                ...
            ],
            "total": {"issues": N, ...},
        }
    """
    from app.db.session import AsyncSessionLocal
    from app.models.user import User
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(User).where(User.is_superuser == False, User.is_active == True)
            .order_by(User.id)
        )
        users = result.scalars().all()

    if not users:
        return {
            "updated_at": _now_iso(),
            "users": [],
            "total": {"issues": 0, "p0": 0, "eta_expiring": 0, "stale": 0},
        }

    user_summaries: List[Dict] = []
    grand_total = {"issues": 0, "p0": 0, "eta_expiring": 0, "stale": 0}

    for u in users:
        try:
            summary = await get_dashboard_summary(user_id=u.id)
        except Exception as e:
            logger.warning("Admin aggregation: failed to load summary for user %s: %s", u.username, e)
            continue

        user_projects = [p for p in summary.get("projects", []) if p.get("enabled")]
        if not user_projects:
            continue

        user_entry = {
            "user_id": u.id,
            "username": u.username or u.full_name or f"用户{u.id}",
            "total": summary.get("total", {}),
            "projects": user_projects,
        }
        user_summaries.append(user_entry)

        for k in grand_total:
            grand_total[k] += summary.get("total", {}).get(k, 0)

    return {
        "updated_at": _now_iso(),
        "users": user_summaries,
        "total": grand_total,
    }


def _now_iso() -> str:
    return datetime.now(_BJS_TZ).isoformat()


def _set_cache(data: Dict[str, Any], user_id: Optional[int] = None) -> None:
    _cache[user_id] = {"data": data, "ts": time.time()}
    logger.debug("Dashboard cache set for user_id=%s", user_id)


def _build_empty_result(errors: List[str]) -> Dict[str, Any]:
    return {
        "updated_at": _now_iso(),
        "total": {"issues": 0, "p0": 0, "eta_expiring": 0, "stale": 0, "epic_total": 0},
        "projects": [],
        "errors": errors,
    }


def _project_skipped(name: str) -> Dict[str, Any]:
    return {
        "name": name,
        "enabled": False,
        "issues": 0, "p0": 0, "eta_expiring": 0, "stale": 0,
        "error": None,
    }
