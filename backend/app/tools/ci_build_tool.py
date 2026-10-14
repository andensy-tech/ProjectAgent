"""内置工具：Google CI (Android Build) 构建查询 / 下载 + FTPS 上传。

ci_build_query      —— 进程内快速查询最新成功构建（build id / RC 名 / 签名 /
                       构建时间 / 产物清单），chat 秒回。
ci_build_download   —— 下载指定（或最新）构建产物并自动上传 FTPS。
                       数 GB 级耗时操作提交 BackgroundTaskWorker 后台队列
                       （复用 task_queue，跨 worker 经 DB 共享状态），
                       返回 task_submitted 由前端轮询 /api/tasks/{task_id}。

配置来源：UserSetting key="ci_build"（设置页「CI 构建」Tab），
后端经 get_ci_build_config_raw 取明文（client_secret / FTP 密码），
绝不暴露给 API。OAuth token cache 按 user_id 隔离在
backend/data/ci_build/tokens/token_{user_id}.json。
"""

import asyncio
import logging
from dataclasses import replace
from typing import Any, Callable, Dict, Optional

from app.services.settings_service import get_ci_build_config_raw

logger = logging.getLogger(__name__)


async def _get_worker():
    """返回全局后台任务 worker（与 engine / api.tasks 共用同一进程内单例）。

    收敛单例的原因：完成回调（飞书推送等）按 task_id 注册，必须落在
    执行该任务的实例上；此前本模块自持独立 worker，跨实例注册永不触发。
    """
    from app.services.task_queue import get_worker
    return await get_worker()


def _err(message: str) -> Dict[str, Any]:
    """统一错误返回（引擎识别 type=error）。"""
    return {"type": "error", "error": message, "success": False}


def _get_conversation_id(context: Dict[str, Any]) -> Optional[int]:
    """从工具上下文取会话 ID（引擎注入的 `_conversation_id`）。"""
    cid = context.get("_conversation_id") if isinstance(context, dict) else None
    if not cid:
        return None
    try:
        return int(cid)
    except (TypeError, ValueError):
        return None


async def _deliver_completion(user_id: int, conversation_id: Optional[int],
                              summary: Dict[str, Any]) -> None:
    """任务完成后把结果送达到用户。

    后台任务运行在 HTTP 请求之外，耗时可达数十分钟——**不能依赖前端轮询
    存活**（用户刷新/切换会话后轮询即死，结果永远收不到）。因此这里主动：

    1. 把结果写成一条 assistant 消息落库 → 会话历史里永久可见（刷新/换设备都在）
    2. 发一条站内消息（顶栏铃铛）→ 用户不在该会话时也能收到提醒

    任一步失败只记日志，绝不让送达环节影响任务本身的结果。
    """
    text = summary.get("message") or ""
    if not text:
        return
    build_id = summary.get("build_id", "")
    ok_count = summary.get("ok_count", 0)
    total = len(summary.get("artifacts") or [])

    # ── 1. 写入会话历史 ──
    if conversation_id:
        try:
            from app.crud.conversation import create_message
            from app.db.session import AsyncSessionLocal

            async with AsyncSessionLocal() as db:
                await create_message(
                    db,
                    conversation_id=conversation_id,
                    role="assistant",
                    content=text,
                    message_type="text",
                    extra_data={
                        "source": "background_task",
                        "skill": "ci_build_download",
                        "build_id": build_id,
                    },
                )
            logger.info("CI 构建结果已写入会话 %s（build %s）",
                        conversation_id, build_id)
        except Exception:
            logger.warning("CI 构建结果写入会话失败（conv=%s）", conversation_id,
                           exc_info=True)

    # ── 2. 站内消息提醒 ──
    try:
        from app.services.notification_service import create_notification

        all_ok = ok_count > 0 and not summary.get("fail_count")
        title = (f"CI 构建{'上传完成' if all_ok else '上传结束'}：{build_id}"
                 f"（{ok_count}/{total} 个产物）")
        await create_notification(
            user_id=user_id,
            title=title[:200],
            content=(summary.get("ftp_base") or "") + "\n" + text[:300],
            notif_type="ci_build",
            level="HIGH" if all_ok else "MEDIUM",
        )
    except Exception:
        logger.warning("CI 构建完成站内消息创建失败（user=%s）", user_id, exc_info=True)


def _get_user_id(context: Dict[str, Any]) -> Optional[int]:
    user_id = context.get("_user_id") if isinstance(context, dict) else None
    if not user_id:
        return None
    return int(user_id)


def _resolve_query_target(cfg, target_param: str, build_type: str) -> str:
    """解析查询用的完整 target（如 CHIPX1-userdebug）。

    build_type="all" 不是合法的 target 后缀——同一构建下 user 与 userdebug
    共享 build id，查询时用 userdebug 作代表即可定位构建。

    Args:
        cfg: CI 构建配置（提供默认 target）
        target_param: 用户传入的 target（可为空、可为完整名或基础名）
        build_type: user / userdebug / all

    Returns:
        完整 target 名；无法解析时返回空串。
    """
    base = (target_param or cfg.target or "").strip()
    if not base:
        return ""
    if base.endswith(("-user", "-userdebug")):
        return base
    suffix = "userdebug" if build_type == "all" else build_type
    return f"{base}-{suffix}"


async def _load_config(user_id: int):
    """读用户 CI 构建配置（明文）→ CiBuildConfig。"""
    from app.services.ci_build import config_from_dict

    raw = await get_ci_build_config_raw(user_id)
    return config_from_dict(raw)


class _ProgressReporter:
    """把管线阶段进度写入后台任务进度（惰性绑定 task_id，防提交竞态）。"""

    def __init__(self, worker) -> None:
        self._worker = worker
        self._task_id: Optional[str] = None

    def bind(self, task_id: str) -> None:
        self._task_id = task_id

    def __call__(self, message: str) -> None:
        if not self._task_id:
            return
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._worker.update_progress(self._task_id, message))
        except RuntimeError:
            pass


async def ci_build_query(params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """查询 Google CI 关注分支上的最新成功构建。

    参数（均可选，缺省用设置页配置）：
      branch     —— 分支名（覆盖配置）
      target     —— 完整 target（如 CHIPX1-userdebug）或基础名（自动补默认类型后缀）
      build_id   —— 指定构建 ID 时改为列出该构建的产物清单
      build_type —— target 为基础名时补的后缀（user/userdebug，默认取配置）
    """
    from app.services.ci_build import (
        CiBuildError, config_from_dict, list_artifacts, query_latest_build,
    )

    user_id = _get_user_id(context)
    if not user_id:
        return _err("缺少用户身份信息，无法读取 CI 构建配置")

    try:
        cfg = await _load_config(user_id)
    except Exception as exc:
        return _err(f"读取 CI 构建配置失败: {exc}")

    # 参数覆盖
    branch = str(params.get("branch") or cfg.branch)
    target_param = str(params.get("target") or "")
    build_type = str(params.get("build_type") or cfg.default_build_type or "userdebug")
    build_id = str(params.get("build_id") or "")

    target = _resolve_query_target(cfg, target_param, build_type)

    cfg = replace(cfg, branch=branch)

    if build_id:
        # 列出指定构建的产物清单
        if not target:
            return _err("指定 build_id 查询产物时必须提供 target（或先在设置页配置）")
        try:
            auth = cfg.build_auth(user_id)
        except Exception as exc:
            return _err(str(exc))
        artifacts = await asyncio.to_thread(
            list_artifacts, build_id, target, cfg.auth.project_id, auth,
        )
        if not artifacts:
            return _err(
                f"构建 {build_id}（{target}）没有可访问的产物，"
                f"请确认 build id / target 正确且 OAuth 凭据有权访问。"
            )
        lines = "\n".join(f"- {a}" for a in artifacts)
        return {
            "type": "text", "success": True,
            "message": f"构建 {build_id}（{target}）产物清单（{len(artifacts)} 项）:\n{lines}",
            "data": {"build_id": build_id, "target": target, "artifacts": artifacts},
        }

    # 查询最新成功构建
    try:
        build = await query_latest_build(cfg, user_id, target or None)
    except CiBuildError as exc:
        return _err(str(exc))

    # 附带该构建默认类型的产物清单
    artifacts: list = []
    if target:
        try:
            auth = cfg.build_auth(user_id)
            artifacts = await asyncio.to_thread(
                list_artifacts, build["build_id"], target, cfg.auth.project_id, auth,
            )
        except Exception:
            artifacts = []

    data = {
        "build_id": build["build_id"],
        "release_candidate_name": build.get("release_candidate_name", ""),
        "signed": build.get("signed", False),
        "creation_timestamp": build.get("creation_timestamp_ms", ""),
        "build_date": build.get("build_date", ""),
        "branch": branch,
        "target": target,
        "artifacts": artifacts,
    }
    art_msg = f"\n产物清单（{len(artifacts)} 项）: {', '.join(artifacts[:10])}" if artifacts else ""
    more = " ..." if len(artifacts) > 10 else ""
    return {
        "type": "text", "success": True,
        "message": (
            f"最新成功构建：{build['build_id']}\n"
            f"- 分支: {branch or '（任意）'}\n"
            f"- Target: {target or '（任意）'}\n"
            f"- RC 名称: {build.get('release_candidate_name') or '无'}\n"
            f"- 已签名: {'是' if build.get('signed') else '否'}\n"
            f"- 构建时间: {build.get('creation_timestamp_ms') or '未知'}"
            f"{art_msg}{more}"
        ),
        "data": data,
    }


async def _run_download_task(params: Dict[str, Any], context: Dict[str, Any],
                             progress: Callable[[str], None]) -> Dict[str, Any]:
    """后台任务体：执行下载 + FTPS 上传管线。"""
    from app.services.ci_build import CiBuildError, run_fetch_pipeline

    user_id = _get_user_id(context)
    if not user_id:
        return {"error": "缺少用户身份信息", "success": False}

    try:
        cfg = await _load_config(user_id)
        summary = await run_fetch_pipeline(
            cfg,
            user_id=user_id,
            build_id=params.get("build_id"),
            build_type=params.get("build_type") or cfg.default_build_type,
            artifacts=params.get("artifacts"),
            progress_cb=progress,
        )
        # 结果送达：写入会话历史 + 站内消息（不依赖前端轮询存活）
        await _deliver_completion(user_id, params.get("conversation_id"), summary)
        return summary
    except CiBuildError as exc:
        await _deliver_completion(user_id, params.get("conversation_id"),
                                  {"message": f"❌ CI 构建下载任务失败：{exc}",
                                   "success": False, "build_id": "",
                                   "ok_count": 0, "fail_count": 1,
                                   "ftp_base": "", "artifacts": []})
        return {"error": str(exc), "success": False}
    except Exception as exc:  # noqa: BLE001 —— 后台任务兜底，避免消费者裸崩
        logger.exception("ci_build_download task failed")
        return {"error": f"下载任务执行出错: {exc}", "success": False}


async def ci_build_download(params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """下载指定 build id 的目标构建产物并自动上传 FTPS。

    参数（均可选）：
      build_id   —— 构建 ID；缺省自动查最新成功构建
      build_type —— user / userdebug / all（默认取设置页配置）
      artifacts  —— 指定产物名列表（跳过自动解析）

    校验配置与 OAuth token 通过后提交后台任务执行（数 GB 下载+上传），
    返回 task_id 供轮询。构建产物不经 Web 文件卡片回传，交付渠道为 FTP。
    """
    from app.services.ci_build import CiBuildError, token_cache_path
    from app.services.ci_build.auth import AuthRequiredError

    user_id = _get_user_id(context)
    if not user_id:
        return _err("缺少用户身份信息，无法读取 CI 构建配置")

    try:
        cfg = await _load_config(user_id)
    except Exception as exc:
        return _err(f"读取 CI 构建配置失败: {exc}")

    build_type = str(params.get("build_type") or cfg.default_build_type or "userdebug")
    build_id = str(params.get("build_id") or "") or None
    artifacts = params.get("artifacts")
    if artifacts is not None and not isinstance(artifacts, list):
        return _err("artifacts 参数必须是产物名列表")
    if build_type not in ("user", "userdebug", "all"):
        return _err(f"非法 build_type: {build_type}（可选 user / userdebug / all）")

    # 提交前 fail-fast 校验（配置 / OAuth token），错误直接在 chat 里反馈
    err = cfg.validate_download()
    if err:
        return _err(err)
    if cfg.auth.enabled:
        try:
            auth = cfg.build_auth(user_id)
            has_token = await asyncio.to_thread(auth.has_valid_token)
        except AuthRequiredError as exc:
            return _err(str(exc))
        except Exception as exc:
            return _err(f"检查 OAuth token 失败: {exc}")
        if not has_token:
            return _err(
                "无有效 OAuth token 或 refresh 失败。请先完成一次性 Google 账号授权：\n"
                "  cd backend && source venv/bin/activate && python scripts/ci_build_auth.py\n"
                "（授权后 token 自动续期，无需重复操作）"
            )

    worker = await _get_worker()
    reporter = _ProgressReporter(worker)

    async def _handler(p: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
        return await _run_download_task(p, ctx, reporter)

    task_id = await worker.submit(_handler, {
        "build_id": build_id,
        "build_type": build_type,
        "artifacts": artifacts,
        # 完成结果要写回该会话（用户刷新/切会话后仍能看到）
        "conversation_id": _get_conversation_id(context),
    }, user_id, "ci_build_download")
    reporter.bind(task_id)

    target_desc = f"构建 {build_id}" if build_id else f"{cfg.target}-{build_type} 最新成功构建"
    return {
        "type": "task_submitted",
        "task_id": task_id,
        "success": True,
        "message": (
            f"已提交后台任务：下载 {target_desc} 的 {build_type} 产物并上传 FTPS。"
            f"任务 ID: {task_id}，可通过任务状态查询进度，完成后产物在 FTP 目录 "
            f"{cfg.ftp.remote_dir.rstrip('/')}/YYYY-MM-DD-{build_id or '最新'}/{build_type}/ 下。"
        ),
    }
