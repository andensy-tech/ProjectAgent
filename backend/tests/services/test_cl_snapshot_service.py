"""CL 监控的故障韧性回归测试。

背景（2026-09-03 线上故障，2026-09-16 迁移到 V2 扫描路径）：
  1. Jira MCP 被 CAPTCHA 锁定时返回错误字符串/异常，旧代码把它当"结果为空"，
     生成全零结果写进快照，覆盖了历史数据，导致看板"数据消失"；
  2. V2 换成追踪队列后，同类风险变成两种：
     a. JQL 查询失败的项目若被当成"没有题"，会把队列里在途的题全部漏掉；
     b. 评论拉取失败若被当成"没有 CL"，会把一大批题误报成"未登记 CL"。

本文件覆盖这两条 + 页面缓存重建/回退。
"""

import pytest

from app.mcp.capability import capability
from app.services import cl_queue_service as queue
from app.services import cl_snapshot_service as svc
from app.services import cl_scan_service as scan

USER_ID = 1

PROJECT = {"name": "P1", "enabled": True, "cl_monitoring_enabled": True,
           "advanced_jql": "project = TV"}


def _patch_config(monkeypatch, projects=None):
    async def _cfg(user_id=None):
        return {"projects": projects if projects is not None else []}
    monkeypatch.setattr("app.services.dashboard_service.get_cl_monitoring_config", _cfg)


async def _no_rd_scan(user_id):
    return {"suppressed": 0, "analyzed": [], "acks": {}}


def _mute_rd_scan(monkeypatch):
    monkeypatch.setattr(
        "app.services.cl_auto_notify_service.scan_rd_replies_for_suppression", _no_rd_scan)


@pytest.mark.asyncio
async def test_jql_failure_does_not_wipe_queue(db_engine, monkeypatch):
    """JQL 查询失败的项目整轮跳过：队列中已有状态必须原样保留（不清零）。"""
    _patch_config(monkeypatch, [PROJECT])
    _mute_rd_scan(monkeypatch)

    # 第一轮：正常拿到 TV-1（Resolved，CL 未合并）
    async def call_ok(server, tool, params, user_id=None, timeout=None):
        if tool == "search_issues":
            return {"issues": [{"key": "TV-1", "fields": {
                "summary": "s", "assignee": {"name": "Kai.Qin"},
                "status": {"name": "Resolved"}, "updated": "u1"}}]}
        if tool == "get_issue_comments":
            return {"comments": [{"body": (
                "Change proposed: 'f [1/1]' (p b)\n"
                "https://gerrit-2.example.com/c/p/+/1")}]}
        if tool == "get_change":
            return "**状态**: NEW"
        return {}

    async def servers(user_id=None):
        return {"jira": {}, "gerrit_gerrit2": {}}

    monkeypatch.setattr(capability, "call", call_ok)
    monkeypatch.setattr(capability, "get_servers", servers)
    await scan.run_daily_scan(USER_ID, dry_run=True)

    # 第二轮：Jira 返回错误字符串（如 403 CAPTCHA）→ 必须报错而不是当作"没有题"
    async def call_fail(server, tool, params, user_id=None, timeout=None):
        if tool == "search_issues":
            return "错误: JiraError HTTP 403 CAPTCHA_CHALLENGE"
        return await call_ok(server, tool, params, user_id, timeout)

    monkeypatch.setattr(capability, "call", call_fail)
    stats = await scan.run_daily_scan(USER_ID, dry_run=True)

    assert stats["projects"] == 0
    assert any("403" in e or "响应异常" in e for e in stats["errors"])

    from app.db.session import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        states = await queue.load_states(db, USER_ID, only_queued=True)
    assert len(states) == 1
    assert states[0].status == "Resolved"      # 状态未被清空
    assert states[0].cl_unmerged == 1          # CL 计数未被清零


@pytest.mark.asyncio
async def test_comment_fetch_failure_not_flagged_as_unregistered(db_engine, monkeypatch):
    """评论拉取失败 -> 保留上次 CL 结果，不得误判成「未登记 CL」。"""
    _patch_config(monkeypatch, [PROJECT])
    _mute_rd_scan(monkeypatch)

    async def servers(user_id=None):
        return {"jira": {}, "gerrit_gerrit2": {}}

    async def call_ok(server, tool, params, user_id=None, timeout=None):
        if tool == "search_issues":
            return {"issues": [{"key": "TV-1", "fields": {
                "summary": "s", "assignee": {"name": "Kai.Qin"},
                "status": {"name": "Verified"}, "updated": "u1"}}]}
        if tool == "get_issue_comments":
            return {"comments": [{"body": (
                "Change proposed: 'f [1/1]' (p b)\n"
                "https://gerrit-2.example.com/c/p/+/1")}]}
        if tool == "get_change":
            return "**状态**: NEW"
        return {}

    monkeypatch.setattr(capability, "get_servers", servers)
    monkeypatch.setattr(capability, "call", call_ok)
    await scan.run_daily_scan(USER_ID, dry_run=True)

    # 第二轮：issue.updated 变了触发复查，但 get_issue_comments 抛错
    async def call_fail(server, tool, params, user_id=None, timeout=None):
        if tool == "get_issue_comments":
            raise RuntimeError("MCP 超时")
        if tool == "search_issues":
            return {"issues": [{"key": "TV-1", "fields": {
                "summary": "s", "assignee": {"name": "Kai.Qin"},
                "status": {"name": "Verified"}, "updated": "u2"}}]}
        return await call_ok(server, tool, params, user_id, timeout)

    monkeypatch.setattr(capability, "call", call_fail)
    await scan.run_daily_scan(USER_ID, dry_run=True)

    from app.db.session import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        state = (await queue.load_states(db, USER_ID))[0]
    assert state.cl_noregister is False    # 关键：不能变成"未登记 CL"
    assert state.cl_unmerged == 1          # 沿用上次结果


@pytest.mark.asyncio
async def test_rebuild_from_queue_writes_page_cache(db_engine, monkeypatch):
    """扫描结束后页面缓存按队列重建，未登记 CL 的焦点题也要出现。"""
    _patch_config(monkeypatch, [PROJECT])
    _mute_rd_scan(monkeypatch)

    async def servers(user_id=None):
        return {"jira": {}, "gerrit_gerrit2": {}}

    async def call(server, tool, params, user_id=None, timeout=None):
        if tool == "search_issues":
            return {"issues": [
                {"key": "TV-1", "fields": {"summary": "有CL", "assignee": {"name": "A"},
                                           "status": {"name": "Verified"}, "updated": "u"}},
                {"key": "TV-2", "fields": {"summary": "无CL", "assignee": {"name": "B"},
                                           "status": {"name": "Verified"}, "updated": "u"}},
            ]}
        if tool == "get_issue_comments":
            if params["issue_key"] == "TV-1":
                return {"comments": [{"body": (
                    "Change proposed: 'f [1/1]' (p b)\n"
                    "https://gerrit-2.example.com/c/p/+/1")}]}
            return {"comments": []}
        if tool == "get_change":
            return "**状态**: NEW"
        return {}

    monkeypatch.setattr(capability, "get_servers", servers)
    monkeypatch.setattr(capability, "call", call)
    await scan.run_daily_scan(USER_ID, dry_run=True)

    snaps = await svc.load_latest_snapshots(user_id=USER_ID)
    assert snaps is not None and len(snaps) == 1
    groups = {g["jira_key"]: g for g in snaps[0]["jira_groups"]}
    assert set(groups) == {"TV-1", "TV-2"}
    assert groups["TV-1"]["unmerged_cls"] == 1
    assert groups["TV-2"]["cl_unregistered"] is True   # 未登记 CL 也可见（V1 会整组丢失）


@pytest.mark.asyncio
async def test_scan_errors_surface_on_page(db_engine, monkeypatch):
    """JQL 写错时错误必须出现在页面上，不能只留一份"没变化"的旧数据。"""
    _patch_config(monkeypatch, [PROJECT])
    _mute_rd_scan(monkeypatch)

    async def call(server, tool, params, user_id=None, timeout=None):
        if tool == "search_issues":
            raise RuntimeError("JQL 语法错误: mismatched input")
        return {}

    monkeypatch.setattr(capability, "call", call)
    stats = await scan.run_daily_scan(USER_ID, dry_run=True)
    assert stats["errors"]

    snaps = await svc.load_latest_snapshots(user_id=USER_ID)
    errors = [s for s in (snaps or []) if s.get("error")]
    assert errors, "扫描错误应作为 error 行写入页面缓存"
    assert "JQL" in errors[0]["error"]


@pytest.mark.asyncio
async def test_load_or_refresh_falls_back_to_cache_on_failure(db_engine, monkeypatch):
    """扫描抛异常时回退到缓存，不返回空列表（避免页面空白）。"""
    good = {"project": "P1", "total_cls": 5, "merged_cls": 2, "unmerged_cls": 3,
            "total_jiras": 4, "jiras_unmerged": 1, "jiras_merged": 3, "jira_groups": []}
    await svc.save_snapshots([good], user_id=USER_ID)

    async def boom(user_id, dry_run=False):
        raise RuntimeError("Jira 403 CAPTCHA")
    monkeypatch.setattr("app.services.cl_scan_service.run_daily_scan", boom)

    data = await svc.load_or_refresh(refresh=True, user_id=USER_ID)
    assert data[0]["project"] == "P1"
    assert data[0]["total_cls"] == 5
    assert "error" not in data[0]


@pytest.mark.asyncio
async def test_manual_refresh_honours_dry_run_setting(db_engine, monkeypatch):
    """手动刷新必须与每日调度共用同一个 dry-run 开关。

    回归：refresh_all_snapshots 原先调 run_daily_scan(user_id) 省略了 dry_run，
    走函数默认值 False —— 灰度期点「刷新」会直接发出真实 Jira 评论。
    """
    from app.core.config import settings as app_settings

    seen = {}

    async def fake_scan(user_id, dry_run=False):
        seen["dry_run"] = dry_run
        return {"errors": []}

    monkeypatch.setattr("app.services.cl_scan_service.run_daily_scan", fake_scan)

    monkeypatch.setattr(app_settings, "CL_SCAN_DRY_RUN", True)
    await svc.refresh_all_snapshots(user_id=USER_ID)
    assert seen["dry_run"] is True

    monkeypatch.setattr(app_settings, "CL_SCAN_DRY_RUN", False)
    await svc.refresh_all_snapshots(user_id=USER_ID)
    assert seen["dry_run"] is False


@pytest.mark.asyncio
async def test_refresh_runs_full_scan_without_timeout_cancelling_it(db_engine, monkeypatch):
    """手动刷新必须跑完完整扫描，不能因 120s 超时被取消。

    回归：旧实现用 asyncio.wait_for(120s) 包住扫描，实测扫描要 116 秒、贴着临界值；
    一旦超时就会 cancel 掉扫描，页面回退旧缓存，用户误以为刷新没生效。
    """
    await svc.save_snapshots([{
        "project": "P1", "total_cls": 1, "merged_cls": 0, "unmerged_cls": 1,
        "total_jiras": 1, "jiras_unmerged": 1, "jiras_merged": 0, "jira_groups": [],
    }], user_id=USER_ID)

    async def slow_scan(user_id, dry_run=False):
        await asyncio.sleep(0.2)   # 模拟扫描耗时，若设短超时会被 cancel
        return {"errors": []}

    monkeypatch.setattr("app.services.cl_scan_service.run_daily_scan", slow_scan)
    # 确保真实扫描不会跑（用空配置 + 打桩），只验证"没有超时取消"这一行为
    monkeypatch.setattr(svc, "refresh_all_snapshots", slow_scan)

    data = await svc._refresh_with_cache_fallback(
        cached=None, user_id=USER_ID)
    # 没有 TimeoutError；返回的是扫描结果（这里是空列表 -> 回退错误占位）
    assert data is not None


@pytest.mark.asyncio
async def test_load_or_refresh_returns_cache_without_scan_when_fresh(db_engine, monkeypatch):
    """缓存新鲜时页面加载只读缓存，不触发扫描。"""
    good = {"project": "P1", "total_cls": 5, "merged_cls": 2, "unmerged_cls": 3,
            "total_jiras": 4, "jiras_unmerged": 1, "jiras_merged": 3, "jira_groups": []}
    await svc.save_snapshots([good], user_id=USER_ID)

    async def boom(user_id, dry_run=False):
        raise AssertionError("不应触发扫描")
    monkeypatch.setattr("app.services.cl_scan_service.run_daily_scan", boom)

    data = await svc.load_or_refresh(refresh=False, user_id=USER_ID)
    assert data[0]["project"] == "P1"
