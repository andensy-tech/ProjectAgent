"""文件回传工具 — 把服务器上**已有**的文件发给用户（Web Chat 下载卡片）。

背景（2026-09-15）：Agent 产出文件（`write_*` 工具写出、技能产物）由引擎自动回传为
下载卡片；但当用户说"把 XXX 文件发给我 / 我要下载 XXX"时，文件是**已存在**的、本轮
没有任何工具产出它，LLM 无工具可调，只能回答"我无法发送附件"。

本工具补上这条路径：按路径或文件名定位文件 → 返回 `files[]` → 引擎渲染下载卡片
（复用 `services/file_outputs.py` 的提取逻辑，**不新增 meta-tool**）。

设计要点：
  - 只给文件名时在常见位置查找（项目根、技能目录、/tmp 等），查找有深度/数量上限，
    避免在请求路径上做全盘扫描；
  - 同名多份 → 按修改时间新的优先，最多返回 `max_files` 个；
  - 找不到时返回明确的中文错误 + 已搜索位置，让 LLM 如实告知用户。
"""

import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 按文件名查找时的搜索根目录（项目根已覆盖 backend/、skills/、data/、temp/ 等）
_MAX_DEPTH = 5              # 目录递归深度上限
_MAX_SCAN_ENTRIES = 20000   # 单次扫描的条目上限（保护请求耗时）
_MAX_FILES_DEFAULT = 3      # 默认最多回传几个文件
_MAX_FILES_LIMIT = 10       # 硬上限

# 扫描时跳过的目录（依赖/版本库/缓存，体积大且与用户文件无关）
_SKIP_DIRS = {
    ".git", "node_modules", "venv", ".venv", "__pycache__", ".claude",
    "site-packages", ".idea", ".vscode", "dist", ".mypy_cache", ".pytest_cache",
}


def _primary_roots() -> List[str]:
    """一级搜索根（快）：Agent 生成文件的常见落点。"""
    roots: List[str] = []
    # /tmp：Agent 生成文件最常落地的位置
    roots.append("/tmp")
    # 进程工作目录（backend/）
    roots.append(os.getcwd())
    # 项目根（backend/app/tools → 上三级）
    try:
        roots.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
    except OSError:
        pass
    return [r for r in roots if r and os.path.isdir(r)]


def _fallback_roots() -> List[str]:
    """二级搜索根（慢，仅一级未命中时扫）：用户主目录（导出到 ~/Downloads 等）。"""
    home = os.path.expanduser("~")
    return [home] if home and os.path.isdir(home) else []


def _search_roots() -> List[str]:
    """全部搜索根（去重）——用于错误提示中如实告知用户搜索范围。"""
    out: List[str] = []
    for root in _primary_roots() + _fallback_roots():
        if root not in out:
            out.append(root)
    return out


def _iter_files(root: str):
    """有界遍历 root 下的文件（跳过依赖/缓存目录，限制深度与条目数）。"""
    root_depth = root.rstrip(os.sep).count(os.sep)
    scanned = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        if dirpath.rstrip(os.sep).count(os.sep) - root_depth >= _MAX_DEPTH:
            dirnames[:] = []
        for name in filenames:
            scanned += 1
            if scanned > _MAX_SCAN_ENTRIES:
                logger.warning("deliver_file: scan budget exhausted under %s", root)
                return
            yield os.path.join(dirpath, name)


def _scan_for_name(roots: List[str], target: str) -> tuple:
    """在给定根目录下查找文件名，返回 (精确同名, 模糊包含)。"""
    exact: List[str] = []
    partial: List[str] = []
    seen: set = set()

    for root in roots:
        for path in _iter_files(root):
            real = os.path.realpath(path)
            if real in seen:
                continue
            seen.add(real)
            base = os.path.basename(real)
            if base.lower() == target:
                exact.append(real)
            elif target in base.lower():
                partial.append(real)

    return exact, partial


def _find_by_name(file_name: str) -> List[str]:
    """按文件名查找（先精确同名，找不到再模糊包含），按修改时间新的优先。

    分两级：先扫快的一级根（/tmp、项目目录），未命中再扫用户主目录——避免每次
    都做上万个文件的全盘遍历。
    同名多份（多处副本）只保留最新的一个：用户说"把文件 X 发给我"要的是一份，
    发多张同名卡片反而让人分不清。
    """
    target = file_name.strip().lower()
    exact, partial = _scan_for_name(_primary_roots(), target)
    if not exact and not partial:
        exact, partial = _scan_for_name(_fallback_roots(), target)

    def _mtime(path: str) -> float:
        try:
            return os.path.getmtime(path)
        except OSError:
            return 0.0

    ordered = sorted(exact or partial, key=_mtime, reverse=True)

    deduped: List[str] = []
    seen_names: set = set()
    for path in ordered:
        base = os.path.basename(path).lower()
        if base in seen_names:
            continue
        seen_names.add(base)
        deduped.append(path)
    return deduped


def _resolve_path(file_path: str) -> Optional[str]:
    """把用户/LLM 给的路径解析成存在的文件；不存在返回 None。"""
    expanded = os.path.expanduser(file_path)
    if not os.path.isabs(expanded):
        expanded = os.path.abspath(expanded)
    real = os.path.realpath(expanded)
    return real if os.path.isfile(real) else None


def _describe(path: str) -> Dict[str, Any]:
    """组装单个文件的描述（引擎会据此渲染下载卡片）。"""
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0
    return {"file_path": path, "file_name": os.path.basename(path), "size": size}


async def deliver_file(params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """把服务器上已有的文件发给用户（聊天里出现下载卡片）。

    Args:
        params:
          file_path: 文件绝对/相对路径（优先使用；已知确切路径时传它）。
          file_name: 文件名（无路径时用；在项目根/技能目录/tmp 等位置按名查找，
                     精确同名优先，找不到再模糊匹配，按修改时间新的优先）。
          max_files: 最多回传几个文件（默认 3，上限 10）。
        context: 执行上下文（本工具不依赖用户信息，回传链接由引擎按用户签发）。

    Returns:
        {"success": True, "message": ..., "files": [{file_path, file_name, size}]}
        或 {"success": False, "error": 中文提示}
    """
    params = params or {}
    raw_path = str(params.get("file_path") or "").strip()
    raw_name = str(params.get("file_name") or "").strip()
    if not raw_path and not raw_name:
        return {
            "success": False,
            "error": "缺少 file_path 或 file_name —— 请提供要发送的文件路径或文件名",
        }

    try:
        max_files = int(params.get("max_files") or _MAX_FILES_DEFAULT)
    except (TypeError, ValueError):
        max_files = _MAX_FILES_DEFAULT
    max_files = max(1, min(max_files, _MAX_FILES_LIMIT))

    matches: List[str] = []
    try:
        if raw_path:
            resolved = _resolve_path(raw_path)
            if resolved:
                matches = [resolved]
            elif not raw_name:
                return {
                    "success": False,
                    "error": f"文件不存在: {raw_path}（请确认路径，或用 file_name 让工具按名查找）",
                }
        if not matches and raw_name:
            matches = _find_by_name(raw_name)[:max_files]
    except (OSError, ValueError) as e:  # 非法路径（NUL 字节等）/ 文件系统异常
        logger.warning("deliver_file failed for path=%r name=%r: %s", raw_path, raw_name, e)
        return {"success": False, "error": f"查找文件失败: {str(e)[:120]}"}

    if not matches:
        searched = "、".join(_search_roots())
        return {
            "success": False,
            "error": (
                f"未找到文件「{raw_path or raw_name}」。已搜索位置: {searched}。"
                f"请确认文件名，或提供完整路径后重试。"
            ),
        }

    files = [_describe(p) for p in matches]
    return {
        "success": True,
        "message": (
            f"已把 {len(files)} 个文件发到聊天里（用户可直接点击下载卡片，"
            f"不要再让用户自己去服务器找路径，也不要回答'无法发送附件'）: "
            + "、".join(f["file_name"] for f in files)
        ),
        "files": files,
    }
