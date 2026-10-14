"""
关注集合后台刷新 + 主动提醒服务测试（P0-1/P0-2）。

验证：成员快照刷新（数据保鲜）、到期未达标提醒 PM、达标主动提示、按日/状态去重。
capability.call（外部 Jira MCP）被 mock；FocusSet/Notification/UserSetting 持久化走真实测试 DB。
"""

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.models.notification import Notification
from app.services.focus_set_refresh_service import (
    _scan_and_notify,
    run_focus_set_cycle,
)
from app.services.focus_set_service import (
    create_or_update_focus_set,
    get_focus_set_notify_state,
    list_focus_sets,
    refresh_focus_set_members,
    update_weekly_baseline,
)

USER_ID = 1


def _issue(key: str, status: str = "Open",
           updated: str = "2026-08-10T10:00:00.000+0000",
           created: str = "2026-07-01T10:00:00.000+0000") -> dict:
    return {"key": key, "summary": f"s-{key}", "status": status,
            "priority": "P1", "assignee": "owner", "created": created,
            "updated": updated, "duedate": ""}


def _days(n: int) -> date:
    return date.today() + timedelta(days=n)


def _iso(days_ago: int) -> str:
    """生成 days_ago 天前的 Jira ISO 时间（含无冒号时区 +0000）。"""
    return (date.today() - timedelta(days=days_ago)).isoformat() + "T09:00:00.000+0000"


async def _create_set(name: str = "测试集合", deadline=None, target=None,
                      issues=None) -> int:
    """直接建关注集合（主表 + 成员快照），返回 focus_set id。"""
    fs = await create_or_update_focus_set(
        user_id=USER_ID, focus_type="filter", identifier=name, name=name,
        jql="project = TV",
        deadline=deadline,
        metric_type="fix_rate" if target is not None else None,
        metric_target=target,
    )
    await refresh_focus_set_members(
        fs.id, issues if issues is not None
        else [_issue("TV-1"), _issue("TV-2", "Resolved")],  # 默认 1/2 done
    )
    return fs.id


async def _notifications() -> list:
    """读取该用户全部站内消息。"""
    from app.db.session import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(Notification).where(Notification.user_id == USER_ID)
        )).scalars().all()
        return rows


def _fake_search(issues: list):
    """构造 mock capability.call：返回指定 issue 列表。"""
    async def _fake(server, tool, params, user_id=None, timeout=None):
        return {"issues": [
            {"key": iss["key"], "fields": {
                "summary": iss.get("summary", ""),
                "status": {"name": iss["status"]},
                "priority": {"name": iss.get("priority", "P2")},
                "assignee": iss.get("assignee", "owner"),
                "created": iss.get("created", "2026-07-01T10:00:00.000+0000"),
                "updated": iss.get("updated", "2026-08-10T10:00:00.000+0000"),
                "duedate": iss.get("duedate", ""),
            }} for iss in issues
        ]}
    return _fake


class TestMemberCreated:
    """成员 created（创建时间）字段持久化 + 返回。"""

    @pytest.mark.asyncio
    async def test_created_persisted_in_members(self, db_session):
        """创建/刷新成员时 created 落库，list_focus_sets 返回含 created。"""
        fsid = await _create_set("created集合", deadline=_days(5), target=0.8)
        sets = await list_focus_sets(USER_ID)
        fs = next(s for s in sets if s["id"] == fsid)
        for m in fs["members"]:
            assert m["created"] == "2026-07-01T10:00:00.000+0000"

    @pytest.mark.asyncio
    async def test_stagnant_members_carry_created(self, db_session):
        """停滞成员（停滞需推动列表）含 created，供前端展示创建时间。"""
        fsid = await _create_set("停滞created", deadline=_days(5), target=0.8)
        # 全部成员停滞（updated 很久前）
        from app.services.focus_set_service import refresh_focus_set_members
        await refresh_focus_set_members(fsid, [
            _issue("TV-1", "Open", updated="2026-07-01T10:00:00.000+0000"),
            _issue("TV-2", "Open", updated="2026-07-02T10:00:00.000+0000"),
        ])
        sets = await list_focus_sets(USER_ID)
        fs = next(s for s in sets if s["id"] == fsid)
        stagnant = fs["health"]["stagnant_members"]
        assert len(stagnant) == 2
        for m in stagnant:
            assert m["created"] == "2026-07-01T10:00:00.000+0000"
            assert m["assignee"] == "owner"
            assert m["stagnant_days"] > 0


class TestLastCommentTime:
    """停滞基准 = 最后评论时间（2026-08-13）：停滞天数 = 今天 − 最后评论时间。"""

    def _mock_comments(self, monkeypatch, created=None):
        """mock capability.call：get_issue_comments 返回指定评论时间（None 表示无评论）。"""
        from app.mcp.capability import capability

        async def fake_call(server, tool, params, user_id=None, timeout=None):
            if tool == "get_issue_comments":
                return {"comments": [{"created": created}]} if created else {"comments": []}
            return {"issues": []}
        monkeypatch.setattr(capability, "call", fake_call)

    @pytest.mark.asyncio
    async def test_comment_active_means_not_stagnant(self, db_session, monkeypatch):
        """updated 10 天前（粗判停滞候选），但最后评论 2 天前 → 有活跃评论 → 不停滞。"""
        from app.services.focus_set_service import refresh_focus_set_members
        fsid = await _create_set("评论活跃", deadline=_days(5), target=0.8)
        self._mock_comments(monkeypatch, created=_iso(2))

        await refresh_focus_set_members(fsid, [
            _issue("TV-1", "Open", updated=_iso(10)),
        ], user_id=USER_ID)

        sets = await list_focus_sets(USER_ID)
        fs = next(s for s in sets if s["id"] == fsid)
        assert fs["health"]["stagnant_members"] == []  # 评论 2 天前 → 活跃，不算停滞

    @pytest.mark.asyncio
    async def test_stagnant_days_from_last_comment(self, db_session, monkeypatch):
        """最后评论 15 天前 → 停滞天数 = 15（用评论，而非 updated 的 20）。"""
        from app.services.focus_set_service import refresh_focus_set_members
        fsid = await _create_set("评论停滞", deadline=_days(5), target=0.8)
        self._mock_comments(monkeypatch, created=_iso(15))

        await refresh_focus_set_members(fsid, [
            _issue("TV-1", "Open", updated=_iso(20)),
        ], user_id=USER_ID)

        sets = await list_focus_sets(USER_ID)
        fs = next(s for s in sets if s["id"] == fsid)
        sm = fs["health"]["stagnant_members"]
        assert len(sm) == 1
        assert sm[0]["stagnant_days"] == 15
        assert sm[0]["last_comment_time"] == _iso(15)

    @pytest.mark.asyncio
    async def test_no_comment_fallback_updated(self, db_session, monkeypatch):
        """无评论 → 停滞天数回退 updated（20 天）。"""
        from app.services.focus_set_service import refresh_focus_set_members
        fsid = await _create_set("无评论", deadline=_days(5), target=0.8)
        self._mock_comments(monkeypatch, created=None)

        await refresh_focus_set_members(fsid, [
            _issue("TV-1", "Open", updated=_iso(20)),
        ], user_id=USER_ID)

        sets = await list_focus_sets(USER_ID)
        fs = next(s for s in sets if s["id"] == fsid)
        sm = fs["health"]["stagnant_members"]
        assert len(sm) == 1
        assert sm[0]["stagnant_days"] == 20
        assert sm[0]["last_comment_time"] is None


class TestRefreshFocusSets:
    """P0-1 成员快照刷新（数据保鲜）。"""

    @pytest.mark.asyncio
    async def test_refresh_replaces_stale_members(self, db_session, monkeypatch):
        """后台刷新后成员更新：新增/移除/状态变更均反映到快照。"""
        from app.mcp.capability import capability
        fsid = await _create_set("刷新集合")

        # 场景：TV-1 已 Resolved，TV-2 移除，新增 TV-3
        monkeypatch.setattr(capability, "call", _fake_search([
            {"key": "TV-1", "status": "Resolved", "updated": "2026-08-11T10:00:00.000+0000"},
            {"key": "TV-3", "status": "Open", "updated": "2026-08-11T10:00:00.000+0000"},
        ]))

        stats = await run_focus_set_cycle(USER_ID)
        assert stats["refresh"]["total"] == 1
        assert stats["refresh"]["refreshed"] == 1

        sets = await list_focus_sets(USER_ID)
        fs = next(s for s in sets if s["id"] == fsid)
        keys = {m["key"] for m in fs["members"]}
        assert keys == {"TV-1", "TV-3"}           # TV-2 被移除、TV-3 新增
        assert fs["health"]["done"] == 1          # TV-1 Resolved
        assert fs["health"]["total"] == 2

    @pytest.mark.asyncio
    async def test_refresh_skips_manual_without_jql(self, db_session, monkeypatch):
        """无 jql 的 manual 集合跳过刷新（不抛错、不影响其他集合）。"""
        from app.mcp.capability import capability
        await create_or_update_focus_set(
            user_id=USER_ID, focus_type="manual", identifier="手动", name="手动",
            jql=None, metric_type=None, metric_target=None,
        )
        await _create_set("带jql集合")
        called = []

        async def _fake(server, tool, params, user_id=None, timeout=None):
            called.append(params.get("jql", ""))
            return {"issues": [_issue("TV-1")]}
        monkeypatch.setattr(capability, "call", _fake)

        stats = await run_focus_set_cycle(USER_ID)
        assert stats["refresh"]["total"] == 2      # 两个 active 集合
        assert stats["refresh"]["refreshed"] == 1  # 仅带 jql 的被刷新
        assert called == ["project = TV"]          # manual 无 jql 未触发拉取

    @pytest.mark.asyncio
    async def test_refresh_failure_isolated(self, db_session, monkeypatch):
        """单个集合刷新失败（MCP 异常）不中断整轮。"""
        from app.mcp.capability import capability
        await _create_set("集合A")
        await _create_set("集合B")

        async def _fake(server, tool, params, user_id=None, timeout=None):
            raise Exception("MCP timeout")
        monkeypatch.setattr(capability, "call", _fake)

        stats = await run_focus_set_cycle(USER_ID)
        assert stats["refresh"]["total"] == 2
        assert stats["refresh"]["refreshed"] == 0
        assert stats["refresh"]["failed"] == 2
        # 失败不中断 → notified 仍为 []（无异常上抛）


class TestScanAndNotify:
    """P0-2 到期未达标 / 达标提醒 PM。"""

    @pytest.mark.asyncio
    async def test_remind_when_due_not_met(self, db_session):
        """距截止 ≤3 天 + 未达标 → 站内消息提醒 PM。"""
        await _create_set("临期集合", deadline=_days(2), target=0.8)  # 1/2 = 50% < 80%
        notified = await _scan_and_notify(USER_ID)
        assert len(notified) == 1
        assert notified[0]["type"] == "due"

        rows = await _notifications()
        assert len(rows) == 1
        n = rows[0]
        assert n.type == "focus_set"
        assert "临期集合" in n.title and "距截止 2 天" in n.title
        assert n.link == "/dashboard"
        assert "差 1 个达标" in n.content

    @pytest.mark.asyncio
    async def test_remind_when_overdue(self, db_session):
        """已超期 + 未达标 → 提醒类型 overdue。"""
        await _create_set("超期集合", deadline=_days(-1), target=0.8)
        notified = await _scan_and_notify(USER_ID)
        assert notified[0]["type"] == "overdue"
        rows = await _notifications()
        assert "已超期" in rows[0].title

    @pytest.mark.asyncio
    async def test_remind_dedup_same_day(self, db_session):
        """同一集合同日多次扫描只提醒一次。"""
        await _create_set("临期集合", deadline=_days(1), target=0.8)
        await _scan_and_notify(USER_ID)
        notified2 = await _scan_and_notify(USER_ID)  # 同日再跑
        assert notified2 == []
        assert len(await _notifications()) == 1

    @pytest.mark.asyncio
    async def test_remind_again_next_day(self, db_session):
        """隔天（手动改 state）再次提醒。"""
        from app.services.focus_set_service import save_focus_set_notify_state
        await _create_set("临期集合", deadline=_days(1), target=0.8)
        await _scan_and_notify(USER_ID)

        # 模拟过了几天：把 last overdue_date 改成昨天
        state = await get_focus_set_notify_state(USER_ID)
        for entry in state.values():
            entry["overdue_date"] = _days(-1).isoformat()
        await save_focus_set_notify_state(USER_ID, state)

        notified = await _scan_and_notify(USER_ID)
        assert len(notified) == 1
        assert len(await _notifications()) == 2  # 两天各一条

    @pytest.mark.asyncio
    async def test_notify_achieved_once(self, db_session):
        """达标 → 提示一次；再扫描不重复；重新未达标可再次提示。"""
        await _create_set("达标集合", deadline=_days(5), target=0.8,
                          issues=[_issue("TV-1", "Resolved"), _issue("TV-2", "Closed")])  # 2/2
        notified = await _scan_and_notify(USER_ID)
        assert notified[0]["type"] == "achieved"
        rows = await _notifications()
        assert len(rows) == 1 and "已达标" in rows[0].title

        # 再扫描不重复
        assert await _scan_and_notify(USER_ID) == []
        assert len(await _notifications()) == 1

        # 集合退化为未达标（成员变 Open）→ achieved 重置；达标后应再次提示
        fsid = (await list_focus_sets(USER_ID))[0]["id"]
        await refresh_focus_set_members(fsid, [_issue("TV-1", "Open"), _issue("TV-2", "Open")])
        assert await _scan_and_notify(USER_ID) == []          # 未到期不提醒
        await refresh_focus_set_members(fsid, [_issue("TV-1", "Resolved"), _issue("TV-2", "Closed")])
        notified2 = await _scan_and_notify(USER_ID)
        assert len(notified2) == 1 and notified2[0]["type"] == "achieved"
        assert len(await _notifications()) == 2

    @pytest.mark.asyncio
    async def test_no_remind_when_not_due(self, db_session):
        """未到期（>3 天）且未达标 → 不提醒。"""
        await _create_set("远期集合", deadline=_days(10), target=0.8)
        assert await _scan_and_notify(USER_ID) == []
        assert await _notifications() == []

    @pytest.mark.asyncio
    async def test_no_remind_without_deadline_or_metric(self, db_session):
        """无 deadline 且无 metric_target → 不提醒（无法判定到期/达标）。"""
        await _create_set("无参数集合", deadline=None, target=None)
        assert await _scan_and_notify(USER_ID) == []
        assert await _notifications() == []

    @pytest.mark.asyncio
    async def test_full_cycle_with_reminder(self, db_session, monkeypatch):
        """完整一轮（refresh + remind）：刷新后用新数据触发到期提醒。"""
        from app.mcp.capability import capability
        await _create_set("完整集合", deadline=_days(1), target=0.8)  # 1/2 done

        # 刷新返回 0/2 done → 未达标 + 临期 → 提醒
        monkeypatch.setattr(capability, "call", _fake_search([
            {"key": "TV-1", "status": "Open", "updated": "2026-08-11T10:00:00.000+0000"},
            {"key": "TV-2", "status": "Open", "updated": "2026-08-11T10:00:00.000+0000"},
        ]))

        stats = await run_focus_set_cycle(USER_ID)
        assert stats["refresh"]["refreshed"] == 1
        assert len(stats["notified"]) == 1
        assert stats["notified"][0]["type"] == "due"
        rows = await _notifications()
        assert len(rows) == 1
        assert "差 2 个达标" in rows[0].content


class TestEpicBaselineRefresh:
    """Epic 周基线刷新与提醒。"""

    @pytest.mark.asyncio
    async def test_epic_baseline_refreshed_on_monday(self, db_session, monkeypatch):
        """周一刷新 epic 集合时，基线总数重置为当前成员数。"""
        from app.mcp.capability import capability
        fs = await create_or_update_focus_set(
            user_id=USER_ID, focus_type="epic", identifier="TV-918177", name="epic测试",
        )

        async def _fake(server, tool, params, user_id=None, timeout=None):
            return {"issues": [
                {"key": "TV-1", "fields": {
                    "summary": "s1", "status": {"name": "Open"}, "priority": {"name": "P1"},
                    "assignee": "owner", "created": "2026-07-01T10:00:00.000+0000",
                    "updated": "2026-08-10T10:00:00.000+0000", "duedate": "",
                }},
                {"key": "TV-2", "fields": {
                    "summary": "s2", "status": {"name": "Resolved"}, "priority": {"name": "P2"},
                    "assignee": "owner", "created": "2026-07-01T10:00:00.000+0000",
                    "updated": "2026-08-10T10:00:00.000+0000", "duedate": "",
                }},
            ]}
        monkeypatch.setattr(capability, "call", _fake)

        # mock 今天是周一（review #4/#5：业务日期统一经 focus_set_service._today()
        # 取北京时区口径，mock 它比 mock date 类更准确）
        def _fake_today():
            return date(2026, 8, 10)  # 周一

        monkeypatch.setattr("app.services.focus_set_service._today", _fake_today)

        stats = await run_focus_set_cycle(USER_ID)
        assert stats["refresh"]["refreshed"] == 1

        sets = await list_focus_sets(USER_ID)
        fs_out = next(s for s in sets if s["id"] == fs.id)
        assert fs_out["health"]["baseline_total"] == 2
        assert fs_out["health"]["baseline_date"] == "2026-08-10"

    @pytest.mark.asyncio
    async def test_epic_reminder_uses_baseline(self, db_session, monkeypatch):
        """epic 到期提醒文案使用本周起始基线口径。"""
        from app.mcp.capability import capability
        fs = await create_or_update_focus_set(
            user_id=USER_ID, focus_type="epic", identifier="TV-918177", name="epic提醒",
            jql='"Epic Link" = TV-918177', deadline=_days(2), metric_type="fix_rate", metric_target=0.8,
        )
        await refresh_focus_set_members(fs.id, [
            _issue("TV-1", "Resolved"), _issue("TV-2", "Open"),
            _issue("TV-3", "Open"), _issue("TV-4", "Open"), _issue("TV-5", "Open"),
        ])
        await update_weekly_baseline(fs.id, [
            {"key": "TV-1", "status": "Open"}, {"key": "TV-2", "status": "Open"},
            {"key": "TV-3", "status": "Open"}, {"key": "TV-4", "status": "Open"},
            {"key": "TV-5", "status": "Open"},
        ])

        monkeypatch.setattr(capability, "call", _fake_search([]))
        notified = await _scan_and_notify(USER_ID)
        assert len(notified) == 1
        rows = await _notifications()
        assert len(rows) == 1
        assert "本周起始 5 个" in rows[0].content
        assert "还差 3 个达标" in rows[0].content


class TestReviewFixesIntegration:
    """2026-09-09 review 修复集成测试（#1/#2/#5/#8）。"""

    @pytest.mark.asyncio
    async def test_truncation_detected_when_total_exceeds_fetched(self, db_session, monkeypatch):
        """#1 服务端 total > 实际返回数 → truncated=True 不静默。"""
        from app.mcp.capability import capability
        from app.services.focus_set_service import fetch_members_with_meta

        async def _fake(server, tool, params, user_id=None, timeout=None):
            # 服务端只返回 3 条但告知 total=10（且第 2 页重复 → 不支持分页）
            return {"total": 10, "issues": [
                {"key": f"TV-{i}", "fields": {"summary": f"s{i}", "status": {"name": "Open"}}}
                for i in range(3)
            ]}

        monkeypatch.setattr(capability, "call", _fake)
        issues, meta = await fetch_members_with_meta(USER_ID, "project = TV")
        assert len(issues) == 3
        assert meta["total"] == 10
        assert meta["truncated"] is True

    @pytest.mark.asyncio
    async def test_no_truncation_when_full(self, db_session, monkeypatch):
        """#1 total == fetched → truncated=False。"""
        from app.mcp.capability import capability
        from app.services.focus_set_service import fetch_members_with_meta

        async def _fake(server, tool, params, user_id=None, timeout=None):
            return {"total": 2, "issues": [
                {"key": f"TV-{i}", "fields": {"summary": f"s{i}", "status": {"name": "Open"}}}
                for i in range(2)
            ]}

        monkeypatch.setattr(capability, "call", _fake)
        issues, meta = await fetch_members_with_meta(USER_ID, "project = TV")
        assert len(issues) == 2
        assert meta["truncated"] is False

    @pytest.mark.asyncio
    async def test_baseline_rebuilt_when_stale(self, db_session, monkeypatch):
        """#5 基线日期早于最近一个周一（周一刷新失败过）→ 本轮自动重建。"""
        fs = await create_or_update_focus_set(
            user_id=USER_ID, focus_type="epic", identifier="TV-300001", name="epic基线",
        )
        # 预置一个"上周"的过期基线
        async with db_session as db:
            from app.models.focus_set import FocusSet
            row = await db.get(FocusSet, fs.id)
            row.baseline_total = 99
            row.baseline_date = date.today() - timedelta(days=9)  # 必然早于本周一
            await db.commit()

        # mock 今天为周二（确保"最近周一"在基线日期之后）
        real_today = date.today()
        monday = real_today - timedelta(days=real_today.weekday())
        fake_today = monday + timedelta(days=1)  # 周二

        monkeypatch.setattr("app.services.focus_set_service._today", lambda: fake_today)

        await update_weekly_baseline(fs.id, [_issue("TV-1"), _issue("TV-2")])

        sets = await list_focus_sets(USER_ID)
        fs_out = next(s for s in sets if s["id"] == fs.id)
        assert fs_out["health"]["baseline_total"] == 2  # 重建为当前成员数
        assert fs_out["health"]["baseline_date"] == fake_today.isoformat()

    @pytest.mark.asyncio
    async def test_baseline_kept_when_current_week(self, db_session, monkeypatch):
        """#5 本周已建基线 → 不重复重建。"""
        fs = await create_or_update_focus_set(
            user_id=USER_ID, focus_type="epic", identifier="TV-300002", name="epic本周",
        )
        real_today = date.today()
        monday = real_today - timedelta(days=real_today.weekday())
        fake_today = monday + timedelta(days=2)  # 周三

        async with db_session as db:
            from app.models.focus_set import FocusSet
            row = await db.get(FocusSet, fs.id)
            row.baseline_total = 5
            row.baseline_date = monday  # 本周一已建
            await db.commit()

        monkeypatch.setattr("app.services.focus_set_service._today", lambda: fake_today)
        await update_weekly_baseline(fs.id, [_issue("TV-1")])

        sets = await list_focus_sets(USER_ID)
        fs_out = next(s for s in sets if s["id"] == fs.id)
        assert fs_out["health"]["baseline_total"] == 5  # 保留未重建

    @pytest.mark.asyncio
    async def test_comment_time_reused_when_updated_unchanged(self, db_session, monkeypatch):
        """#2 成员 updated 未变 → 复用旧评论时间，不再调 MCP 拉评论。"""
        from app.mcp.capability import capability

        fs = await create_or_update_focus_set(
            user_id=USER_ID, focus_type="filter", identifier="reuse-test", name="复用",
            jql="project = TV",
        )
        stale_updated = _iso(30)  # 30 天前 → 停滞候选
        # 第 1 次刷新：拉评论（mock 返回评论时间）
        async def _fake_with_comment(server, tool, params, user_id=None, timeout=None):
            return {"comments": [{"created": "2026-08-20T10:00:00.000+0800"}]}

        monkeypatch.setattr(capability, "call", _fake_with_comment)
        await refresh_focus_set_members(
            fs.id, [_issue("TV-9", updated=stale_updated)], user_id=USER_ID,
        )

        from app.models.focus_set import FocusSetMember
        async with db_session as db:
            m = (await db.execute(
                select(FocusSetMember).where(FocusSetMember.focus_set_id == fs.id)
            )).scalar_one()
            assert m.last_comment_time == "2026-08-20T10:00:00.000+0800"

        # 第 2 次刷新：updated 不变 → 不应再调 capability
        calls = []

        async def _spy(server, tool, params, user_id=None, timeout=None):
            calls.append(tool)
            return {"comments": []}

        monkeypatch.setattr(capability, "call", _spy)
        await refresh_focus_set_members(
            fs.id, [_issue("TV-9", updated=stale_updated)], user_id=USER_ID,
        )
        assert calls == []  # 零 MCP 调用——评论时间复用

        async with db_session as db:
            m = (await db.execute(
                select(FocusSetMember).where(FocusSetMember.focus_set_id == fs.id)
            )).scalar_one()
            assert m.last_comment_time == "2026-08-20T10:00:00.000+0800"  # 复用保留

    @pytest.mark.asyncio
    async def test_multi_goal_epic_sets_are_independent(self, db_session, monkeypatch):
        """#8 同一 epic 带不同 #goal 后缀 → 两个独立集合，互不 upsert 覆盖。"""
        from app.mcp.capability import capability

        async def _fake(server, tool, params, user_id=None, timeout=None):
            return {"total": 1, "issues": [
                {"key": "TV-1", "fields": {"summary": "s", "status": {"name": "Open"}}},
            ]}

        monkeypatch.setattr(capability, "call", _fake)

        fs1 = await create_or_update_focus_set(
            user_id=USER_ID, focus_type="epic", identifier="TV-400001#week80",
            name="epic周目标", jql='"Epic Link" = TV-400001', metric_target=0.8,
        )
        fs2 = await create_or_update_focus_set(
            user_id=USER_ID, focus_type="epic", identifier="TV-400001#month100",
            name="epic月目标", jql='"Epic Link" = TV-400001', metric_target=1.0,
        )
        assert fs1.id != fs2.id
        # 再下发同目标 → upsert 同一行（不新增）
        fs1b = await create_or_update_focus_set(
            user_id=USER_ID, focus_type="epic", identifier="TV-400001#week80",
            name="epic周目标", jql='"Epic Link" = TV-400001', metric_target=0.8,
        )
        assert fs1b.id == fs1.id
