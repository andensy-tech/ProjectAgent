#!/usr/bin/env python3
"""
Project Agent - 独立飞书 Bot 进程

作为独立进程运行，管理飞书 WebSocket 长连接 + AgentEngine
（MCP 客户端、技能、工具等全量初始化）。

由 restart.sh start 启动，restart.sh stop 停止，与 Web 服务解耦。
"""

import asyncio
import os
import signal
import sys
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("run_feishu")

# 确保 backend/ 在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


async def startup():
    """初始化 MCP / 技能 / 工具，启动飞书 WS 客户端."""
    from app.core.config import settings
    from app.mcp import mcp_manager
    from app.services.agent.executor import action_executor
    from app.services.memory import memory_system
    from app.tools import register_all_tools, tool_registry
    from app.services.feishu import FeishuApiClient, FeishuWsClient, FeishuAdapter

    print("🤖 Starting Feishu Bot process...")

    # ── 1. MCP 客户端 ──
    try:
        await mcp_manager.initialize_all()
        servers = mcp_manager.get_available_servers()
        print(f"✅ MCP clients initialized: {servers}")

        for name in servers:
            client = mcp_manager.get_client(name)
            action_executor.register_mcp_client(name, client)

        for name in servers:
            try:
                client = mcp_manager.get_client(name)
                tools = await client.list_tools()
                for tool in tools:
                    memory_system.system_memory.add_tool(
                        name, tool.get("name", "unknown"), tool.get("description", "")
                    )
            except Exception as e:
                print(f"  ⚠️  Failed to discover tools from '{name}': {e}")
    except Exception as e:
        print(f"⚠️  Some MCP clients failed to initialize: {e}")

    # ── 2. 技能注册 ──
    from app.skills.scanner import skill_scanner
    from app.skills.script_runner import make_script_handler

    discovered = skill_scanner.scan()
    discovered_names = {s.name for s in discovered}

    from app.skills import jira_confluence_report_handler, jira_cl_merge_monitor_handler

    if "jira-confluence-report" in discovered_names:
        memory_system.system_memory.add_skill(
            name="jira-confluence-report",
            description="Summarize Jira issues and generate Confluence reports",
            params=["config_name", "epics", "labels"],
        )
    if "jira-cl-merge-monitor" in discovered_names:
        memory_system.system_memory.add_skill(
            name="jira-cl-merge-monitor",
            description="Monitor CL merge status and generate risk warnings",
            params=["config_name"],
        )

    action_executor.register_skill("jira-confluence-report", jira_confluence_report_handler)
    action_executor.register_skill("jira-cl-merge-monitor", jira_cl_merge_monitor_handler)

    auto_count = 0
    overrides = []
    for skill_def in discovered:
        if skill_def.name in action_executor.skill_registry:
            overrides.append(skill_def.name)
        if skill_def.is_script_skill():
            handler = make_script_handler(skill_def)
            action_executor.register_skill(skill_def.name, handler)
            auto_count += 1

    print(f"✅ Skills registered: {len(action_executor.skill_registry)} total "
          f"({len(action_executor.skill_registry) - auto_count} explicit + {auto_count} auto-discovered"
          f"{', overrides: ' + ', '.join(overrides) if overrides else ''})")

    # ── 3. 内置工具注册 ──
    register_all_tools()
    for tool_def in tool_registry.list_tools():
        action_executor.register_tool(tool_def.name, tool_def.handler)
        memory_system.system_memory.add_builtin_tool(
            name=tool_def.name,
            description=tool_def.description,
            input_types=tool_def.input_types,
        )
    print(f"✅ Built-in tools registered: {len(tool_registry.list_tools())}")

    # ── 4. 飞书 Bot ──
    feishu_app_id = settings.FEISHU_APP_ID
    feishu_app_secret = settings.FEISHU_APP_SECRET

    if not feishu_app_id or not feishu_app_secret:
        print("❌ FEISHU_APP_ID / FEISHU_APP_SECRET not configured, exiting.")
        return None

    from app.services.agent import agent_engine

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

    return feishu_ws


async def main():
    ws = await startup()
    if ws is None:
        sys.exit(1)

    # 等待终止信号
    stop_event = asyncio.Event()

    def _handle_signal():
        logger.info("Received shutdown signal, stopping Feishu bot...")
        stop_event.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_signal)
        except NotImplementedError:
            # Windows 不支持 add_signal_handler
            pass

    await stop_event.wait()

    # 清理
    ws.stop()
    await mcp_manager.close_all()
    print("👋 Feishu Bot stopped.")


if __name__ == "__main__":
    asyncio.run(main())
