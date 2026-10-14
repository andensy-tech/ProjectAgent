"""
Gerrit CL query service — extract CL references from Jira issues and query Gerrit status.

Reuses CL extraction patterns from jira_cl_monitor_skill.py for use by the dashboard.
Supports Jira-grouped output matching the skill's presentation logic.
"""

import asyncio
import os
import re
import logging
from typing import Dict, List, Any, Optional, Set, Tuple

logger = logging.getLogger(__name__)


def _load_instance_map() -> Dict[str, str]:
    """Load Gerrit host -> instance name mapping.

    Resolution order:
      1. ``GERRIT_HOSTS`` env var: comma-separated ``host=instance`` pairs,
         e.g. ``GERRIT_HOSTS="gerrit-1.example.com=gerrit1,gerrit-2.example.com=gerrit2"``
      2. Neutral example defaults (suitable for a fresh deployment).

    The instance name doubles as the MCP client suffix (``gerrit_<instance>``).
    """
    raw = os.environ.get("GERRIT_HOSTS", "")
    mapping: Dict[str, str] = {}
    for pair in raw.split(","):
        if "=" in pair:
            host, name = pair.split("=", 1)
            host, name = host.strip(), name.strip()
            if host and name:
                mapping[host] = name
    if mapping:
        return mapping
    return {
        "gerrit-1.example.com": "gerrit1",
        "gerrit-2.example.com": "gerrit2",
    }


# Gerrit instance host -> instance name（MCP 客户端名 = gerrit_<instance>）
GERRIT_INSTANCE_MAP: Dict[str, str] = _load_instance_map()

# 反查表：instance name -> host（构建 CL 链接时使用）
GERRIT_HOST_BY_INSTANCE: Dict[str, str] = {v: k for k, v in GERRIT_INSTANCE_MAP.items()}

# 默认实例（第一个映射项）
DEFAULT_GERRIT_INSTANCE: str = next(iter(GERRIT_INSTANCE_MAP.values()), "gerrit1")

_HOSTS_PATTERN = "|".join(re.escape(h) for h in GERRIT_INSTANCE_MAP)

# 完整形式：https://<host>/c/<project>/+/<id>
CL_URL_PATTERN = re.compile(
    rf"https://(?P<host>{_HOSTS_PATTERN})/c/(?P<project>.+?)/\+/(?P<change_id>\d+)"
)

# 短链形式：https://<host>/<id>
# 实测该短链不是 Gerrit permalink（change:<id> 查询返回 0 条），只能靠机器人评论判定状态
SHORT_CL_URL_PATTERN = re.compile(
    rf"https://(?P<host>{_HOSTS_PATTERN})/(?P<change_id>\d+)(?![\d/])"
)

# 机器人"提交"评论（带项目/分支/主题元信息）。URL 与括号之间实际是换行，
# 也兼容历史上出现过的 " | " 分隔写在同一行的情况。
#   Change proposed: '<subject>' (<project> <branch>)
#   https://<gerrit-host>/<id>
CL_PROPOSED_PATTERN = re.compile(
    r"Change proposed:\s*'(?P<subject>.+?)'\s*\((?P<project>\S+)\s+(?P<branch>[^)]*)\)"
    r"[\s|]+(?P<url>https?://\S+)"
)

# 机器人"合并"评论：Change merged from <cl_url> to <commit_url>
CL_MERGED_PATTERN = re.compile(r"Change merged from\s+(?P<url>https?://\S+?)\s+to\b")

# Gerrit client name prefix
GERRIT_CLIENT_PREFIX = "gerrit_"

# Status values considered "merged"（统一大写后比对，兼容中文状态）
MERGED_STATUSES = frozenset({"MERGED", "MERGING", "已合并", "已合入"})

# CL 状态：短链无法查 Gerrit，仅有"提交"评论时视为已提交未合并
UNMERGED_SUBMITTED = "PROPOSED"


def is_merged(status: Optional[str]) -> bool:
    """判断 CL 状态是否属于"已合并"（大小写不敏感，兼容中文实例）。"""
    return str(status or "").strip().upper() in MERGED_STATUSES


def _parse_cl_url(url: str) -> Optional[Tuple[str, str]]:
    """解析 CL URL，返回 (instance, change_id)；无法识别返回 None。"""
    m = CL_URL_PATTERN.search(url)
    if m:
        return (GERRIT_INSTANCE_MAP[m.group("host")], m.group("change_id"))
    m = SHORT_CL_URL_PATTERN.search(url)
    if m:
        return (GERRIT_INSTANCE_MAP[m.group("host")], m.group("change_id"))
    return None


def extract_cls_from_comments(comments: List[Dict], jira_key: str) -> List[Dict[str, Any]]:
    """从 Jira 评论中提取 CL 引用与合并证据。

    支持两类来源：
      1. 完整 Gerrit URL：https://<host>/c/<project>/+/<id>
         —— 可经 Gerrit MCP 查询权威状态。
      2. 短链（机器人评论）：https://<host>/<id>
         —— 短链不是 Gerrit permalink，无法查询；但机器人成对记录了
            "Change proposed" 与 "Change merged from"，合并状态可直接由评论确定。

    Args:
        comments: Jira get_issue_comments 返回的评论列表。
        jira_key: 所属 Jira key。

    Returns:
        CL 列表，按 (instance, change_id) 去重。每项字段：
        instance/project/change_id/jira_key/subject/branch/url_form/merged_evidence。
    """
    found: Dict[Tuple[str, str], Dict[str, Any]] = {}
    merged_keys: Set[Tuple[str, str]] = set()

    def _entry(instance: str, change_id: str, url_form: str,
               project: str = "", subject: str = "", branch: str = "") -> Dict[str, Any]:
        """取或建 CL 条目，后到的元信息只补齐不覆盖。"""
        key = (instance, change_id)
        item = found.get(key)
        if item is None:
            item = {
                "instance": instance,
                "project": project,
                "change_id": change_id,
                "jira_key": jira_key,
                "subject": subject,
                "branch": branch,
                "url_form": url_form,
                "merged_evidence": False,
            }
            found[key] = item
        else:
            if project and not item["project"]:
                item["project"] = project
            if subject and not item["subject"]:
                item["subject"] = subject
            if branch and not item["branch"]:
                item["branch"] = branch
            if url_form == "full":
                item["url_form"] = "full"  # 完整 URL 可查 Gerrit，优先
        return item

    for comment in comments or []:
        if not isinstance(comment, dict):
            continue
        body = comment.get("body", "") or ""
        if not body:
            continue

        # ① 机器人"提交"评论：含 subject/project/branch 元信息
        for m in CL_PROPOSED_PATTERN.finditer(body):
            parsed = _parse_cl_url(m.group("url"))
            if parsed:
                instance, change_id = parsed
                _entry(instance, change_id,
                       "full" if CL_URL_PATTERN.search(m.group("url")) else "short",
                       project=m.group("project"), subject=m.group("subject"),
                       branch=m.group("branch"))

        # ② 任意评论中的完整 Gerrit URL
        for m in CL_URL_PATTERN.finditer(body):
            _entry(GERRIT_INSTANCE_MAP[m.group("host")], m.group("change_id"), "full",
                   project=m.group("project"))

        # ③ 机器人"合并"评论 -> 合并证据
        for m in CL_MERGED_PATTERN.finditer(body):
            parsed = _parse_cl_url(m.group("url"))
            if parsed:
                merged_keys.add(parsed)
                _entry(parsed[0], parsed[1],
                       "full" if CL_URL_PATTERN.search(m.group("url")) else "short")

    for key in merged_keys:
        if key in found:
            found[key]["merged_evidence"] = True

    return list(found.values())

# Max concurrent Gerrit / Jira queries per project
_MAX_CONCURRENT = 5


def _parse_jira_field(issue: Dict, field: str) -> str:
    """Safely extract a nested field value from a Jira issue dict."""
    fields = issue.get("fields", {}) if isinstance(issue, dict) else {}
    val = fields.get(field, "")
    if isinstance(val, dict):
        return val.get("name", val.get("displayName", "")) or ""
    return str(val) if val else ""


def _parse_assignee(issue: Dict) -> str:
    """Extract assignee displayName from a Jira issue dict.

    MCP protocol may return assignee as a plain string (displayName),
    a dict with displayName key, or None/empty when unassigned.
    """
    fields = issue.get("fields", {}) if isinstance(issue, dict) else {}
    assignee = fields.get("assignee")
    if isinstance(assignee, str):
        return assignee.strip() or "Unassigned"
    if isinstance(assignee, dict):
        return assignee.get("displayName", "Unassigned") or "Unassigned"
    return "Unassigned"


async def get_cl_status_for_issues(issues: List[Dict],
                                    user_id: Optional[int] = None) -> Dict[str, Any]:
    """Extract CL references from Jira issue comments and query Gerrit status.

    Groups results by Jira key (matching the skill report presentation),
    enriching each group with Jira metadata (summary, status, assignee).

    Args:
        issues: List of Jira issue dicts.
        user_id: If provided, resolves per-user MCP auth for Jira and Gerrit calls.
                All MCP config comes from per-user DB settings, no config.yaml.
    """
    if not issues:
        return {"total_cls": 0, "merged_cls": 0, "unmerged_cls": 0,
                "total_jiras": 0, "jiras_unmerged": 0, "jiras_merged": 0,
                "jira_groups": []}

    # 统一 MCP 接口：从 MCPCapabilityService 获取能力清单（per-user，热启动）
    from app.mcp.capability import capability
    servers = await capability.get_servers(user_id)
    if "jira" not in servers:
        return {"total_cls": 0, "merged_cls": 0, "unmerged_cls": 0,
                "total_jiras": 0, "jiras_unmerged": 0, "jiras_merged": 0,
                "jira_groups": [],
                "error": "Jira MCP 未配置，请先在设置页面的「MCP 服务」中配置"}

    sem = asyncio.Semaphore(_MAX_CONCURRENT)

    # 评论拉取失败的题：结果里标出来，调用方据此**不要**把该题判成"未登记 CL"，
    # 否则一次 Jira 抖动就会误报一大批"漏提 CL"（V1 曾因同类静默清零出过故障）
    failed_keys: List[str] = []

    # Build index of issue data by key for later enrichment
    issue_map: Dict[str, Dict] = {}
    for iss in issues:
        k = iss.get("key", "")
        if k:
            issue_map[k] = iss

    async def _fetch_cls_from_issue(issue_key: str) -> List[Dict]:
        """Get comments for one issue and extract CL references."""
        async with sem:
            try:
                result = await capability.call("jira", "get_issue_comments", {"issue_key": issue_key}, user_id)
                comments = result.get("comments", []) if isinstance(result, dict) else []
                return extract_cls_from_comments(comments, issue_key)
            except Exception as e:
                logger.debug("get_issue_comments(%s) failed: %s", issue_key, e)
                failed_keys.append(issue_key)
                return []

    # Step 1: extract CLs from all issue comments in parallel
    keys = [iss["key"] for iss in issues if iss.get("key")]
    if not keys:
        return {"total_cls": 0, "merged_cls": 0, "unmerged_cls": 0,
                "total_jiras": 0, "jiras_unmerged": 0, "jiras_merged": 0,
                "jira_groups": []}

    raw_batches = await asyncio.gather(
        *[_fetch_cls_from_issue(k) for k in keys],
        return_exceptions=True,
    )

    # Flatten + deduplicate。去重键必须含 jira_key：同一个 CL 常被多个 Jira 引用
    # （如 release/集成类 CL），只按 (project, change_id) 去重会让后一个 Jira 整组消失。
    all_cls: List[Dict] = []
    seen: set = set()
    for batch in raw_batches:
        if not isinstance(batch, list):
            continue
        for cl in batch:
            dedup_key = (cl["jira_key"], cl["instance"], cl["change_id"])
            if dedup_key not in seen:
                seen.add(dedup_key)
                all_cls.append(cl)

    if not all_cls:
        return {"total_cls": 0, "merged_cls": 0, "unmerged_cls": 0,
                "total_jiras": 0, "jiras_unmerged": 0, "jiras_merged": 0,
                "jira_groups": [], "failed_keys": failed_keys}

    # Step 2: query Gerrit for each CL's status (with concurrency limit)
    gerrit_sem = asyncio.Semaphore(_MAX_CONCURRENT)

    async def _query_gerrit(cl: Dict) -> Optional[Dict]:
        instance = cl.get("instance") or DEFAULT_GERRIT_INSTANCE
        base = {
            "change_id": cl["change_id"],
            "project": cl.get("project", ""),
            "jira_key": cl["jira_key"],
            "instance": instance,
            "branch": cl.get("branch", ""),
            "subject": cl.get("subject", ""),
            "url_form": cl.get("url_form", "full"),
        }
        # 机器人评论已声明合并 -> 直接判定，省一次 Gerrit 查询（MCP 不可达时仍准确）
        if cl.get("merged_evidence"):
            return {**base, "status": "MERGED"}
        # 短链不是 Gerrit permalink，查不到；仅能靠评论判定"已提交未合并"
        if cl.get("url_form") == "short":
            return {**base, "status": UNMERGED_SUBMITTED}

        async with gerrit_sem:
            client_name = f"{GERRIT_CLIENT_PREFIX}{instance}"
            if client_name not in servers:
                # 模糊匹配：处理非标准服务器名（如用户配置 gerrit_<hostname> 而非 gerrit_<instance>）
                matched = next(
                    (k for k in servers if "gerrit" in k and instance.lower() in k.lower()),
                    None,
                )
                if matched:
                    client_name = matched
                else:
                    logger.debug("Gerrit client %s not configured for user", client_name)
                    return None
            try:
                result = await capability.call(client_name, "get_change", {
                    "project": cl["project"],
                    "change_id": cl["change_id"],
                }, user_id)
                text = result if isinstance(result, str) else str(result)
                status_m = re.search(r"\*\*状态\*\*:\s*(\w+)", text)
                status = status_m.group(1) if status_m else "Unknown"
                branch_m = re.search(r"\*\*分支\*\*:\s*(.+)", text)
                branch = branch_m.group(1).strip() if branch_m else base["branch"]
                subject_m = re.search(r"\*\*主题\*\*:\s*(.+)", text)
                subject = subject_m.group(1).strip() if subject_m else base["subject"]
                return {**base, "status": status, "branch": branch, "subject": subject}
            except Exception as e:
                logger.warning("Gerrit query failed for %s/%s: %s",
                               cl["project"], cl["change_id"], e)
                return {**base, "status": "Error"}

    cl_results = await asyncio.gather(
        *[_query_gerrit(cl) for cl in all_cls],
        return_exceptions=True,
    )

    items = [r for r in cl_results if isinstance(r, dict)]

    # Step 3: group CL items by jira_key, enriched with Jira metadata
    jira_groups: Dict[str, Dict] = {}
    for item in items:
        jk = item["jira_key"]
        if jk not in jira_groups:
            iss = issue_map.get(jk, {})
            jira_groups[jk] = {
                "jira_key": jk,
                "summary": _parse_jira_field(iss, "summary") or "",
                "assignee": _parse_assignee(iss),
                "status": _parse_jira_field(iss, "status") or "",
                "total_cls": 0,
                "merged_cls": 0,
                "unmerged_cls": 0,
                "items": [],
            }
        group = jira_groups[jk]
        group["items"].append(item)
        group["total_cls"] += 1
        if is_merged(item["status"]):
            group["merged_cls"] += 1
        else:
            group["unmerged_cls"] += 1

    groups_list = list(jira_groups.values())
    total_jiras = len(groups_list)
    jiras_unmerged = sum(1 for g in groups_list if g["unmerged_cls"] > 0)
    jiras_merged = total_jiras - jiras_unmerged

    merged_total = sum(g["merged_cls"] for g in groups_list)
    unmerged_total = sum(g["unmerged_cls"] for g in groups_list)

    return {
        "total_cls": merged_total + unmerged_total,
        "merged_cls": merged_total,
        "unmerged_cls": unmerged_total,
        "total_jiras": total_jiras,
        "jiras_unmerged": jiras_unmerged,
        "jiras_merged": jiras_merged,
        "jira_groups": groups_list,
        "failed_keys": failed_keys,
    }
