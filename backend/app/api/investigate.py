"""
Investigate API — one-click AI deep dive from dashboard metrics to chat.

POST /api/investigate
  Accept dashboard context (metric, project, etc.), creates a conversation,
  runs agent analysis synchronously, and returns the conversation_id for
  the frontend to navigate to.
"""

import logging
from typing import Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_active_user
from app.db import get_db
from app.crud.conversation import create_conversation, create_message
from app.services.agent import agent_engine

router = APIRouter(prefix="/api", tags=["investigate"])
logger = logging.getLogger(__name__)


class InvestigateRequest(BaseModel):
    context_type: str = "metric_card"  # metric_card | project_issues | alert | cl_status
    context_data: Dict[str, Any] = {}
    message: str = ""


def _format_prompt(context_type: str, data: Dict[str, Any]) -> str:
    """Build an investigation prompt from dashboard context."""
    if context_type == "metric_card":
        metric = data.get("metric", "指标")
        value = data.get("value", 0)
        project = data.get("project", "项目")
        return (
            f"请分析仪表盘指标：项目「{project}」的「{metric}」当前值为 {value}。"
            f"请调查可能的原因，评估影响，并给出改进建议。"
            f"如果需要更多数据，请使用可用的 MCP 工具查询。"
        )

    if context_type == "project_issues":
        project = data.get("project", "未知项目")
        p1 = data.get("p1", 0)
        stale = data.get("stale", 0)
        eta = data.get("eta_expiring", 0)
        cl_unmerged = data.get("cl_unmerged", 0)
        return (
            f"请分析项目「{project}」的整体状况：\n"
            f"- P1 问题: {p1} 个\n"
            f"- 超过 7 天未更新: {stale} 个\n"
            f"- ETA 到期: {eta} 个\n"
            f"- 未合并 CL: {cl_unmerged} 个\n\n"
            f"请分析这些指标之间是否存在关联，识别主要风险点，并给出处理建议。"
        )

    if context_type == "issue":
        issue_key = data.get("issue_key", "")
        summary = data.get("summary", "")
        status = data.get("status", "")
        priority = data.get("priority", "")
        assignee = data.get("assignee", "")
        return (
            f"请分析 Jira 问题 {issue_key}：\n"
            f"- 概要: {summary}\n"
            f"- 状态: {status}\n"
            f"- 优先级: {priority}\n"
            f"- 经办人: {assignee}\n\n"
            f"请查询该问题的详细信息、评论和关联 CL 状态，评估是否存在风险。"
        )

    if context_type == "cl_status":
        project = data.get("project", "未知项目")
        unmerged = data.get("unmerged", 0)
        total = data.get("total", 0)
        return (
            f"请分析项目「{project}」的 CL 状态：共 {total} 个 CL，"
            f"其中 {unmerged} 个未合并。请逐个检查未合并 CL 的当前状态、"
            f"关联的 Jira 问题，分析阻塞原因并给出建议。"
        )

    if context_type == "alert":
        title = data.get("title", "")
        description = data.get("description", "")
        project = data.get("project", "")
        return (
            f"请分析以下告警：\n"
            f"- 项目: {project}\n"
            f"- 标题: {title}\n"
            f"- 描述: {description}\n\n"
            f"请深入调查此告警的根本原因，评估影响范围，并给出具体的解决建议。"
        )

    # Fallback
    return data.get("message", f"请分析仪表盘数据: {data}")


@router.post("/investigate")
async def start_investigation(
    body: InvestigateRequest,
    current_user: dict = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Start an AI investigation from a dashboard metric or context."""
    prompt = body.message or _format_prompt(body.context_type, body.context_data)
    title = prompt[:100]

    # 1. Create conversation under the current user
    conv = await create_conversation(db, user_id=current_user.get("user_id"), title=title)

    # 2. Save the investigation request as a user message
    await create_message(
        db,
        conversation_id=conv.id,
        role="user",
        content=prompt,
        message_type="text",
    )

    # 3. Run agent synchronously and collect text/card responses
    user_id = current_user.get("user_id", "dev_user")
    text_responses: list[str] = []
    error_response: Optional[str] = None

    try:
        async for response in agent_engine.process_message(
            message=prompt,
            conversation_id=str(conv.id),
            user_id=user_id,
            db=db,
        ):
            if response.type in ("text", "card"):
                content = response.content or ""
                text_responses.append(content)
                if content:
                    await create_message(
                        db,
                        conversation_id=conv.id,
                        role="assistant",
                        content=content,
                        message_type=response.type,
                        extra_data=response.data or {},
                    )
            elif response.type == "error":
                error_response = response.content
                break
    except Exception as e:
        logger.error("Investigation failed: %s", e)
        error_response = str(e)

    return {
        "conversation_id": conv.id,
        "title": title,
        "has_error": error_response is not None,
        "error": error_response,
    }
