"""CI 构建下载 + FTPS 上传管线编排。

查询/解析/下载/上传均为同步阻塞 IO，统一经 asyncio.to_thread 放入线程
执行，不阻塞事件循环。结果字典刻意使用 ``local_file``（而非 file_path）
作为本地路径键，避免后台任务结果被 file_outputs 识别为可回传文件
（构建产物数 GB，交付渠道是 FTP，不经 Web 下载卡片）。
"""

import asyncio
import logging
import os
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from .auth import AuthRequiredError
from .config import CiBuildConfig, storage_dir
from .google_api import get_latest_good_build
from .artifact_resolver import resolve_artifacts
from .fetcher import ArtifactFetcher
from .uploader import FTUploader

logger = logging.getLogger(__name__)

ProgressCb = Callable[[str], None]

BUILD_TYPES = ("user", "userdebug")


class CiBuildError(Exception):
    """管线执行失败（消息面向用户，用中文）。"""


def _format_build_date(creation_timestamp: str, build_id: str) -> str:
    """由构建创建时间戳推导 FTP 归档子目录 ``YYYY-MM-DD-{build_id}``。

    v4 API 返回 ISO-8601 字符串（如 "2026-09-24T19:29:58.633Z"），v3 为毫秒。
    解析失败时回退当前日期（与 BuildAutoFetch 行为一致）。
    """
    if creation_timestamp:
        dt: Optional[datetime] = None
        try:
            dt = datetime.fromisoformat(creation_timestamp.replace("Z", "+00:00"))
        except ValueError:
            try:
                dt = datetime.fromtimestamp(int(creation_timestamp) / 1000)
            except (ValueError, OSError, OverflowError):
                dt = None
        if dt is not None:
            return f"{dt.strftime('%Y-%m-%d')}-{build_id}"
    return f"{datetime.now().strftime('%Y-%m-%d')}-{build_id}"


def _noop_progress(_msg: str) -> None:
    return None


def _human_size(num_bytes: int) -> str:
    """字节数 → 可读字符串（如 1.2 GB）。"""
    size = float(num_bytes or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def _cleanup_uploaded_file(local_path: str) -> bool:
    """上传成功后删除本地副本（下载目录仅作上传 FTP 的中转缓存，本地不留数据）。

    Returns:
        True 表示本地已无该文件（删除成功或本就不存在）。
    """
    try:
        os.remove(local_path)
        logger.info("Cleaned local artifact after upload: %s", local_path)
        return True
    except FileNotFoundError:
        return True
    except OSError as exc:
        logger.warning("清理本地文件失败 %s: %s", local_path, exc)
        return False


def _prune_empty_build_dirs(storage_root: str, build_id: str,
                            targets: List[str]) -> None:
    """删除本次构建遗留的空目录。

    已成功上传的产物被清理后目录为空 → 删除；目录非空说明仍有失败产物
    保留在本地（便于排查），此时 `os.rmdir` 抛 OSError，跳过。
    """
    for target in targets:
        try:
            os.rmdir(os.path.join(storage_root, build_id, target))
        except OSError:
            continue
    try:
        os.rmdir(os.path.join(storage_root, build_id))
    except OSError:
        pass


def _build_result_message(summary: Dict[str, Any], failed: List[Dict[str, Any]]) -> str:
    """生成上传结果文本（markdown）：构建信息 + FTP 路径 + 上传清单。

    该文本作为后台任务结果的 message 字段，任务完成后由前端在聊天里展示；
    构建产物不经 Web 下载卡片回传（数 GB 文件交付渠道是 FTP）。
    """
    ok_count = summary.get("ok_count", 0)
    total = len(summary.get("artifacts") or [])
    build_id = summary.get("build_id", "")
    header = "✅" if ok_count and not failed else ("⚠️" if ok_count else "❌")
    lines = [
        f"{header} 构建 {build_id} 上传 FTP "
        f"{'完成' if ok_count else '失败'}（{ok_count}/{total} 个产物）",
        "",
        "| 项目 | 值 |",
        "|---|---|",
        f"| 构建 ID | {build_id} |",
        f"| 构建类型 | {summary.get('build_type', '')} |",
        f"| 构建日期 | {summary.get('build_date', '') or '未知'} |",
        f"| RC 名称 | {summary.get('release_candidate_name', '') or '无'} |",
        f"| FTP 根目录 | {summary.get('ftp_base', '')} |",
    ]

    ok_items = [r for r in (summary.get("artifacts") or []) if r.get("status") == "ok"]
    if ok_items:
        lines += ["", "**上传清单**", "", "| 产物 | 大小 | FTP 路径 |", "|---|---|---|"]
        for r in ok_items:
            lines.append(
                f"| {os.path.basename(r.get('ftp_path') or r.get('artifact') or '')} "
                f"| {_human_size(r.get('size', 0))} | {r.get('ftp_path', '')} |"
            )
        lines += ["", "> 本地下载缓存已清理，请从 FTP 获取产物。"]

    if failed:
        lines += ["", "**失败项**", ""]
        for r in failed:
            reason = {"download_failed": "下载失败",
                      "upload_failed": "上传失败",
                      "empty": "未解析到产物"}.get(r.get("status", ""), r.get("status", "失败"))
            lines.append(f"- {r.get('artifact') or r.get('target', '')}（{reason}）")
        if any(r.get("local_file") for r in failed):
            lines.append("")
            lines.append("> 失败产物已保留在服务器本地下载目录，便于排查。")
    return "\n".join(lines)


async def query_latest_build(
    cfg: CiBuildConfig,
    user_id: int,
    target: Optional[str] = None,
) -> Dict[str, Any]:
    """查询指定 branch/target 的最新成功构建。

    Args:
        cfg: CI 构建配置
        user_id: 用户 ID（定位 OAuth token cache）
        target: 完整 target 名（如 CHIPX1-userdebug）；缺省用 cfg.branch/cfg.target

    Returns:
        build 信息 dict（build_id / release_candidate_name / signed /
        creation_timestamp_ms），另加 build_date（YYYY-MM-DD）。

    Raises:
        CiBuildError: 配置缺失、token 无效或查询失败。
    """
    err = cfg.validate_query()
    if err:
        raise CiBuildError(err)

    try:
        auth = cfg.build_auth(user_id)
    except AuthRequiredError as exc:
        raise CiBuildError(str(exc)) from exc

    branch = cfg.branch
    tgt = target or cfg.target
    build = await asyncio.to_thread(
        get_latest_good_build, branch, tgt, cfg.auth.project_id, auth,
    )
    if not build or not build.get("build_id"):
        raise CiBuildError(
            f"未找到成功构建（branch={branch or '任意'} target={tgt or '任意'}）。"
            f"请确认分支/目标名称正确，且 OAuth 凭据有权访问该分支。"
        )
    build["build_date"] = _format_build_date(
        build.get("creation_timestamp_ms", ""), build["build_id"],
    )
    return build


async def run_fetch_pipeline(
    cfg: CiBuildConfig,
    user_id: int,
    build_id: Optional[str] = None,
    build_type: str = "userdebug",
    artifacts: Optional[List[str]] = None,
    progress_cb: Optional[ProgressCb] = None,
) -> Dict[str, Any]:
    """下载指定（或最新）构建的产物并经 FTPS 上传。

    Args:
        cfg: CI 构建配置（须经 validate_download 校验）
        user_id: 用户 ID（token cache / 本地下载目录按用户隔离）
        build_id: 指定构建 ID；None 时自动查询最新成功构建
        build_type: "user" / "userdebug" / "all"
        artifacts: 指定 artifact 名列表（跳过自动解析）；None 用默认解析规则
        progress_cb: 阶段进度回调（下载/上传 artifact 粒度）

    Returns:
        汇总 dict：build 信息 + artifacts 逐项结果
        （status: ok / download_failed / upload_failed；本地下载路径键为
        ``local_file``——刻意不用 file_path，防止被识别为可回传文件）。

    Raises:
        CiBuildError: 配置缺失 / token 无效 / 查询不到构建 / 全部失败。
    """
    progress = progress_cb or _noop_progress
    err = cfg.validate_download()
    if err:
        raise CiBuildError(err)
    if build_type not in ("user", "userdebug", "all"):
        raise CiBuildError(f"非法 build_type: {build_type}（可选 user / userdebug / all）")

    types = list(BUILD_TYPES) if build_type == "all" else [build_type]

    try:
        auth = cfg.build_auth(user_id)
    except AuthRequiredError as exc:
        raise CiBuildError(str(exc)) from exc

    # ── 确定构建 ──
    if build_id:
        build_info: Dict[str, Any] = {
            "build_id": str(build_id),
            "release_candidate_name": "",
            "signed": False,
            "creation_timestamp_ms": "",
        }
        # 有具体 build id 时按用户指定 type 查一次元信息（拿 RC 名/时间戳，
        # 用于 signed OTA 命名与 FTP 日期子目录）；失败不阻断，用 fallback。
        probe_target = f"{cfg.target}-{types[0]}"
        probe = await asyncio.to_thread(
            get_latest_good_build, cfg.branch, probe_target, cfg.auth.project_id, auth,
        )
        if probe:
            build_info.update(probe)
        build_info["build_date"] = _format_build_date(
            build_info.get("creation_timestamp_ms", ""), build_info["build_id"],
        )
    else:
        progress("查询最新成功构建...")
        build_info = await query_latest_build(cfg, user_id, f"{cfg.target}-{types[0]}")

    build_id = build_info["build_id"]
    rc_name = build_info.get("release_candidate_name", "")
    date_subdir = build_info.get("build_date") or _format_build_date(
        build_info.get("creation_timestamp_ms", ""), build_id,
    )
    progress(f"目标构建：{build_id}（{date_subdir}）")

    fetcher = ArtifactFetcher(str(storage_dir(user_id)), auth)
    results: List[Dict[str, Any]] = []
    full_targets: List[str] = []  # 本地下载目录按完整 target 命名，清理时要用它

    for t in types:
        full_target = f"{cfg.target}-{t}"
        full_targets.append(full_target)
        remote_dir = f"{cfg.ftp.remote_dir.rstrip('/')}/{date_subdir}/{t}"

        # ── 解析产物清单 ──
        if artifacts:
            names = list(artifacts)
        else:
            progress(f"解析 {t} 产物清单...")
            names = await asyncio.to_thread(
                resolve_artifacts, t, build_id, full_target, auth,
                cfg.auth.project_id, rc_name,
            )
        if not names:
            results.append({"target": full_target, "artifact": "", "status": "empty",
                            "message": f"{t} 未解析到任何产物"})
            continue

        uploader = FTUploader(
            cfg.ftp.host, cfg.ftp.port, cfg.ftp.username, cfg.ftp.password,
            remote_dir,
        )

        # ── 逐项下载 + 上传 ──
        for name in names:
            progress(f"[{t}] 下载 {name} ...")
            ok, local = await asyncio.to_thread(
                fetcher.download, build_id, full_target, name, cfg.auth.project_id,
            )
            if not ok or not local:
                results.append({"target": full_target, "artifact": name,
                                "status": "download_failed"})
                continue

            size = os.path.getsize(local)
            progress(f"[{t}] 上传 {name}（{_human_size(size)}）到 FTP ...")
            uploaded = await asyncio.to_thread(uploader.upload, local, 3)

            # 上传成功即清理本地副本：下载目录只是上传 FTP 的中转缓存，
            # 本地不留数据（失败时保留文件便于排查）。
            cleaned = _cleanup_uploaded_file(local) if uploaded else False

            results.append({
                "target": full_target,
                "artifact": name,
                "status": "ok" if uploaded else "upload_failed",
                "size": size,
                # 刻意不用 file_path 键：避免后台任务结果被识别为可回传文件
                # local_file 非空 ⇒ 本地仍有该文件（仅失败时保留）
                "local_file": "" if cleaned else local,
                "local_cleaned": cleaned,
                "ftp_path": f"{remote_dir}/{os.path.basename(local)}" if uploaded else "",
            })
            if uploaded:
                progress(f"[{t}] {name} 上传完成，本地缓存已清理")

    # 清理本次构建遗留的空目录（已成功上传的产物目录）
    _prune_empty_build_dirs(str(storage_dir(user_id)), build_id, full_targets)

    ok_count = sum(1 for r in results if r["status"] == "ok")
    failed = [r for r in results if r["status"] != "ok"]

    summary: Dict[str, Any] = {
        "success": ok_count > 0,
        "build_id": build_id,
        "build_type": build_type,
        "build_date": build_info.get("build_date", ""),
        "release_candidate_name": rc_name,
        "ftp_base": f"{cfg.ftp.remote_dir.rstrip('/')}/{date_subdir}",
        "ok_count": ok_count,
        "fail_count": len(failed),
        "artifacts": results,
    }
    if ok_count == 0:
        summary["error"] = (
            f"构建 {build_id} 的 {len(results)} 个产物全部下载/上传失败，请查看日志。"
        )
    # 结果文本经任务结果 message 字段回聊天展示（前端任务完成气泡渲染 markdown）
    summary["message"] = _build_result_message(summary, failed)
    return summary
