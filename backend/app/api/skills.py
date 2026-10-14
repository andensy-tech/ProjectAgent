"""
Skills API — List, execute, and configure Skills.
Multi-user: each user has their own skill parameter overrides.
"""

import os
import uuid
import yaml
from typing import Dict, List, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, FileResponse
from pydantic import BaseModel
from app.api.deps import get_current_active_user
from app.services.agent.executor import action_executor
from app.services.memory import memory_system
from app.skills.scanner import skill_scanner
from app.db.session import AsyncSessionLocal
from app.models.skill_config import SkillConfig
from sqlalchemy import select

router = APIRouter(prefix="/api/skills", tags=["skills"])


class SkillInfo(BaseModel):
    name: str
    display_name: str
    description: str
    version: str
    triggers: List[str]
    mcp_required: List[str] = []


class SkillExecuteRequest(BaseModel):
    params: Dict[str, Any] = {}


class SkillExecuteResponse(BaseModel):
    execution_id: str
    status: str
    result: Dict[str, Any] = {}


class SkillConfigSchema(BaseModel):
    """User's skill config override."""
    config_json: Dict[str, Any] = {}
    description: Optional[str] = None


def _build_skill_metadata() -> Dict[str, dict]:
    """Build metadata dict from scanner + registered skills."""
    metadata = {}
    for skill_def in skill_scanner.scan():
        metadata[skill_def.name] = {
            "display_name": skill_def.display_name,
            "description": skill_def.description,
            "version": skill_def.version,
            "triggers": skill_def.triggers,
            "mcp_required": skill_def.mcp_required,
        }
    return metadata


def _load_default_config(skill_name: str) -> dict:
    """Load a skill's default config.yaml from disk."""
    skill_def = skill_scanner.get_metadata().get(skill_name)
    if not skill_def:
        return {}
    config_path = os.path.join(skill_def.directory, "config.yaml")
    if not os.path.isfile(config_path):
        return {}
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


async def load_skill_config_for_user(skill_name: str, user_id: int) -> dict:
    """Load user's DB override for a skill, merged on top of default config.yaml.

    Returns the merged config dict (user values take precedence).
    """
    default = _load_default_config(skill_name)
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(SkillConfig).where(
                SkillConfig.user_id == user_id,
                SkillConfig.skill_name == skill_name,
            )
        )
        row = result.scalar_one_or_none()
    if row and row.config_json:
        merged = {**default, **row.config_json}
        return merged
    return default


# ── Endpoints ─────────────────────────────────────────────────


@router.get("", response_model=List[SkillInfo])
async def list_skills(
    current_user: dict = Depends(get_current_active_user),
):
    """List all available Skills from the real registry."""
    registered = action_executor.skill_registry
    metadata = _build_skill_metadata()
    skills = []
    for name in registered:
        meta = metadata.get(name, {})
        skills.append(SkillInfo(
            name=name,
            display_name=meta.get("display_name", name),
            description=meta.get("description", ""),
            version=meta.get("version", "0.0.0"),
            triggers=meta.get("triggers", ["chat"]),
            mcp_required=meta.get("mcp_required", []),
        ))
    return skills


@router.post("/{skill_name}/execute")
async def execute_skill(
    skill_name: str,
    request: SkillExecuteRequest,
    current_user: dict = Depends(get_current_active_user),
):
    """Execute a Skill through the Agent engine."""
    if skill_name not in action_executor.skill_registry:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")

    execution_id = f"exec-{uuid.uuid4().hex[:12]}"

    try:
        context = {}
        user_id = current_user.get("user_id")
        if user_id is not None:
            context["_user_id"] = user_id

        result = await action_executor._execute_skill(skill_name, request.params, context)

        if isinstance(result, dict) and result.get("success") is False:
            return SkillExecuteResponse(
                execution_id=execution_id,
                status="failed",
                result=result,
            )

        return SkillExecuteResponse(
            execution_id=execution_id,
            status="completed",
            result=result if isinstance(result, dict) else {"message": str(result)},
        )

    except Exception as e:
        return SkillExecuteResponse(
            execution_id=execution_id,
            status="failed",
            result={"error": str(e)},
        )


@router.get("/{skill_name}/download")
async def download_skill_file(
    skill_name: str,
    file: str,
    current_user: dict = Depends(get_current_active_user),
):
    """Download a file generated by a skill.

    The `file` parameter is the relative path from the skill's directory
    (e.g. 'weekly_report_2026_07_22.html').
    Only files within the skill's directory are accessible.
    """
    # Resolve skill directory
    skills_dir = os.path.join(os.path.dirname(__file__), "..", "..", "..", "skills")
    skill_dir = os.path.normpath(os.path.join(skills_dir, skill_name))

    if not os.path.isdir(skill_dir):
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")

    # Sanitize: only allow files within the skill directory
    requested_path = os.path.normpath(os.path.join(skill_dir, file))
    if not requested_path.startswith(skill_dir + os.sep):
        raise HTTPException(status_code=403, detail="Access denied: file outside skill directory")

    if not os.path.isfile(requested_path):
        raise HTTPException(status_code=404, detail=f"File not found: {file}")

    return FileResponse(
        requested_path,
        filename=os.path.basename(file),
        media_type="application/octet-stream",
    )


@router.get("/{skill_name}/config")
async def get_skill_config(
    skill_name: str,
    current_user: dict = Depends(get_current_active_user),
):
    """Get merged skill config: default config.yaml + user's DB overrides."""
    if skill_name not in action_executor.skill_registry:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")

    default = _load_default_config(skill_name)
    user_id = current_user.get("user_id")

    user_override = {}
    if user_id:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(SkillConfig).where(
                    SkillConfig.user_id == user_id,
                    SkillConfig.skill_name == skill_name,
                )
            )
            row = result.scalar_one_or_none()
            if row:
                user_override = row.config_json or {}

    return {
        "skill_name": skill_name,
        "default_config": default,
        "user_override": user_override,
        "merged_config": {**default, **user_override},
    }


@router.put("/{skill_name}/config")
async def update_skill_config(
    skill_name: str,
    config: SkillConfigSchema,
    current_user: dict = Depends(get_current_active_user),
):
    """Save or update user's skill config override."""
    if skill_name not in action_executor.skill_registry:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")

    user_id = current_user.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="未登录")

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(SkillConfig).where(
                SkillConfig.user_id == user_id,
                SkillConfig.skill_name == skill_name,
            )
        )
        row = result.scalar_one_or_none()
        if row:
            row.config_json = config.config_json
            if config.description is not None:
                row.description = config.description
        else:
            row = SkillConfig(
                user_id=user_id,
                skill_name=skill_name,
                config_json=config.config_json,
                description=config.description,
            )
            db.add(row)
        await db.commit()
        await db.refresh(row)

    return {
        "skill_name": skill_name,
        "config_json": row.config_json,
        "description": row.description,
    }


@router.delete("/{skill_name}/config")
async def delete_skill_config(
    skill_name: str,
    current_user: dict = Depends(get_current_active_user),
):
    """Remove user's skill config override (revert to defaults)."""
    if skill_name not in action_executor.skill_registry:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")

    user_id = current_user.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="未登录")

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(SkillConfig).where(
                SkillConfig.user_id == user_id,
                SkillConfig.skill_name == skill_name,
            )
        )
        row = result.scalar_one_or_none()
        if row:
            await db.delete(row)
            await db.commit()

    return {"detail": "配置已重置为默认值"}


@router.get("/{skill_name}/config-file")
async def get_skill_config_file(
    skill_name: str,
    current_user: dict = Depends(get_current_active_user),
):
    """获取技能配置文件的 YAML 原文（config_{userid}.yaml）。

    如果用户尚未保存过配置，返回默认 config.yaml 作为模板。
    """
    skill_def = skill_scanner.get_metadata().get(skill_name)
    if not skill_def:
        raise HTTPException(status_code=404, detail="Skill 不存在")

    user_id = current_user.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="未登录")

    user_path = os.path.join(skill_def.directory, f"config_{user_id}.yaml")
    if os.path.exists(user_path):
        with open(user_path, "r", encoding="utf-8") as f:
            return Response(content=f.read(), media_type="text/plain")

    # 返回默认模板
    default_path = os.path.join(skill_def.directory, "config.yaml")
    if os.path.exists(default_path):
        with open(default_path, "r", encoding="utf-8") as f:
            return Response(content=f.read(), media_type="text/plain")

    return Response(content="# 无默认配置", media_type="text/plain")


@router.put("/{skill_name}/config-file")
async def update_skill_config_file(
    skill_name: str,
    request: Request,
    current_user: dict = Depends(get_current_active_user),
):
    """保存技能配置文件的 YAML 原文到 config_{userid}.yaml。

    请求体为原始 YAML 字符串，保存前做格式校验。
    """
    skill_def = skill_scanner.get_metadata().get(skill_name)
    if not skill_def:
        raise HTTPException(status_code=404, detail="Skill 不存在")

    user_id = current_user.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="未登录")

    body = await request.body()
    yaml_text = body.decode("utf-8")

    # 校验 YAML 格式
    try:
        yaml.safe_load(yaml_text)
    except yaml.YAMLError as e:
        raise HTTPException(
            status_code=400,
            detail=f"YAML 格式错误：{str(e)}",
        )

    user_path = os.path.join(skill_def.directory, f"config_{user_id}.yaml")
    with open(user_path, "w", encoding="utf-8") as f:
        f.write(yaml_text)

    return Response(content=yaml_text, media_type="text/plain")
