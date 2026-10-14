"""
Jira CL Merge Monitor Skill Adapter

Async adapter that wraps the jira-cl-merge-monitor logic.
Uses the backend's MCPManager for Jira/Gerrit/Confluence queries.
"""

import json
import os
import re
from datetime import datetime
from typing import Dict, List, Any, Optional

from app.mcp import mcp_manager
from app.core.config import settings
from app.services.gerrit_service import GERRIT_INSTANCE_MAP, DEFAULT_GERRIT_INSTANCE


# Jira / Confluence 外部链接前缀：部署时经环境变量覆盖为真实地址
JIRA_WEB_URL = os.environ.get("JIRA_WEB_URL", "https://jira.example.com")
CONFLUENCE_BASE_URL = os.environ.get("CONFLUENCE_BASE_URL", "https://confluence.example.com")

# 完整 CL URL 正则：从 Gerrit 实例映射推导（host 白名单）
_CL_HOSTS = "|".join(re.escape(h) for h in GERRIT_INSTANCE_MAP)


# Default configuration
DEFAULT_CONFIG = {
    "jira": {
        "label": os.environ.get("JIRA_MONITOR_LABEL", "release_verified"),
        "epics": settings.JIRA_MONITOR_EPICS.split(",") if settings.JIRA_MONITOR_EPICS else [],
        "status_filter": "Verified",
    },
    "gerrit": {
        "cl_url_pattern": rf"https://({_CL_HOSTS})/c/(.+?)/\+/(\d+)",
    },
    "confluence": {
        "space_key": os.environ.get("CONFLUENCE_SPACE_KEY", "~YourName"),
        "parent_id": os.environ.get("CONFLUENCE_PARENT_ID", ""),
        "title_template": "CL Merge Monitor Report - {timestamp}",
    },
}


def _escape_html(text: Any) -> str:
    if not isinstance(text, str):
        text = str(text) if text is not None else ""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _status_badge(status: str) -> str:
    """Generate a colored status badge HTML."""
    colors = {
        "MERGED": "#36b37e",
        "Merged": "#36b37e",
        "ABANDONED": "#de350b",
        "NEW": "#ff8b00",
        "DRAFT": "#626f86",
    }
    bg = colors.get(status, "#0052cc")
    return (
        f'<span style="background:{bg};color:white;padding:2px 8px;'
        f'border-radius:3px;font-size:12px;">{status}</span>'
    )


async def _collect_verified_jiras(config: Dict) -> Dict[str, Dict]:
    """Collect all Verified Jiras by label and epic."""
    jira = mcp_manager.get_client("jira")
    if not jira:
        raise RuntimeError("Jira MCP client not available")

    all_keys = set()
    label = config["jira"]["label"]
    status_filter = config["jira"]["status_filter"]

    # 1. Search by label
    result = await jira.call_tool(
        "search_issues",
        {
            "jql": f'labels = {label} AND status = {status_filter}',
            "max_results": 1000,
        },
    )
    for issue in result.get("issues", []):
        all_keys.add(issue.get("key"))

    # 2. Search by epic
    for epic in config["jira"]["epics"]:
        result = await jira.call_tool(
            "search_issues",
            {
                "jql": f'"Epic Link" = {epic} AND status = {status_filter}',
                "max_results": 1000,
            },
        )
        for issue in result.get("issues", []):
            all_keys.add(issue.get("key"))

    # 3. Get full details
    jira_details = {}
    for key in sorted(all_keys):
        full = await jira.call_tool("get_issue", {"issue_key": key})
        jira_details[key] = full

    return jira_details


async def _extract_cls(jira_details: Dict[str, Dict], config: Dict) -> List[Dict]:
    """Extract CL URLs from Jira comments."""
    jira = mcp_manager.get_client("jira")
    if not jira:
        raise RuntimeError("Jira MCP client not available")

    pattern = re.compile(config["gerrit"]["cl_url_pattern"])
    all_cls = []

    for key, issue in jira_details.items():
        comments_result = await jira.call_tool(
            "get_issue_comments", {"issue_key": key}
        )
        comments = comments_result.get("comments", [])

        for comment in comments:
            body = comment.get("body", "")
            for match in pattern.finditer(body):
                all_cls.append({
                    "instance": GERRIT_INSTANCE_MAP.get(match.group(1), match.group(1)),
                    "project": match.group(2),
                    "change_id": match.group(3),
                    "jira": key,
                })

    # Deduplicate
    seen = set()
    unique_cls = []
    for cl in all_cls:
        k = (cl["project"], cl["change_id"])
        if k not in seen:
            seen.add(k)
            unique_cls.append(cl)

    return unique_cls


async def _query_cl_status(cls: List[Dict]) -> Dict[tuple, str]:
    """Query Gerrit for CL status. Routes to correct instance per CL URL."""
    cl_status = {}
    for cl in cls:
        try:
            instance = cl.get("instance") or DEFAULT_GERRIT_INSTANCE
            client_name = f"gerrit_{instance}"
            gerrit = mcp_manager.get_client(client_name)
            if not gerrit:
                cl_status[(cl["project"], cl["change_id"])] = "Error"
                continue

            result = await gerrit.call_tool(
                "get_change",
                {
                    "project": cl["project"],
                    "change_id": cl["change_id"],
                },
            )
            text = result if isinstance(result, str) else str(result)
            status_match = re.search(r"\*\*状态\*\*:\s*(\w+)", text)
            status = status_match.group(1) if status_match else "Unknown"
            cl_status[(cl["project"], cl["change_id"])] = status
        except Exception:
            cl_status[(cl["project"], cl["change_id"])] = "Error"

    return cl_status


def _analyze_risks(
    cls: List[Dict],
    cl_status: Dict[tuple, str],
    jira_details: Dict[str, Dict],
) -> Dict[str, List[Dict]]:
    """Analyze risks based on CL status and Jira metadata."""
    risks = {
        "unmerged_cl_blocking": [],
        "long_pending_cl": [],
        "epic_convergence_risk": [],
        "too_many_cls": [],
        "missing_assignee": [],
    }

    unmerged = [
        cl for cl in cls
        if cl_status.get((cl["project"], cl["change_id"])) not in ["MERGED", "Merged"]
    ]

    # Risk 1: P1 Jira with unmerged CL
    for cl in unmerged:
        key = cl["jira"]
        fields = jira_details.get(key, {}).get("fields", {})
        if fields.get("priority") == "P1":
            risks["unmerged_cl_blocking"].append(cl)

    # Risk 2: CL in NEW status for too long (we don't have creation time from Gerrit MCP)
    # Skip for now - would need additional Gerrit fields

    # Risk 3: Epic convergence - multiple unmerged CLs in same epic
    epic_cls_count = {}
    for cl in unmerged:
        key = cl["jira"]
        fields = jira_details.get(key, {}).get("fields", {})
        epic = fields.get(settings.JIRA_CUSTOM_FIELD_EPIC_LINK, "")  # Epic Link field
        if epic:
            epic_cls_count[epic] = epic_cls_count.get(epic, 0) + 1

    for epic, count in epic_cls_count.items():
        if count >= 3:
            risks["epic_convergence_risk"].append({
                "epic": epic,
                "unmerged_count": count,
            })

    # Risk 4: Jira with too many CLs
    jira_cl_count = {}
    for cl in cls:
        key = cl["jira"]
        jira_cl_count[key] = jira_cl_count.get(key, 0) + 1

    for key, count in jira_cl_count.items():
        if count >= 5:
            risks["too_many_cls"].append({
                "jira": key,
                "cl_count": count,
            })

    # Risk 5: Missing assignee
    for cl in unmerged:
        key = cl["jira"]
        fields = jira_details.get(key, {}).get("fields", {})
        assignee = fields.get("assignee", "")
        if not assignee or assignee == "None":
            risks["missing_assignee"].append(cl)

    return risks


def _build_report_html(
    jira_details: Dict[str, Dict],
    cls: List[Dict],
    cl_status: Dict[tuple, str],
    risks: Dict[str, List[Dict]],
    config: Dict,
    timestamp: str,
) -> str:
    """Generate HTML report."""
    unmerged = [
        cl for cl in cls
        if cl_status.get((cl["project"], cl["change_id"])) not in ["MERGED", "Merged"]
    ]
    merged = [
        cl for cl in cls
        if cl_status.get((cl["project"], cl["change_id"])) in ["MERGED", "Merged"]
    ]

    epics = ", ".join(config["jira"]["epics"])

    html = (
        f'<h1 style="color:#172b4d;border-bottom:2px solid #0052cc;padding-bottom:10px;">'
        f'CL Merge Monitor Report</h1>'
        f'<div style="background:#f4f5f7;border-radius:8px;padding:15px 20px;margin:15px 0;">'
        f'<div><strong>生成时间</strong> {timestamp}</div>'
        f'<div><strong>Label</strong> {config["jira"]["label"]}</div>'
        f'<div><strong>Epics</strong> {epics}</div>'
        f'<div><strong>状态筛选</strong> {config["jira"]["status_filter"]}</div>'
        f'<div style="margin-top:8px;"><strong>统计</strong> '
        f'已验证Jira：<strong>{len(jira_details)}</strong> | '
        f'关联CL总数：<strong>{len(cls)}</strong> | '
        f'已合并：<strong style="color:#36b37e;">{len(merged)}</strong> | '
        f'未合并：<strong style="color:#de350b;">{len(unmerged)}</strong></div>'
        f'</div>'
    )

    # Risk warnings
    risk_count = sum(len(v) for v in risks.values() if isinstance(v, list))
    if risk_count > 0:
        html += '<h2 style="color:#de350b;">风险告警</h2>'

        if risks["unmerged_cl_blocking"]:
            html += (
                '<h3 style="color:#de350b;">P1 Jira关联的未合并CL</h3>'
                '<p>以下P1优先级的Jira存在未合并的CL，可能阻塞后续工作</p><ul>'
            )
            for cl in risks["unmerged_cl_blocking"]:
                status = cl_status.get((cl["project"], cl["change_id"]), "Unknown")
                html += (
                    f'<li><a href="{JIRA_WEB_URL}/browse/{cl["jira"]}">'
                    f'{cl["jira"]}</a> - CL {cl["change_id"]} ({_escape_html(cl["project"])}) '
                    f'- {_status_badge(status)}</li>'
                )
            html += '</ul>'

        if risks["epic_convergence_risk"]:
            html += '<h3 style="color:#ff8b00;">Epic收敛风险</h3><p>以下Epic有多个未合并CL</p><ul>'
            for risk in risks["epic_convergence_risk"]:
                html += (
                    f'<li><a href="{JIRA_WEB_URL}/browse/{risk["epic"]}">'
                    f'{risk["epic"]}</a> - {risk["unmerged_count"]} 个未合并CL</li>'
                )
            html += '</ul>'

        if risks["too_many_cls"]:
            html += '<h3 style="color:#ff8b00;">CL数量过多</h3><p>以下Jira关联了过多CL</p><ul>'
            for risk in risks["too_many_cls"]:
                html += (
                    f'<li><a href="{JIRA_WEB_URL}/browse/{risk["jira"]}">'
                    f'{risk["jira"]}</a> - {risk["cl_count"]} 个CL</li>'
                )
            html += '</ul>'

        if risks["missing_assignee"]:
            html += '<h3 style="color:#ff8b00;">缺少Assignee</h3><p>以下未合并CL的Jira没有Assignee</p><ul>'
            for cl in risks["missing_assignee"]:
                status = cl_status.get((cl["project"], cl["change_id"]), "Unknown")
                html += (
                    f'<li><a href="{JIRA_WEB_URL}/browse/{cl["jira"]}">'
                    f'{cl["jira"]}</a> - CL {cl["change_id"]} - {_status_badge(status)}</li>'
                )
            html += '</ul>'

    # Unmerged CLs table
    if unmerged:
        html += (
            '<h2 style="color:#de350b;">未合并的CL</h2>'
            '<p>以下CL尚未合并到主分支</p>'
            '<table style="width:100%;border-collapse:collapse;">'
            '<tr style="background:#f4f5f7;font-weight:bold;">'
            '<td style="padding:8px;border:1px solid #dfe1e6;">CL ID</td>'
            '<td style="padding:8px;border:1px solid #dfe1e6;">项目</td>'
            '<td style="padding:8px;border:1px solid #dfe1e6;">关联Jira</td>'
            '<td style="padding:8px;border:1px solid #dfe1e6;">状态</td>'
            '</tr>'
        )
        for cl in unmerged:
            status = cl_status.get((cl["project"], cl["change_id"]), "Unknown")
            html += (
                f'<tr>'
                f'<td style="padding:8px;border:1px solid #dfe1e6;">{cl["change_id"]}</td>'
                f'<td style="padding:8px;border:1px solid #dfe1e6;">{_escape_html(cl["project"])}</td>'
                f'<td style="padding:8px;border:1px solid #dfe1e6;">'
                f'<a href="{JIRA_WEB_URL}/browse/{cl["jira"]}">{cl["jira"]}</a></td>'
                f'<td style="padding:8px;border:1px solid #dfe1e6;">{_status_badge(status)}</td>'
                f'</tr>'
            )
        html += '</table>'
    else:
        html += (
            '<h2 style="color:#36b37e;">所有CL已合并</h2>'
            '<p>所有关联的CL均已合并到主分支</p>'
        )

    # All CLs summary table
    html += (
        '<h2>所有CL状态汇总</h2>'
        '<table style="width:100%;border-collapse:collapse;">'
        '<tr style="background:#f4f5f7;font-weight:bold;">'
        '<td style="padding:8px;border:1px solid #dfe1e6;">CL ID</td>'
        '<td style="padding:8px;border:1px solid #dfe1e6;">项目</td>'
        '<td style="padding:8px;border:1px solid #dfe1e6;">关联Jira</td>'
        '<td style="padding:8px;border:1px solid #dfe1e6;">状态</td>'
        '</tr>'
    )
    for cl in cls:
        status = cl_status.get((cl["project"], cl["change_id"]), "Unknown")
        html += (
            f'<tr>'
            f'<td style="padding:8px;border:1px solid #dfe1e6;">{cl["change_id"]}</td>'
            f'<td style="padding:8px;border:1px solid #dfe1e6;">{_escape_html(cl["project"])}</td>'
            f'<td style="padding:8px;border:1px solid #dfe1e6;">'
            f'<a href="{JIRA_WEB_URL}/browse/{cl["jira"]}">{cl["jira"]}</a></td>'
            f'<td style="padding:8px;border:1px solid #dfe1e6;">{_status_badge(status)}</td>'
            f'</tr>'
        )
    html += '</table>'

    # Footer
    html += (
        '<hr style="border:none;border-top:1px solid #dfe1e6;margin:30px 0;"/>'
        '<p style="color:#5e6c84;font-size:12px;text-align:center;">'
        '<em>本报告由 Project Agent 自动生成</em></p>'
    )

    return html


async def _publish_to_confluence(html: str, config: Dict, timestamp: str) -> Dict:
    """Publish report to Confluence."""
    conf = mcp_manager.get_client("confluence")
    if not conf:
        raise RuntimeError("Confluence MCP client not available")

    title = config["confluence"]["title_template"].format(timestamp=timestamp)
    space_key = config["confluence"]["space_key"]
    parent_id = config["confluence"]["parent_id"]

    result = await conf.call_tool(
        "create_page",
        {
            "title": title,
            "space_key": space_key,
            "content": html,
            "parent_id": parent_id,
        },
    )

    if isinstance(result, dict) and "id" in result:
        page_id = result["id"]
        return {
            "success": True,
            "page_id": page_id,
            "url": f"{CONFLUENCE_BASE_URL}/pages/viewpage.action?pageId={page_id}",
            "title": title,
        }
    else:
        return {
            "success": False,
            "error": str(result),
            "title": title,
        }


async def jira_cl_merge_monitor_handler(params: Dict, context: Dict) -> Dict:
    """
    Handler for jira-cl-merge-monitor skill.

    Args:
        params: Skill parameters from planner
        context: Execution context

    Returns:
        Dict with report summary and Confluence page info
    """
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    config_name = params.get("config_name", "default")

    if "label" in params:
        config["jira"]["label"] = params["label"]
    if "epics" in params:
        config["jira"]["epics"] = params["epics"]
    if "space_key" in params:
        config["confluence"]["space_key"] = params["space_key"]
    if "parent_id" in params:
        config["confluence"]["parent_id"] = params["parent_id"]

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    try:
        # Step 1: Collect Verified Jiras
        jira_details = await _collect_verified_jiras(config)

        # Step 2: Extract CLs from comments
        cls = await _extract_cls(jira_details, config)

        # Step 3: Query CL status
        cl_status = await _query_cl_status(cls)

        # Step 4: Analyze risks
        risks = _analyze_risks(cls, cl_status, jira_details)

        # Step 5: Generate HTML
        html = _build_report_html(
            jira_details, cls, cl_status, risks, config, timestamp
        )

        # Step 6: Publish to Confluence
        publish_result = await _publish_to_confluence(html, config, timestamp)

        unmerged = [
            cl for cl in cls
            if cl_status.get((cl["project"], cl["change_id"])) not in ["MERGED", "Merged"]
        ]
        merged = [
            cl for cl in cls
            if cl_status.get((cl["project"], cl["change_id"])) in ["MERGED", "Merged"]
        ]

        return {
            "type": "report",
            "title": f"CL Merge Monitor Report - {timestamp}",
            "data": {
                "verified_jiras": len(jira_details),
                "total_cls": len(cls),
                "merged": len(merged),
                "unmerged": len(unmerged),
                "risks": {
                    k: len(v) for k, v in risks.items() if isinstance(v, list)
                },
            },
            "confluence": publish_result,
            "actions": [
                {
                    "label": "查看Confluence报告",
                    "url": publish_result.get("url", "#"),
                }
            ],
        }

    except Exception as e:
        return {
            "type": "error",
            "title": "CL Merge Monitor 报告生成失败",
            "error": str(e),
            "data": {},
        }
