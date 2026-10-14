"""Context Spill 暂存服务 — Agent 上下文溢出数据的独立临时存储。

背景（2026-09-08）：MCP 的 get_issue_comments 等工具**不支持分页**，一次全量返回大数据
（如 57 条评论），超过单次展示预算时不能静默丢弃（LLM 会漏掉关键信息），也不能塞进
memory 系统（会二次撑爆 context / 污染 FTS 检索 / 无 TTL）。

本模块提供独立的临时文件暂存区（backend/data/context_cache/{user_id}/{spill_id}.json），
语义与 agent 的四层记忆系统**完全隔离**：
  - 不进入 FTS5 索引、不被 Auto-RAG / daily_logger / system prompt 注入
  - 会话级 TTL（默认 24h），由后台循环定期清扫
  - 按 user_id 分目录，天然多用户隔离
  - 支持**有界取回**（offset/limit/query），等效为不分页的 MCP 补上"代理侧分页"，
    杜绝整读再次撑爆 context

数据形态：spill 面向**结构化列表**（评论/表格行/issue 列表等可切片数据）。
save_spill 会自动从 data 中提取列表字段，读回时按条切片 + 可选关键词过滤。
"""

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 目录名常量：与 daily_logger 的 daily_logs 平级
SPILL_DIR_NAME = "context_cache"
SPILL_DEFAULT_TTL_HOURS = 24  # 默认 TTL；后台清扫按文件记录 expires_at + mtime 兜底
_FILE_MODE = 0o600  # 内容可能含内网数据，仅本人可读写
# 提取列表字段的候选键（save 时从 dict data 中识别可切片的列表）
_LIST_KEYS = ("comments", "matches", "cells", "tables", "issues", "items", "all", "rows", "changes")


def _default_spill_dir() -> str:
    """默认 spill 根目录（backend/data/context_cache）。"""
    return str(Path(__file__).resolve().parent.parent.parent.parent / "data" / SPILL_DIR_NAME)


def _spill_path(spill_root: str, user_id: int, spill_id: str) -> Path:
    """单个 spill 文件路径。"""
    return Path(spill_root) / str(user_id) / f"{spill_id}.json"


def _extract_entries(data: Any) -> tuple:
    """从 data 中提取可切片的列表（entries）与其余元信息（meta）。

    Args:
        data: 原始工具结果（dict 含列表字段 / 纯 list / 其它）

    Returns:
        (entries, meta): entries 为可切片列表（无则空列表），meta 为列表之外的结构。
    """
    if isinstance(data, list):
        return data, {}
    if isinstance(data, dict):
        for key in _LIST_KEYS:
            value = data.get(key)
            if isinstance(value, list):
                meta = {k: v for k, v in data.items() if k != key}
                return value, meta
        # dict 无列表字段 → 单条语义，包装为单元素列表，保留结构
        return [data], {}
    return [], {"payload": data}


def _entry_text(entry: Any) -> str:
    """条目字符串化，供关键词过滤。"""
    if isinstance(entry, dict):
        return json.dumps(entry, ensure_ascii=False, default=str)
    return str(entry)


def _sync_save(
    spill_root: str,
    user_id: int,
    source_tool: str,
    source_args: Dict[str, Any],
    data: Any,
    kind: str,
    summary: Optional[str],
    ttl_hours: int,
) -> str:
    """同步写单个 spill 文件（在 asyncio.to_thread 中调用）。

    Returns:
        spill_id

    Raises:
        OSError: 目录/文件写入失败时。
    """
    spill_id = uuid.uuid4().hex[:16]
    now = datetime.now()
    entries, meta = _extract_entries(data)
    doc: Dict[str, Any] = {
        "spill_id": spill_id,
        "user_id": user_id,
        "kind": kind,
        "created_at": now.isoformat(timespec="seconds"),
        "ttl_hours": ttl_hours,
        "expires_at": (now + timedelta(hours=ttl_hours)).isoformat(timespec="seconds"),
        "source_tool": source_tool,
        "source_args": source_args or {},
        "total_entries": len(entries),
        "meta": meta,
        "summary": summary,
        "entries": entries,
    }
    user_dir = Path(spill_root) / str(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    target = user_dir / f"{spill_id}.json"
    # 原子写：临时文件 + os.replace，避免半写文件被读
    tmp = user_dir / f".{spill_id}.{uuid.uuid4().hex[:6]}.tmp"
    try:
        tmp.write_text(json.dumps(doc, ensure_ascii=False, default=str), encoding="utf-8")
        os.chmod(tmp, _FILE_MODE)
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    logger.debug("spill saved: %s kind=%s entries=%d ttl=%dh", spill_id, kind, len(entries), ttl_hours)
    return spill_id


async def save_spill(
    user_id: int,
    source_tool: str,
    source_args: Optional[Dict[str, Any]] = None,
    data: Any = None,
    kind: str = "generic",
    summary: Optional[str] = None,
    ttl_hours: int = SPILL_DEFAULT_TTL_HOURS,
    spill_root: Optional[str] = None,
) -> str:
    """保存一份溢出的原始数据到临时暂存区。

    Args:
        user_id: 用户 ID（目录隔离）。
        source_tool: 数据来源工具（如 call_mcp_tool/jira/get_issue_comments）。
        source_args: 来源工具的调用参数（便于追溯）。
        data: 原始结果。dict 含列表字段（comments/matches/...）时自动提取为切片列表；
            纯 list 直接作为切片列表；其它作为单条。
        kind: 数据类别（"comments"/"generic" 等）。
        summary: 可选的 LLM 压缩摘要（若调用方已生成）。
        ttl_hours: 存活小时数，默认 24。
        spill_root: 测试注入用；None 用默认 backend/data/context_cache。

    Returns:
        spill_id

    Raises:
        ValueError: user_id 缺失时。
    """
    if not user_id:
        raise ValueError("save_spill 需要 user_id（多用户隔离）")
    root = spill_root or _default_spill_dir()
    try:
        return await asyncio.to_thread(
            _sync_save, root, user_id, source_tool, source_args or {},
            data, kind, summary, ttl_hours,
        )
    except OSError as e:
        logger.error("save_spill 失败 (user=%s tool=%s): %s", user_id, source_tool, e)
        raise


def _sync_load(spill_root: str, user_id: int, spill_id: str) -> Optional[Dict[str, Any]]:
    """同步读取单个 spill 文件并校验归属/过期。"""
    path = _spill_path(spill_root, user_id, spill_id)
    if not path.exists():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("spill 读取失败 %s: %s", path, e)
        return None
    # 归属校验
    if doc.get("user_id") != user_id or doc.get("spill_id") != spill_id:
        logger.warning("spill 归属校验失败 user=%s spill=%s", user_id, spill_id)
        return None
    # 过期校验（读取时兜底拦截，正常由后台清扫）
    expires = doc.get("expires_at")
    if expires:
        try:
            if datetime.fromisoformat(expires) < datetime.now():
                return None
        except ValueError:
            pass
    return doc


async def read_spill_slice(
    user_id: int,
    spill_id: str,
    offset: int = 0,
    limit: int = 30,
    query: Optional[str] = None,
    spill_root: Optional[str] = None,
) -> Dict[str, Any]:
    """有界取回 spill 数据的切片（**只允许切片，杜绝整读再撑爆 context**）。

    Args:
        user_id: 用户 ID。
        spill_id: save_spill 返回的标识。
        offset: 起始下标（负数视为 0）。
        limit: 最多返回条数（0 表示不限制，但调用方应传具体值）。
        query: 可选关键词，逐条做大小写不敏感子串过滤后再切片。
        spill_root: 测试注入用。

    Returns:
        {
          "success": bool,
          "spill_id": ..., "kind": ..., "source_tool": ...,
          "offset": ..., "limit": ...,
          "total": 命中条数, "returned": 实际条数,
          "total_all": 未过滤总条数,
          "entries": [...], "meta": {...},
          "error": 未找到/越权时给中文提示
        }
    """
    if not user_id:
        return {"success": False, "error": "缺少用户身份信息"}
    root = spill_root or _default_spill_dir()
    doc = await asyncio.to_thread(_sync_load, root, user_id, spill_id)
    if doc is None:
        return {
            "success": False,
            "spill_id": spill_id,
            "error": f"暂存数据 {spill_id} 不存在、已过期或不属于当前用户",
        }

    entries = doc.get("entries", []) or []
    offset = max(int(offset or 0), 0)
    limit = max(int(limit or 30), 0)

    if query:
        needle = str(query).lower()
        filtered = [e for e in entries if needle in _entry_text(e).lower()]
    else:
        filtered = entries

    total_all = len(entries)
    total_hit = len(filtered)
    shown = filtered[offset:offset + limit] if limit else filtered[offset:]
    if isinstance(shown, list):
        shown = shown[:limit] if limit else shown

    return {
        "success": True,
        "spill_id": spill_id,
        "kind": doc.get("kind", "generic"),
        "source_tool": doc.get("source_tool", ""),
        "offset": offset,
        "limit": limit,
        "total": total_hit,
        "total_all": total_all,
        "returned": len(shown),
        "meta": doc.get("meta", {}),
        "summary": doc.get("summary"),
        "entries": shown,
    }


async def delete_spill(
    user_id: int, spill_id: str, spill_root: Optional[str] = None
) -> bool:
    """删除单个 spill 文件。"""
    root = spill_root or _default_spill_dir()
    path = _spill_path(root, user_id, spill_id)
    try:
        if path.exists():
            await asyncio.to_thread(path.unlink)
            return True
    except OSError as e:
        logger.warning("delete_spill 失败 %s: %s", path, e)
    return False


def _sync_cleanup_expired(spill_root: str) -> int:
    """清扫过期 spill 文件，返回删除数量。

    判断：文件记录 expires_at 已过期即删；若记录缺失（异常文件），按 mtime 超过
    SPILL_DEFAULT_TTL_HOURS 兜底删。同时清理孤儿 .tmp 残留。
    """
    root = Path(spill_root)
    if not root.exists():
        return 0
    removed = 0
    now = datetime.now()
    for user_dir in root.iterdir():
        if not user_dir.is_dir():
            continue
        for path in user_dir.iterdir():
            name = path.name
            try:
                if name.endswith(".tmp"):
                    # 残留临时文件（超过 1h 视为写入中断残留）
                    if now - datetime.fromtimestamp(path.stat().st_mtime) > timedelta(hours=1):
                        path.unlink()
                        removed += 1
                    continue
                if not name.endswith(".json"):
                    continue
                expired = False
                try:
                    doc = json.loads(path.read_text(encoding="utf-8"))
                    exp = doc.get("expires_at")
                    if exp:
                        expired = datetime.fromisoformat(exp) < now
                    else:
                        # 记录缺失：按 mtime + TTL 兜底
                        mtime = datetime.fromtimestamp(path.stat().st_mtime)
                        expired = now - mtime > timedelta(hours=SPILL_DEFAULT_TTL_HOURS)
                except (OSError, json.JSONDecodeError, ValueError):
                    # 损坏/无法解析文件：按 mtime 兜底清理
                    mtime = datetime.fromtimestamp(path.stat().st_mtime)
                    expired = now - mtime > timedelta(hours=SPILL_DEFAULT_TTL_HOURS)
                if expired:
                    path.unlink()
                    removed += 1
            except OSError as e:
                logger.warning("spill cleanup 跳过 %s: %s", path, e)
    if removed:
        logger.info("context spill cleanup removed %d files", removed)
    return removed


async def cleanup_expired_spills(spill_root: Optional[str] = None) -> int:
    """清理所有用户的过期 spill 文件（后台循环调用）。"""
    root = spill_root or _default_spill_dir()
    return await asyncio.to_thread(_sync_cleanup_expired, root)
