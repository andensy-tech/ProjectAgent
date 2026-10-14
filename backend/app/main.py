"""
Project Agent - Main Application Entry Point

FastAPI application with:
- LLM dual-track routing (commercial + local)
- Agent execution engine (3-layer architecture)
- Memory system (4-layer architecture)
- MCP client integration
- Skill system
"""

import asyncio
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from app.core.config import settings
from app.api import api_router
from app.mcp import mcp_manager
from app.services.memory import memory_system
from app.services.agent.executor import action_executor
from app.skills import jira_confluence_report_handler, jira_cl_merge_monitor_handler
from app.tools import register_all_tools, tool_registry
from app.services.feishu import FeishuApiClient, FeishuWsClient, FeishuAdapter



async def _warm_mcp_clients():
    """Pre-warm MCP tool cache for admin user so gunicorn workers don't block first request.

    统一走 MCPCapabilityService.discover —— 预热仅为性能优化，不再是能力来源；
    任何用户的首次 chat 若缓存空/过期会自动发现（热启动）。
    """
    try:
        from app.mcp.capability import capability
        status = await capability.discover(1, timeout=15)
        for name, st in status.items():
            if st.get("status") == "ok":
                print(f"✅ MCP pre-warm: {name} ({st.get('tools', 0)} tools)")
            else:
                print(f"⚠️  MCP pre-warm skip: {name} ({st.get('message', '')[:80]})")
    except Exception as e:
        print(f"⚠️  MCP pre-warm failed: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    # Startup
    print(f"🚀 Starting {settings.APP_NAME} v{settings.APP_VERSION}")
    if not settings.DEBUG and settings.SECRET_KEY == "change-me-in-production":
        print("⚠️  安全警告: SECRET_KEY 使用默认值 'change-me-in-production'，请在生产环境中通过 .env 设置一个随机密钥！")

    # MCP clients are created on-demand from per-user DB settings.
    # No startup initialization from config.yaml.
    # The executor registers MCP clients dynamically when first needed.
    print("ℹ️  MCP clients: lazy-loaded from per-user settings (no config.yaml)")

    # Pre-warm MCP clients for admin user so first request isn't slow
    try:
        await _warm_mcp_clients()
    except Exception as e:
        print(f"ℹ️  MCP pre-warm failed (non-fatal): {e}")


    
    # Register system memory (from discovered skills + explicit handlers)
    from app.skills.scanner import skill_scanner
    discovered = skill_scanner.scan()
    discovered_names = {s.name for s in discovered}

    if "jira-confluence-report" in discovered_names:
        memory_system.system_memory.add_skill(
            name="jira-confluence-report",
            description="汇总 Jira 问题并生成 Confluence 报告（已配置默认参数，可直接执行）",
            params=["config_name", "epics", "labels"],
        )
    if "jira-cl-merge-monitor" in discovered_names:
        memory_system.system_memory.add_skill(
            name="jira-cl-merge-monitor",
            description="监控 CL 合并状态并生成风险预警报告（已配置默认参数，可直接执行）",
            params=["config_name"],
        )
    
    # Register explicit Skill handlers (process-internal, using MCPManager)
    action_executor.register_skill("jira-confluence-report", jira_confluence_report_handler)
    action_executor.register_skill("jira-cl-merge-monitor", jira_cl_merge_monitor_handler)

    # Auto-discover script-based skills from ProjectAgent/skills/ directory
    # Script-based skills OVERRIDE explicit handlers — they are independently executable,
    # not coupled to the Agent backend, and can be reused by other AI chat tools.
    from app.skills.script_runner import make_script_handler

    auto_count = 0
    overrides = []
    for skill_def in discovered:
        if skill_def.name in action_executor.skill_registry:
            overrides.append(skill_def.name)
        if skill_def.is_script_skill():
            handler = make_script_handler(skill_def)
            action_executor.register_skill(skill_def.name, handler)
            auto_count += 1

    total = len(action_executor.skill_registry)
    print(f"✅ Skills registered: {total} total "
          f"({total - auto_count} explicit + {auto_count} auto-discovered"
          f"{', overrides: ' + ', '.join(overrides) if overrides else ''})")

    # Add all auto-discovered script skills to system memory so LLM knows about them
    for skill_def in discovered:
        if skill_def.is_script_skill() and skill_def.name not in memory_system.system_memory.skills:
            memory_system.system_memory.add_skill(
                name=skill_def.name,
                description=skill_def.description or skill_def.display_name or skill_def.name,
                params=skill_def.entry_args or [],
            )

    # Register built-in document processing tools
    register_all_tools()
    for tool_def in tool_registry.list_tools():
        action_executor.register_tool(tool_def.name, tool_def.handler)
        memory_system.system_memory.add_builtin_tool(
            name=tool_def.name,
            description=tool_def.description,
            input_types=tool_def.input_types,
        )
    print(f"✅ Built-in tools registered: {len(tool_registry.list_tools())}")

    # ── Feishu Bot: WebSocket 长连接 ──
    # 当设置了 PROJECT_AGENT_DISABLE_FEISHU=1 时跳过（独立 run_feishu.py 进程处理）
    feishu_app_id = settings.FEISHU_APP_ID
    feishu_app_secret = settings.FEISHU_APP_SECRET
    feishu_client = None
    feishu_ws = None

    if os.environ.get("PROJECT_AGENT_DISABLE_FEISHU", "").lower() not in ("1", "true"):
        if feishu_app_id and feishu_app_secret:
            from app.services.agent import agent_engine

            try:
                feishu_client = FeishuApiClient(feishu_app_id, feishu_app_secret)
                feishu_adapter = FeishuAdapter(feishu_client, agent_engine)
                main_loop = asyncio.get_event_loop()
                feishu_ws = FeishuWsClient(
                    app_id=feishu_app_id,
                    app_secret=feishu_app_secret,
                    loop=main_loop,
                    event_callback=feishu_adapter.handle_event,
                )
                feishu_ws.start()
                print("✅ Feishu bot WebSocket client started")
            except Exception as e:
                print(f"⚠️  Feishu bot init failed: {e}")
        else:
            print("ℹ️  Feishu bot not configured (set FEISHU_APP_ID / FEISHU_APP_SECRET to enable)")
    else:
        print("ℹ️  Feishu bot skipped (managed by separate run_feishu.py process)")

    # Store instances on app state for access by other routes if needed
    app.state.feishu_client = feishu_client
    app.state.feishu_ws = feishu_ws
    
    # Auto-run database migrations in development
    if settings.DEBUG:
        try:
            from app.db.session import init_db, seed_default_user
            await init_db()
            print(f"✅ Database tables created (mode: {settings.DEBUG=})")
            # Run alembic migrations so schema changes (new columns) are applied
            # even when tables already exist from a previous create_all.
            try:
                from alembic.config import Config
                from alembic import command
                alembic_cfg = Config("alembic.ini")
                command.upgrade(alembic_cfg, "head")
                print("✅ Alembic migrations applied")
            except Exception as alembic_err:
                print(f"⚠️  Alembic upgrade skipped: {alembic_err}")
            await seed_default_user()
        except Exception as e:
            print(f"⚠️  Database init skipped: {e}")

    # Run memory maintenance check on startup (best-effort)
    try:
        from app.db.session import AsyncSessionLocal as async_session
        from sqlalchemy import text, select

        async with async_session() as startup_db:
            # Process all users with undistilled logs
            result = await startup_db.execute(text("SELECT DISTINCT user_id FROM memory_logs WHERE distilled = false"))
            user_ids = [row[0] for row in result.fetchall()]
            for uid in user_ids:
                print(f"📋 Found undistilled memory logs for user {uid}, running maintenance...")
                stats = await memory_system.run_maintenance(startup_db, uid)
                print(f"✅ Startup maintenance for user {uid}: {stats}")
    except Exception as e:
        print(f"ℹ️  Startup memory maintenance skipped: {e}")

    # Start background daily CL monitoring refresh
    _cl_scheduler_task = None

    async def _get_all_active_user_ids():
        """Get all active user IDs for background tasks."""
        try:
            from app.db.session import AsyncSessionLocal as _session
            from sqlalchemy import select
            from app.models import User
            async with _session() as _db:
                result = await _db.execute(select(User.id).where(User.is_active == True))
                return [row[0] for row in result.fetchall()]
        except Exception:
            return [1]  # fallback

    async def _run_concurrent_for_users(user_ids, fn, max_concurrent=5, label=""):
        """Run an async function for each user concurrently with a concurrency limit."""
        sem = asyncio.Semaphore(max_concurrent)

        async def _wrapped(uid):
            async with sem:
                try:
                    await fn(uid)
                except Exception as e:
                    # flush=True：stdout 重定向到文件时是块缓冲，不加会看不到失败
                    print(f"⚠️  {label} failed for user {uid}: {e}", flush=True)

        await asyncio.gather(*(_wrapped(uid) for uid in user_ids))

    def _should_run_task(task_name: str, interval: int) -> bool:
        """跨进程协调：只有一台 worker 会返回 True，避免多 worker 重复执行同一任务。"""
        try:
            from app.task_coordinator import should_run
            return should_run(task_name, interval)
        except Exception:
            return True

    async def _daily_cl_scan():
        """CL 监控每日扫描（V2）：状态 diff + 焦点 CL 确认 + 提醒 + 页面缓存重建。

        与手动刷新走同一条路径 `run_daily_scan`，避免两套逻辑口径不一致。
        """
        interval = settings.CL_SCAN_INTERVAL_SECONDS
        dry_run = settings.CL_SCAN_DRY_RUN
        await asyncio.sleep(10)
        while True:
            if _should_run_task("cl_scan", interval):
                try:
                    from app.services.cl_scan_service import run_daily_scan
                    user_ids = await _get_all_active_user_ids()
                    await _run_concurrent_for_users(
                        user_ids,
                        lambda uid: run_daily_scan(uid, dry_run=dry_run),
                        max_concurrent=3, label="CL scan",
                    )
                    print(f"✅ CL scan completed for {len(user_ids)} user(s)"
                          f"{' [dry-run]' if dry_run else ''}", flush=True)
                except Exception as e:
                    print(f"⚠️  CL daily scan failed: {e}", flush=True)
            await asyncio.sleep(600)  # 每 10 分钟检查一次（coordinator 强制执行真实间隔）

    _cl_scheduler_task = asyncio.create_task(_daily_cl_scan())
    print(f"✅ CL scan scheduler started (interval: {settings.CL_SCAN_INTERVAL_SECONDS}s, "
          f"dry_run={settings.CL_SCAN_DRY_RUN})")

    # Start background daily dashboard summary refresh
    async def _daily_dashboard_refresh():
        """Background loop: check every hour, refresh if stale (>24h) for all users."""
        await asyncio.sleep(30)
        while True:
            if _should_run_task("dashboard_refresh", 3600):
                try:
                    from app.services.dashboard_snapshot_service import any_stale, _do_refresh
                    user_ids = await _get_all_active_user_ids()
                    stale_uids = []
                    for uid in user_ids:
                        if await any_stale(user_id=uid):
                            stale_uids.append(uid)
                    if stale_uids:
                        await _run_concurrent_for_users(
                            stale_uids,
                            lambda uid: _do_refresh(user_id=uid),
                            max_concurrent=5, label="Dashboard refresh",
                        )
                    print(f"✅ Dashboard snapshot refreshed for {len(user_ids)} user(s) ({len(stale_uids)} stale)")
                except Exception as e:
                    print(f"⚠️  Dashboard daily refresh failed: {e}")
            await asyncio.sleep(600)

    _dashboard_scheduler_task = asyncio.create_task(_daily_dashboard_refresh())
    print("✅ Dashboard daily refresh scheduler started (check interval: 1h)")

    # Start background metric snapshot collection (every 5 minutes)
    async def _metric_snapshot_loop():
        """Background loop: collect dashboard + CL metrics for all users every 5 minutes."""
        while True:
            await asyncio.sleep(300)
            if _should_run_task("metric_snapshot", 300):
                try:
                    from app.services.trend_service import collect_snapshot
                    user_ids = await _get_all_active_user_ids()
                    await _run_concurrent_for_users(
                        user_ids,
                        lambda uid: collect_snapshot(user_id=uid),
                        max_concurrent=10, label="Metric snapshot",
                    )
                except Exception as e:
                    print(f"⚠️  Metric snapshot collection failed: {e}")

    _snapshot_task = asyncio.create_task(_metric_snapshot_loop())
    print("✅ Metric snapshot collection started (interval: 5 min)")

    # CL 自动提醒（原 _cl_auto_notify_loop）已于 2026-09-16 合并进 _daily_cl_scan：
    # 提醒规则、RD 回复检测、快照刷新现在是 `run_daily_scan` 内部的步骤，
    # 不再由独立的 30 分钟循环驱动（两套循环会互相覆盖状态）。

    # Start background focus-set refresh + reminder loop (P0-1/P0-2)
    async def _focus_set_loop():
        """每 30 分钟对每用户刷新关注集合成员 + 扫描到期/达标提醒 PM。"""
        await asyncio.sleep(75)  # stagger: 错开启动写冲突
        while True:
            try:
                if _should_run_task("focus_set_refresh", 1800):  # 跨进程只一台 worker 执行
                    from app.services.focus_set_refresh_service import run_focus_set_cycle
                    user_ids = await _get_all_active_user_ids()
                    await _run_concurrent_for_users(
                        user_ids,
                        lambda uid: run_focus_set_cycle(user_id=uid),
                        max_concurrent=3, label="Focus set",
                    )
                    print("✅ Focus set refresh + reminder cycle completed")
            except Exception as e:
                print(f"⚠️  Focus set cycle failed: {e}")
            await asyncio.sleep(600)  # 每 10 分钟检查一次（coordinator 强制执行 30min 间隔）

    _focus_set_task = asyncio.create_task(_focus_set_loop())
    print("✅ Focus set refresh + reminder scheduler started (interval: 30min)")

    # Start background daily alert scan
    async def _daily_alert_scan():
        """Background loop: check every 24 hours, run alert rules."""
        await asyncio.sleep(60)  # stagger: avoid startup write-contention
        while True:
            try:
                from app.tasks.alert_scanner import run_alert_scan
                created = await run_alert_scan()
                if created:
                    print(f"🚨 Alert scan created {len(created)} new alert(s): {created}")
                else:
                    print("✅ Alert scan completed — no new alerts")
            except Exception as e:
                print(f"⚠️  Daily alert scan failed: {e}")
            await asyncio.sleep(86400)  # every 24 hours

    _alert_scanner_task = asyncio.create_task(_daily_alert_scan())
    print("✅ Daily alert scan scheduler started (check interval: 24h)")

    # Start background context-spill cleanup loop (层 B/C，2026-09-08)
    async def _context_spill_cleanup_loop():
        """每小时清扫过期的 context spill 暂存文件（纯文件 mtime 判断，无 DB/网络）。

        spill 是上下文溢出时的大数据临时暂存区（data/context_cache/{user_id}/），
        默认 24h TTL；清扫是幂等文件操作，跨进程单 worker 由 _should_run_task 保证。
        """
        await asyncio.sleep(120)  # stagger: 错开启动写冲突
        while True:
            try:
                if _should_run_task("spill_cleanup", 3600):
                    from app.services.context_spill import cleanup_expired_spills
                    removed = await cleanup_expired_spills()
                    if removed:
                        print(f"🧹 Context spill cleanup removed {removed} expired file(s)")
            except Exception as e:
                print(f"⚠️  Context spill cleanup failed: {e}")
            await asyncio.sleep(1800)  # 每 30 分钟检查一次（coordinator 强制执行 1h 间隔）

    _spill_cleanup_task = asyncio.create_task(_context_spill_cleanup_loop())
    print("✅ Context spill cleanup scheduler started (check interval: 1h)")

    # Start generic scheduled-task engine loop (Agent 通用定时任务，2026-09-09)
    async def _scheduled_task_loop():
        """通用定时任务引擎驱动：每 5 分钟检查到期任务并执行（跨进程单 worker）。

        任务的实际调度由 scheduled_task_service.next_run_at 控制（interval_minutes 粒度），
        本 loop 只负责周期性唤起引擎；新增任务类型无需改这里（register_handler 注册即可）。
        """
        await asyncio.sleep(150)  # stagger: 错开启动写冲突
        while True:
            try:
                if _should_run_task("scheduled_tasks", 300):
                    from app.services import scheduled_task_handlers  # noqa: F401 — 触发 handler 自注册
                    from app.services.scheduled_task_service import run_due_tasks
                    summary = await run_due_tasks()
                    if summary.get("ran") or summary.get("expired"):
                        print(f"⏰ 定时任务本轮: 执行 {summary['ran']} 个, 到期停用 {summary['expired']} 个")
            except Exception as e:
                print(f"⚠️  Scheduled task engine failed: {e}")
            await asyncio.sleep(300)

    _scheduled_task_task = asyncio.create_task(_scheduled_task_loop())
    print("✅ Scheduled task engine started (check interval: 5min)")

    yield

    # Shutdown — flush all active working memories before closing
    try:
        from app.db.session import AsyncSessionLocal as async_session
        async with async_session() as shutdown_db:
            flushed = 0
            for _key, wm in list(memory_system.working_memories.items()):
                if wm.user_id and wm.conversation_id and (wm.entities or wm.context_vars or any(
                    any(kw in (m.get("content", "") or "") for kw in ("记住", "保存", "重要", "决定"))
                    for m in wm.messages[-10:]
                )):
                    stats = await memory_system.flush_important_context(
                        shutdown_db, wm.user_id, wm.conversation_id, reason="shutdown"
                    )
                    if stats.get("entities_saved") or stats.get("vars_saved") or stats.get("patterns_saved"):
                        flushed += 1
            if flushed:
                await shutdown_db.commit()
                print(f"✅ Flushed {flushed} working memory(s) to long-term memory on shutdown")
            else:
                print("ℹ️  No working memory to flush on shutdown")
    except Exception as e:
        print(f"ℹ️  Shutdown memory flush skipped: {e}")

    from app.mcp.capability import capability
    await capability.close()
    print("👋 Shutting down...")

    # ── Feishu Bot 清理 ──
    feishu_ws = getattr(app.state, "feishu_ws", None)
    if feishu_ws:
        feishu_ws.stop()
    feishu_client = getattr(app.state, "feishu_client", None)
    if feishu_client:
        await feishu_client.close()
        print("✅ Feishu bot stopped")


# Create FastAPI app
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="AI-powered project management assistant",
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routes
app.include_router(api_router)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "llm_commercial": bool(settings.COMMERCIAL_LLM_API_KEY),
        "llm_local": bool(settings.LOCAL_LLM_API_BASE),
        "mcp_servers": mcp_manager.get_available_servers(),
    }

# Serve frontend static files (if built)
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/")
    async def serve_frontend():
        """Serve frontend index.html."""
        return FileResponse(os.path.join(static_dir, "index.html"))

    @app.get("/{path:path}")
    async def serve_frontend_routes(path: str):
        """Serve frontend routes (SPA support)."""
        file_path = os.path.join(static_dir, path)
        if os.path.exists(file_path) and os.path.isfile(file_path):
            return FileResponse(file_path)
        return FileResponse(os.path.join(static_dir, "index.html"))
else:
    @app.get("/")
    async def root():
        """Root endpoint - API only mode."""
        return {
            "name": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "status": "running",
            "mode": "api-only",
            "message": "Frontend not built. Run 'npm run build' in frontend/",
        }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=settings.BACKEND_PORT)
