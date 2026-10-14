"""通用定时任务引擎 + declarative_rule handler 测试（2026-09-09）。

验证：config 校验 / 创建与调度初始化 / run_due_tasks 执行闭环（mock MCP 三连：
search_issues + get_issue_comments + add_comment）/ 去重 / remind_interval_days /
到期自动停用 + 通知 / 未到期不执行 / 单任务失败隔离 / 模板渲染。
"""

from datetime import date, timedelta

import pytest

from app.services import scheduled_task_handlers  # noqa: F401 — 触发 handler 自注册
from app.services.scheduled_task_service import (
    create_scheduled_task,
    delete_scheduled_task,
    list_scheduled_tasks,
    run_due_tasks,
    update_scheduled_task,
    validate_config,
)

USER_ID = 1

GOOD_CONFIG = {
    "source": {"kind": "jira_jql", "jql": "filter = 61364"},
    "conditions": [
        {"kind": "status_not_in", "statuses": ["Verified", "Closed"]},
        {"kind": "stale_days", "days": 3, "basis": "last_comment"},
    ],
    "action": {
        "kind": "jira_comment",
        "template": "[~{owner}] {key} 已 {stale_days} 天无更新，请尽快处理",
        "remind_interval_days": 3,
    },
}


def _stale_iso(days_ago: int) -> str:
    return (date.today() - timedelta(days=days_ago)).isoformat() + "T09:00:00.000+0000"


def _issue(key: str, status: str = "Open", updated: str = "", assignee: str = "Kai.Qin") -> dict:
    return {"key": key, "summary": f"s-{key}", "status": status, "priority": "P1",
            "assignee": assignee, "created": _stale_iso(40),
            "updated": updated or _stale_iso(10), "duedate": ""}


def _mock_mcp(monkeypatch, issues, comments=None):
    """mock capability.call：search_issues 返回成员；get_issue_comments 返回评论时间。"""
    from app.mcp.capability import capability

    add_calls: list = []

    async def _fake(server, tool, params, user_id=None, timeout=None):
        if tool == "search_issues":
            return {"total": len(issues), "issues": [
                {"key": i["key"], "fields": {
                    "summary": i["summary"], "status": {"name": i["status"]},
                    "priority": {"name": i["priority"]}, "assignee": i["assignee"],
                    "created": i["created"], "updated": i["updated"], "duedate": i["duedate"],
                }} for i in issues]}
        if tool == "get_issue_comments":
            ts = (comments or {}).get(params.get("issue_key"))
            return {"comments": [{"created": ts}] if ts else {"comments": []}}
        if tool == "add_comment":
            add_calls.append(params)
            return {"success": True}
        return {}

    monkeypatch.setattr(capability, "call", _fake)
    return add_calls


class TestValidateConfig:
    """声明式 config 校验。"""

    def test_good_config_passes(self):
        assert validate_config("declarative_rule", GOOD_CONFIG) is None

    def test_empty_config_rejected(self):
        assert validate_config("declarative_rule", {}) is not None

    def test_source_requires_jql(self):
        bad = {"source": {"kind": "jira_jql"}, "conditions": [{"kind": "stale_days", "days": 3}],
               "action": {"kind": "jira_comment", "template": "x"}}
        assert "source" in validate_config("declarative_rule", bad)

    def test_condition_kind_whitelist(self):
        bad = {"source": {"kind": "jira_jql", "jql": "x"},
               "conditions": [{"kind": "delete_all_issues"}], "action": {"kind": "jira_comment", "template": "x"}}
        assert "未知条件" in validate_config("declarative_rule", bad)

    def test_stale_days_bounds(self):
        for days in (0, 91):
            bad = {"source": {"kind": "jira_jql", "jql": "x"},
                   "conditions": [{"kind": "stale_days", "days": days}],
                   "action": {"kind": "jira_comment", "template": "x"}}
            assert validate_config("declarative_rule", bad) is not None

    def test_action_requires_template(self):
        bad = {"source": {"kind": "jira_jql", "jql": "x"},
               "conditions": [{"kind": "stale_days", "days": 3}],
               "action": {"kind": "jira_comment"}}
        assert validate_config("declarative_rule", bad) is not None


class TestCrud:
    """CRUD 与调度初始化。"""

    @pytest.mark.asyncio
    async def test_create_initializes_next_run(self, db_session):
        t = await create_scheduled_task(USER_ID, "卡闭环", GOOD_CONFIG, interval_minutes=30)
        assert t["id"] > 0
        assert t["next_run_at"] is not None  # 创建后即可被执行
        assert t["active"] is True

    @pytest.mark.asyncio
    async def test_create_rejects_invalid_config(self, db_session):
        with pytest.raises(ValueError):
            await create_scheduled_task(USER_ID, "坏配置", {"foo": "bar"})

    @pytest.mark.asyncio
    async def test_update_toggle_resets_schedule(self, db_session):
        t = await create_scheduled_task(USER_ID, "启停测试", GOOD_CONFIG)
        off = await update_scheduled_task(USER_ID, t["id"], active=False)
        assert off["active"] is False
        assert off["next_run_at"] is None
        on = await update_scheduled_task(USER_ID, t["id"], active=True)
        assert on["active"] is True and on["next_run_at"] is not None

    @pytest.mark.asyncio
    async def test_delete(self, db_session):
        t = await create_scheduled_task(USER_ID, "删除测试", GOOD_CONFIG)
        assert await delete_scheduled_task(USER_ID, t["id"]) is True
        assert all(x["id"] != t["id"] for x in await list_scheduled_tasks(USER_ID))

    @pytest.mark.asyncio
    async def test_update_cross_user_denied(self, db_session):
        """user 2 不能改 user 1 的任务（返回 None）。"""
        t = await create_scheduled_task(USER_ID, "越权测试", GOOD_CONFIG)
        assert await update_scheduled_task(2, t["id"], active=False) is None
        assert await delete_scheduled_task(2, t["id"]) is False


class TestRunDueTasks:
    """引擎执行闭环。"""

    @pytest.mark.asyncio
    async def test_executes_and_nudges(self, db_session, monkeypatch):
        """停滞 Open issue → add_comment 发出，模板变量替换正确，state 去重写入。"""
        issues = [_issue("TV-100", "Open", _stale_iso(10), "Kai.Qin"),
                  _issue("TV-101", "Verified", _stale_iso(10))]  # Verified 被排除
        # TV-100 最后评论 5 天前（>3 天停滞）；TV-101 不拉评论（被状态条件先排除）
        adds = _mock_mcp(monkeypatch, issues, comments={"TV-100": _stale_iso(5)})

        t = await create_scheduled_task(USER_ID, "执行测试", GOOD_CONFIG, interval_minutes=60)
        summary = await run_due_tasks()
        assert summary["ran"] == 1

        assert len(adds) == 1  # 仅 TV-100
        assert adds[0]["issue_key"] == "TV-100"
        assert "[~Kai.Qin]" in adds[0]["comment"]
        assert "TV-100" in adds[0]["comment"]
        assert "5 天" in adds[0]["comment"] or "5天" in adds[0]["comment"]

        tasks = await list_scheduled_tasks(USER_ID)
        tt = next(x for x in tasks if x["id"] == t["id"])
        assert tt["run_count"] == 1
        assert tt["last_run_status"].startswith("ok:")
        assert tt["next_run_at"] > tt["last_run_at"]  # 已排下一次

    @pytest.mark.asyncio
    async def test_dedup_skips_recently_reminded(self, db_session, monkeypatch):
        """state 里今日已提醒 → 不再 add_comment。"""
        issues = [_issue("TV-200", "Open", _stale_iso(10))]
        adds = _mock_mcp(monkeypatch, issues, comments={"TV-200": _stale_iso(6)})

        t = await create_scheduled_task(USER_ID, "去重测试", GOOD_CONFIG)
        # 预置 state：TV-200 今天已提醒
        async with db_session as db:
            from app.models.scheduled_task import ScheduledTask
            row = await db.get(ScheduledTask, t["id"])
            row.state = {"TV-200": date.today().isoformat()}
            await db.commit()

        await run_due_tasks()
        assert adds == []  # 去重跳过

    @pytest.mark.asyncio
    async def test_remind_interval_days(self, db_session, monkeypatch):
        """3 天前去重间隔=3 → 昨天提醒过的今天跳过。"""
        issues = [_issue("TV-300", "Open", _stale_iso(10))]
        adds = _mock_mcp(monkeypatch, issues, comments={"TV-300": _stale_iso(8)})

        t = await create_scheduled_task(USER_ID, "间隔测试", GOOD_CONFIG)
        async with db_session as db:
            from app.models.scheduled_task import ScheduledTask
            row = await db.get(ScheduledTask, t["id"])
            row.state = {"TV-300": (date.today() - timedelta(days=1)).isoformat()}
            await db.commit()

        await run_due_tasks()
        assert adds == []  # 1 天前提醒过 < 3 天间隔

    @pytest.mark.asyncio
    async def test_expired_task_stops_with_notification(self, db_session, monkeypatch):
        """run_until 已过 → active=False + 站内通知，不执行。"""
        issues = [_issue("TV-400", "Open", _stale_iso(10))]
        adds = _mock_mcp(monkeypatch, issues)

        await create_scheduled_task(
            USER_ID, "到期测试", GOOD_CONFIG,
            run_until=date.today() - timedelta(days=1),
        )
        summary = await run_due_tasks()
        assert summary["expired"] == 1
        assert adds == []

        from sqlalchemy import select
        from app.models.notification import Notification
        async with db_session as db:
            notifs = (await db.execute(
                select(Notification).where(Notification.user_id == USER_ID)
            )).scalars().all()
        assert any("到期测试" in (n.title or "") for n in notifs)
        tasks = await list_scheduled_tasks(USER_ID)
        expired = next(x for x in tasks if x["name"] == "到期测试")
        assert expired["active"] is False

    @pytest.mark.asyncio
    async def test_future_task_not_run(self, db_session, monkeypatch):
        """next_run_at 在未来 → 不执行。"""
        issues = [_issue("TV-500", "Open", _stale_iso(10))]
        adds = _mock_mcp(monkeypatch, issues)

        t = await create_scheduled_task(USER_ID, "未到期测试", GOOD_CONFIG)
        async with db_session as db:
            from datetime import datetime
            from app.services.focus_set_service import _BJS_TZ
            from app.models.scheduled_task import ScheduledTask
            row = await db.get(ScheduledTask, t["id"])
            row.next_run_at = datetime.now(_BJS_TZ) + timedelta(hours=2)
            await db.commit()

        summary = await run_due_tasks()
        assert summary["ran"] == 0
        assert adds == []

    @pytest.mark.asyncio
    async def test_one_task_failure_does_not_block_others(self, db_session, monkeypatch):
        """任务 A 的 MCP 抛异常 → A 记 error 状态，B 正常执行。"""
        from app.mcp.capability import capability

        adds_b: list = []

        async def _fake(server, tool, params, user_id=None, timeout=None):
            if tool == "search_issues":
                if params["jql"] == "filter = 61364":  # 任务 A：拉取失败
                    raise Exception("MCP 炸了")
                # 任务 B：TV-600 停滞且无评论（回退 updated）
                return {"total": 1, "issues": [
                    {"key": "TV-600", "fields": {"summary": "s", "status": {"name": "Open"},
                     "priority": {"name": "P1"}, "assignee": "Kai.Qin",
                     "created": _stale_iso(40), "updated": _stale_iso(10), "duedate": ""}},
                ]}
            if tool == "get_issue_comments":
                return {"comments": []}
            if tool == "add_comment":
                adds_b.append(params)
                return {"success": True}
            return {}

        monkeypatch.setattr(capability, "call", _fake)

        cfg_a = dict(GOOD_CONFIG, source={"kind": "jira_jql", "jql": "filter = 61364"})
        cfg_b = dict(GOOD_CONFIG, source={"kind": "jira_jql", "jql": "project = TV"})
        await create_scheduled_task(USER_ID, "失败任务A", cfg_a)
        await create_scheduled_task(USER_ID, "正常任务B", cfg_b)

        summary = await run_due_tasks()
        assert summary["ran"] == 1
        assert summary["failed"] == 1
        assert len(adds_b) == 1  # B 正常

        tasks = {t["name"]: t for t in await list_scheduled_tasks(USER_ID)}
        assert tasks["失败任务A"]["last_run_status"].startswith("error:")
        assert tasks["正常任务B"]["last_run_status"].startswith("ok:")

    @pytest.mark.asyncio
    async def test_template_omits_mention_for_non_username(self, db_session, monkeypatch):
        """assignee 是 displayName（含空格）→ 省略 [~]，评论照发。"""
        issues = [_issue("TV-700", "Open", _stale_iso(10), "Test User")]
        adds = _mock_mcp(monkeypatch, issues, comments={"TV-700": _stale_iso(9)})

        await create_scheduled_task(USER_ID, "mention测试", GOOD_CONFIG)
        await run_due_tasks()
        assert len(adds) == 1
        assert "[~]" not in adds[0]["comment"]  # 空 mention 已清理
        assert "TV-700" in adds[0]["comment"]


class TestUpdateConfig:
    """编辑功能：改 config 重新校验 + 重置去重状态。"""

    @pytest.mark.asyncio
    async def test_update_config_resets_state(self, db_session):
        """改 config → state 去重置（旧条件的提醒记录对新条件无意义）。"""
        t = await create_scheduled_task(USER_ID, "改配置测试", GOOD_CONFIG)
        # 预置 state：TV-100 今日已提醒
        async with db_session as db:
            from app.models.scheduled_task import ScheduledTask
            row = await db.get(ScheduledTask, t["id"])
            row.state = {"TV-100": date.today().isoformat()}
            await db.commit()

        new_cfg = dict(GOOD_CONFIG, conditions=[
            {"kind": "status_not_in", "statuses": ["Closed"]},  # 改条件
            {"kind": "stale_days", "days": 7, "basis": "last_comment"},
        ])
        updated = await update_scheduled_task(USER_ID, t["id"], config=new_cfg)
        assert updated["config"]["conditions"][0]["statuses"] == ["Closed"]

        async with db_session as db:
            from app.models.scheduled_task import ScheduledTask
            row = await db.get(ScheduledTask, t["id"])
            assert row.state == {}  # 去重状态已重置

    @pytest.mark.asyncio
    async def test_update_config_invalid_rejected(self, db_session):
        """改 config 传非法配置 → ValueError，且原 config 不变。"""
        t = await create_scheduled_task(USER_ID, "非法改配置", GOOD_CONFIG)
        with pytest.raises(ValueError):
            await update_scheduled_task(USER_ID, t["id"], config={"bad": True})
        tasks = await list_scheduled_tasks(USER_ID)
        tt = next(x for x in tasks if x["id"] == t["id"])
        assert tt["config"]["source"]["jql"] == "filter = 61364"  # 原值未变
