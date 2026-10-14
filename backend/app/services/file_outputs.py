"""Agent 产出文件提取与下载描述（Web Chat 文件回传）。

Agent 执行技能/内置工具后，生成的文件落在服务器磁盘上（技能目录、/tmp 或用户
指定的绝对路径）。Web Chat 需要把这些文件回传给用户，但：

  - 文件路径是服务端路径，直接暴露给前端既不可读也不安全；
  - 前端 <a href> 无法携带 JWT，无法直接访问需鉴权的下载接口。

因此本模块是"文件回传"的单一事实来源：

  1. extract_file_outputs()  —— 从任意技能/工具结果中识别"生成的文件"；
  2. describe_file_outputs() —— 组装前端可直接渲染的下载描述（含签名下载链接）。

被三处复用：引擎的 SSE 文件卡片（chat 路径）、/api/tasks/{id} 任务结果
（技能面板路径）、以及需要回传文件的其它接口。
"""

import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from app.core.security import create_file_token

logger = logging.getLogger(__name__)

# 技能/工具结果中表达"输出文件"的常见字段名
_FILE_PATH_KEYS: Tuple[str, ...] = ("file_path", "output_path", "output_file", "saved_path")

# 内置文档写入工具（返回 type=document + content=写入路径）
_WRITE_TOOL_PREFIX = "write_"


def _as_existing_file(path: Any) -> Optional[str]:
    """把候选值归一化为存在的文件的绝对路径，否则返回 None。

    路径来自 LLM 生成的工具参数/技能输出（不可信），含 NUL 字节、超长、非法编码
    的路径会让 os.path 抛 ValueError —— 一律当作"不是文件"，不能让它打断引擎的
    工具结果处理循环。
    """
    if not isinstance(path, str) or not path:
        return None
    try:
        expanded = os.path.expanduser(path)
        if not os.path.isabs(expanded):
            expanded = os.path.abspath(expanded)
        real = os.path.realpath(expanded)
        return real if os.path.isfile(real) else None
    except (OSError, ValueError):
        logger.debug("Ignore invalid file path from tool result: %.120r", path)
        return None


def _collect_candidates(result: Dict[str, Any]) -> List[Tuple[Any, Optional[str]]]:
    """从结果 dict 中收集 (候选路径, 技能名) —— 只认已知结构，不做深度扫描。"""
    candidates: List[Tuple[Any, Optional[str]]] = []

    # 形态一：技能结果 {"file_path": ..., "skill_name": ...}
    for key in _FILE_PATH_KEYS:
        if result.get(key):
            candidates.append((result[key], result.get("skill_name")))

    data = result.get("data")
    if isinstance(data, dict):
        # 形态二：脚本技能经 script_runner 归一化后 → data.file_path
        for key in _FILE_PATH_KEYS:
            if data.get(key):
                candidates.append((data[key], data.get("skill_name") or result.get("skill_name")))
        # 形态三：data.files = [{file_path: ...}, ...]
        candidates.extend(_collect_from_files(data.get("files"), data.get("skill_name")))
    elif isinstance(data, list):
        # 形态四：data = [{file_path: ...}, ...]
        candidates.extend(_collect_from_files(data, result.get("skill_name")))

    # 形态五：顶层 files 列表
    candidates.extend(_collect_from_files(result.get("files"), result.get("skill_name")))

    # 形态六：内置文档工具 {"type": "document", "tool": "write_xlsx", "content": <路径>}
    tool_name = result.get("tool")
    if (
        result.get("type") == "document"
        and isinstance(tool_name, str)
        and tool_name.startswith(_WRITE_TOOL_PREFIX)
    ):
        candidates.append((result.get("content"), None))

    return candidates


def _collect_from_files(value: Any, skill_name: Optional[str]) -> List[Tuple[Any, Optional[str]]]:
    """从 files 列表（元素为 str 或含路径字段的 dict）中收集候选路径。"""
    out: List[Tuple[Any, Optional[str]]] = []
    if not isinstance(value, list):
        return out
    for item in value:
        if isinstance(item, str):
            out.append((item, skill_name))
        elif isinstance(item, dict):
            for key in _FILE_PATH_KEYS:
                if item.get(key):
                    out.append((item[key], item.get("skill_name") or skill_name))
                    break
    return out


def extract_file_outputs(result: Any, skill_name: Optional[str] = None) -> List[Dict[str, Any]]:
    """从技能/工具执行结果中提取生成的文件（按路径去重）。

    Args:
        result: 技能或内置工具的返回结果（任意结构）。
        skill_name: 调用方已知的技能名（结果里没有时兜底）。

    Returns:
        描述文件列表，每项 {"file_path", "file_name", "size", "skill_name"}；
        出错结果或不含文件时返回空列表。
    """
    if not isinstance(result, dict):
        return []
    if result.get("error"):
        return []

    outputs: List[Dict[str, Any]] = []
    seen: set = set()
    for candidate, cand_skill in _collect_candidates(result):
        real_path = _as_existing_file(candidate)
        if not real_path or real_path in seen:
            continue
        seen.add(real_path)
        try:
            size = os.path.getsize(real_path)
        except OSError:
            size = 0
        outputs.append({
            "file_path": real_path,
            "file_name": os.path.basename(real_path),
            "size": size,
            "skill_name": cand_skill or skill_name or "",
        })
    return outputs


def describe_file_outputs(
    result: Any,
    skill_name: Optional[str] = None,
    user_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """提取产出文件并补上前端可用的下载链接（签名令牌，绑定用户）。

    Args:
        result: 技能或内置工具的返回结果。
        skill_name: 调用方已知的技能名。
        user_id: 当前用户，用于签发下载令牌。

    Returns:
        描述文件列表，每项比 extract_file_outputs() 多一个 download_url。
    """
    outputs = extract_file_outputs(result, skill_name=skill_name)
    if user_id is not None:
        for item in outputs:
            item["download_url"] = (
                f"/api/files/download?token={create_file_token(item['file_path'], user_id)}"
            )
    return outputs
