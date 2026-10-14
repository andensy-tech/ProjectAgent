"""CL 监控 V2 追踪队列测试 — 状态机、扫描 diff、提醒规则。

对应 docs/design/cl_monitoring_v2.md 第七节用例 1~13。
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.mcp.capability import capability
from app.models.cl_jira_state import ClJiraState
from app.services import cl_scan_service as scan
from app.services import cl_queue_service as queue
from app.services.cl_scan_service import (
    apply_ack, apply_cl_result, apply_seen, build_reminder_text, clear_ack,
    needs_reminder, needs_cl_action, parse_issue_fields, run_daily_scan,
    should_exit_queue, should_refresh_cl,
)
from app.services.dashboard_service import build_cl_jql

NOW = datetime(2026, 9, 16, 3, 0, tzinfo=timezone.utc)


def _issue(key: str, status: str = "Resolved", summary: str = "s",
           assignee: str = "Kai.Qin", updated: str = "2026-09-16T10:00:00.000+0800") -> dict:
    return {"key": key, "fields": {
        "summary": summary, "assignee": assignee, "updated": updated,
        "status": {"name": status},
    }}


def _state(**kw) -> ClJiraState:
    """构造未落库的队列记录（纯函数测试用）。"""
    base = dict(user_id=1, issue_key="TV-1", project_name="P1", status="",
                prev_status="", queued=True, archived=False, cl_total=0,
                cl_merged=0, cl_unmerged=0, cl_noregister=False,
                cl_items_json="[]", fail_count=0)
    base.update(kw)
    return ClJiraState(**base)


class TestBuildClJql:
    """用例 1/2：JQL 原样执行，advanced_jql 优先，不再追加状态条件。"""

    def test_advanced_jql_used_verbatim(self):
        jql = ('status not in (Closed) AND "Project ID" = "PROJECTX-CHIP-X" '
               'AND issuetype not in (Epic)')
        assert build_cl_jql({"advanced_jql": jql}) == jql

    def test_advanced_jql_wins_over_labels(self):
        """回归：UI 显示 JQL 模式但残留 labels 时，必须用 JQL（旧实现会回退到 labels）。"""
        proj = {
            "advanced_jql": 'status not in (Closed) AND "Project ID" = "PROJECTX-CHIP-X"',
            "labels": "oem_release_tv",
            "jira_project_key": "TV",
            "epics": "TV-1",
        }
        assert build_cl_jql(proj) == proj["advanced_jql"]
        assert "oem_release_tv" not in build_cl_jql(proj)

    def test_no_status_condition_appended(self):
        """cl_status_filter 已删除：不得再出现 status IN (...)。"""
        proj = {"advanced_jql": "project = TV", "cl_status_filter": "Verified,Resolved"}
        assert build_cl_jql(proj) == "project = TV"

    def test_basic_mode_when_no_advanced(self):
        proj = {"jira_project_key": "TV", "labels": "a,b"}
        assert build_cl_jql(proj) == 'project = TV AND (labels = "a" OR labels = "b")'

    def test_empty_returns_none(self):
        assert build_cl_jql({}) is None
        assert build_cl_jql({"advanced_jql": "   "}) is None


class TestParseIssueFields:
    def test_dict_status_and_assignee(self):
        info = parse_issue_fields(_issue("TV-1", "Verified", "s", "Test User"))
        assert info == {"key": "TV-1", "status": "Verified", "summary": "s",
                        "assignee": "Test User",
                        "updated": "2026-09-16T10:00:00.000+0800"}

    def test_plain_string_status(self):
        info = parse_issue_fields({"key": "TV-2", "fields": {"status": "Closed"}})
        assert info["status"] == "Closed"

    def test_missing_fields(self):
        assert parse_issue_fields({"key": "TV-3"})["status"] == ""


class TestApplySeen:
    """用例 3/4/11：入队、状态变化、首轮历史 Closed 归档。"""

    def test_first_scan_resolved_queued(self):
        state = _state(status="")
        outcome = apply_seen(state, parse_issue_fields(_issue("TV-1", "Resolved")),
                             first_scan=True, now=NOW)
        assert outcome == "new"
        assert state.queued is True
        assert state.status == "Resolved"
        assert state.archived is False

    def test_first_scan_closed_archived(self):
        """用例 11：首轮即 Closed 的历史题归档，不做 CL 确认。"""
        state = _state(status="")
        outcome = apply_seen(state, parse_issue_fields(_issue("TV-1", "Closed")),
                             first_scan=True, now=NOW)
        assert outcome == "new"
        assert state.archived is True
        assert should_refresh_cl(state, outcome, {"updated": "x"}) is False

    def test_status_transition_records_prev(self):
        state = _state(status="Resolved", cl_checked_at=NOW)
        outcome = apply_seen(state, parse_issue_fields(_issue("TV-1", "Verified")),
                             first_scan=False, now=NOW)
        assert outcome == "changed"
        assert state.prev_status == "Resolved"
        assert state.status == "Verified"
        assert state.status_changed_at == NOW

    def test_unchanged_status(self):
        state = _state(status="Resolved")
        outcome = apply_seen(state, parse_issue_fields(_issue("TV-1", "Resolved")),
                             first_scan=False, now=NOW)
        assert outcome == "unchanged"
        assert state.prev_status == ""

    def test_archived_wakes_on_transition(self):
        state = _state(status="Closed", archived=True)
        outcome = apply_seen(state, parse_issue_fields(_issue("TV-1", "In Progress")),
                             first_scan=False, now=NOW)
        assert outcome == "changed"
        assert state.archived is False


class TestShouldRefreshCl:
    """用例 12：只对焦点题查 CL；updated 未变且已查过则复用上次结果。"""

    def test_new_focus_issue_needs_check(self):
        assert should_refresh_cl(_state(status="Resolved", cl_checked_at=None),
                                 "new", {"updated": "u"})

    def test_changed_status_needs_check(self):
        assert should_refresh_cl(_state(status="Verified", cl_checked_at=NOW),
                                 "changed", {"updated": "u"})

    def test_non_focus_status_skips(self):
        """题还在 Open/In Progress 时不查 CL —— 首次入队省下七成 MCP 调用。"""
        assert should_refresh_cl(_state(status="In Progress", cl_checked_at=None),
                                 "new", {"updated": "u"}) is False
        assert should_refresh_cl(_state(status="To Do", cl_checked_at=None),
                                 "new", {"updated": "u"}) is False

    def test_closed_is_checked(self):
        """Closed 也要查：要判断"跳过验证直接关闭"时 CL 是否已合并。"""
        assert should_refresh_cl(_state(status="Closed", cl_checked_at=None),
                                 "changed", {"updated": "u"}) is True

    def test_unchanged_and_updated_same_skips(self):
        state = _state(status="Verified", cl_checked_at=NOW)
        assert should_refresh_cl(state, "unchanged", {"updated": "u1"}, "u1") is False

    def test_unchanged_but_updated_differs_checks(self):
        """有新评论（updated 变了）即使状态没变也要复查 CL。

        回归：prev_updated 必须由调用方在 apply_seen 之前取，否则永远相等 ——
        实测 TV-953261 因这条失效，CL 已合并 + 有新评论却一直停留在旧数据上。
        """
        state = _state(status="Verified", cl_checked_at=NOW)
        assert should_refresh_cl(state, "unchanged", {"updated": "u2"}, "u1") is True

    def test_apply_seen_overwrites_updated_so_caller_must_capture(self):
        """锁定"必须在 apply_seen 前取 prev_updated"这个约束。"""
        state = _state(status="Verified", cl_checked_at=NOW, issue_updated="u1")
        info = parse_issue_fields(_issue("TV-1", "Verified", updated="u2"))
        outcome = apply_seen(state, info, first_scan=False, now=NOW)
        # apply_seen 之后 state 已是 u2，用 state 自己比较必然为 False（旧 bug）
        assert should_refresh_cl(state, outcome, info, state.issue_updated) is False
        # 用真正的旧值比较才正确
        assert should_refresh_cl(state, outcome, info, "u1") is True

    def test_archived_never_checks(self):
        state = _state(status="Closed", cl_checked_at=None, archived=True)
        assert should_refresh_cl(state, "new", {"updated": "u"}) is False


class TestClResultAndExit:
    """用例 7/8：Closed 的退出条件与"仍要盯"的条件。"""

    def test_unregistered_marks_noregister(self):
        state = _state(status="Verified")
        apply_cl_result(state, None, NOW)
        assert state.cl_noregister is True
        assert needs_cl_action(state) is True

    def test_all_merged_not_closed_stays_queued(self):
        state = _state(status="Verified")
        apply_cl_result(state, {"items": [
            {"instance": "i", "change_id": "1", "status": "MERGED"}]}, NOW)
        assert state.cl_unmerged == 0
        assert state.cl_noregister is False
        assert needs_cl_action(state) is False
        assert should_exit_queue(state) is False  # 未 Closed -> 继续跟踪

    def test_closed_all_merged_exits(self):
        """用例 7：Closed 且 CL 全合并 -> 退出队列。"""
        state = _state(status="Closed")
        apply_cl_result(state, {"items": [
            {"instance": "i", "change_id": "1", "status": "MERGED"}]}, NOW)
        assert should_exit_queue(state) is True

    def test_closed_with_unmerged_stays(self):
        """用例 8：Closed 但 CL 未合并 -> 留在队列（进 PM summary）。"""
        state = _state(status="Closed")
        apply_cl_result(state, {"items": [
            {"instance": "i", "change_id": "1", "status": "NEW"}]}, NOW)
        assert should_exit_queue(state) is False
        assert needs_cl_action(state) is True

    def test_closed_unregistered_stays(self):
        state = _state(status="Closed")
        apply_cl_result(state, None, NOW)
        assert should_exit_queue(state) is False

    def test_chinese_status_counted_as_merged(self):
        state = _state(status="Closed")
        apply_cl_result(state, {"items": [
            {"instance": "i", "change_id": "1", "status": "已合并"}]}, NOW)
        assert state.cl_merged == 1
        assert should_exit_queue(state) is True

    def test_suppressed_cls_excluded(self):
        """标记"不关注"的 CL 不参与计数，也不再触发提醒。"""
        state = _state(status="Verified")
        apply_cl_result(state, {"items": [
            {"instance": "i", "change_id": "1", "status": "NEW"}]}, NOW,
            suppressed_keys={("i", "1", "TV-1")})
        assert state.cl_unmerged == 0
        assert state.cl_noregister is False  # 不是漏提，是明确无需关注


class TestReminderRules:
    """用例 9/10：提醒对象与频率。"""

    def test_resolved_reminds(self):
        assert needs_reminder(_state(status="Resolved"), NOW) == "Resolved"

    def test_verified_with_unmerged_reminds(self):
        state = _state(status="Verified", cl_total=1, cl_unmerged=1)
        assert needs_reminder(state, NOW) == "Verified"

    def test_verified_all_merged_silent(self):
        """Verified 且 CL 全部已合并 -> 不发提醒（无待办），但仍跟踪。"""
        state = _state(status="Verified", cl_total=1, cl_unmerged=0)
        assert needs_reminder(state, NOW) is None

    def test_closed_never_reminds_owner(self):
        """Closed 只进 PM summary，不评论 owner。"""
        state = _state(status="Closed", cl_total=1, cl_unmerged=1)
        assert needs_reminder(state, NOW) is None

    def test_daily_dedup(self):
        """用例 10：同一天内重复扫描不重复提醒。"""
        state = _state(status="Resolved", last_reminded_at=NOW - timedelta(hours=1))
        assert needs_reminder(state, NOW) is None

    def test_reminds_again_next_day(self):
        state = _state(status="Resolved", last_reminded_at=NOW - timedelta(hours=25))
        assert needs_reminder(state, NOW) == "Resolved"

    def test_archived_silent(self):
        assert needs_reminder(_state(status="Resolved", archived=True), NOW) is None

    def test_reminder_text_unregistered(self):
        """用例 9：未登记 CL 的文案区分于"有未合并 CL"。"""
        state = _state(status="Verified", cl_noregister=True)
        text = build_reminder_text(state, "Kai.Qin")
        assert "[~Kai.Qin]" in text
        assert "未在 Jira 中登记关联 CL" in text

    def test_reminder_text_lists_unmerged(self):
        state = _state(status="Verified", cl_total=1, cl_unmerged=1,
                       cl_items_json='[{"instance":"i","change_id":"99","status":"NEW","subject":"s"}]')
        text = build_reminder_text(state, "Kai.Qin")
        assert "未合并 CL" in text and "99" in text

    def test_reminder_text_resolved(self):
        text = build_reminder_text(_state(status="Resolved"), "Kai.Qin")
        assert "走验证流程" in text


class TestOwnerAck:
    """经办人已说明 CL 处理完毕 -> 停止提醒，但保留在队列供 PM 复核。"""

    def test_ack_stops_reminder_resolved(self):
        """Resolved 原本无条件提醒；有 ack 后不再提醒。"""
        assert needs_reminder(_state(status="Resolved"), NOW) == "Resolved"
        assert needs_reminder(_state(status="Resolved", ack_at=NOW), NOW) is None

    def test_ack_stops_reminder_verified_unregistered(self):
        """未登记 CL 的题同理：owner 说"本 Jira 不需要代码改动"后不再催。"""
        state = _state(status="Verified", cl_noregister=True)
        assert needs_reminder(state, NOW) == "Verified"
        state.ack_at = NOW
        assert needs_reminder(state, NOW) is None

    def test_ack_keeps_queue_membership(self):
        """ack 只停提醒，不退出队列（PM 仍要在汇总里看到）。"""
        state = _state(status="Closed", cl_total=1, cl_unmerged=1, ack_at=NOW)
        assert should_exit_queue(state) is False
        assert needs_reminder(state, NOW) is None

    def test_status_change_clears_ack(self):
        """状态再变化说明"已无待办"的结论不再成立 -> 重新武装提醒。"""
        state = _state(status="Verified", ack_at=NOW, ack_reason="owner 说都合并了")
        apply_seen(state, parse_issue_fields(_issue("TV-1", "Reopened")),
                   first_scan=False, now=NOW)
        assert state.ack_at is None
        assert state.ack_reason == ""

    def test_apply_and_clear_ack(self):
        state = _state(status="Verified")
        apply_ack(state, "owner 说明 CL 已全部合并", NOW)
        assert state.ack_at == NOW and state.ack_reason
        clear_ack(state)
        assert state.ack_at is None and state.ack_reason == ""


class TestClosedTrackingFlag:
    """回归：页面缓存必须给 Closed 组打 tracking，否则经办人在关闭后的回复不会被分析。

    V1 的 scan_and_merge_tracking 专门打过这个标（2026-09-04 修 TV-943550），
    V2 重建 payload 时漏了，导致 Closed 题被回复检测的门槛跳过。
    """

    def test_closed_group_marked_tracking(self):
        from app.services.cl_snapshot_service import build_project_payload
        payload = build_project_payload("P1", [
            _state(issue_key="TV-1", status="Closed"),
            _state(issue_key="TV-2", status="Verified"),
        ])
        groups = {g["jira_key"]: g for g in payload["jira_groups"]}
        assert groups["TV-1"]["tracking"] is True
        assert groups["TV-2"]["tracking"] is False

    def test_payload_exposes_ack(self):
        from app.services.cl_snapshot_service import build_project_payload
        payload = build_project_payload("P1", [
            _state(issue_key="TV-1", status="Verified", ack_at=NOW, ack_reason="都合并了"),
        ])
        group = payload["jira_groups"][0]
        assert group["ack_reason"] == "都合并了"
        assert group["ack_at"] is not None


class TestDryRunPreview:
    """dry-run 的"本应发出"的提醒必须落盘，否则灰度期看不到完整名单。"""

    def test_appends_to_preview_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(scan, "_DRY_RUN_LOG_DIR", tmp_path)
        scan.append_dry_run_log(1, "TV-1", "Verified", "[~Kai.Qin] TV-1 请确保 CL 已合并。")
        scan.append_dry_run_log(1, "TV-2", "Resolved", "[~A] TV-2 请走验证流程。")

        path = tmp_path / "cl_scan_dryrun_1.log"
        content = path.read_text(encoding="utf-8")
        assert "TV-1" in content and "TV-2" in content
        assert content.count("-" * 60) == 2

    def test_different_users_separate_files(self, tmp_path, monkeypatch):
        monkeypatch.setattr(scan, "_DRY_RUN_LOG_DIR", tmp_path)
        scan.append_dry_run_log(1, "TV-1", "Resolved", "x")
        scan.append_dry_run_log(2, "TV-9", "Resolved", "y")
        assert (tmp_path / "cl_scan_dryrun_1.log").exists()
        assert (tmp_path / "cl_scan_dryrun_2.log").exists()


class TestSummary:
    """PM review 汇总：按状态分组 + 未登记/未合并清单。"""

    def test_build_summary_groups(self):
        states = [
            _state(issue_key="TV-1", status="Resolved"),
            _state(issue_key="TV-2", status="Verified", cl_total=1, cl_unmerged=1),
            _state(issue_key="TV-3", status="Closed", cl_noregister=True),
        ]
        summary = queue.build_summary(states)
        assert summary["total"] == 3
        assert [i["jira_key"] for i in summary["groups"]["Resolved"]] == ["TV-1"]
        assert [i["jira_key"] for i in summary["groups"]["Verified"]] == ["TV-2"]
        assert [i["jira_key"] for i in summary["groups"]["Closed"]] == ["TV-3"]
        assert [i["jira_key"] for i in summary["unregistered"]] == ["TV-3"]
        assert [i["jira_key"] for i in summary["unmerged"]] == ["TV-2"]


# ── 扫描集成（mock MCP + 真实测试 DB）────────────────────────────────────────


class _FakeMCP:
    """可控的 MCP mock：按 issue_key 返回状态与评论。"""

    def __init__(self, issues: dict, comments: dict, gerrit_status: str = "NEW"):
        self.issues = issues          # key -> status
        self.comments = comments      # key -> [{"body": ...}]
        self.gerrit_status = gerrit_status
        self.comment_calls = []       # 记录 get_issue_comments 调用
        self.add_comment_calls = []
        self.get_issue_calls = []

    async def call(self, server, tool, params, user_id=None, timeout=None):
        if tool == "search_issues":
            issues = [{
                "key": k, "fields": {"summary": f"s-{k}", "assignee": {"name": "Kai.Qin"},
                                     "status": {"name": st}, "updated": "u1"},
            } for k, st in self.issues.items()]
            return {"issues": issues, "total": len(issues)}
        if tool == "get_issue_comments":
            k = params["issue_key"]
            self.comment_calls.append(k)
            return {"comments": self.comments.get(k, [])}
        if tool == "get_issue":
            k = params["issue_key"]
            self.get_issue_calls.append(k)
            if k not in self.issues:
                raise RuntimeError("not found")
            return {"key": k, "fields": {"summary": f"s-{k}", "status": {"name": self.issues[k]},
                                         "assignee": {"name": "Kai.Qin"}, "updated": "u2"}}
        if tool == "add_comment":
            self.add_comment_calls.append(params["issue_key"])
            return {}
        if tool == "get_change":
            return (f"# Change {params.get('change_id')}\n"
                    f"**状态**: {self.gerrit_status}\n**分支**: b\n**主题**: s")
        return {}

    async def get_servers(self, user_id=None):
        return {"jira": {"url": "x"}, "gerrit_gerrit2": {"url": "x"}}


CL_MERGED_BODY = ("Change proposed: 'fix [1/1]' (proj branch)\n"
                  "https://gerrit-2.example.com/c/proj/+/123\n"
                  "Change merged from https://gerrit-2.example.com/c/proj/+/123 to\n"
                  "https://gerrit-2.example.com/proj/commit/?id=abc")
CL_OPEN_BODY = ("Change proposed: 'fix [1/1]' (proj branch)\n"
                "https://gerrit-2.example.com/c/proj/+/456")


@pytest.mark.asyncio
async def test_owner_reply_stops_repeated_reminders(db_engine, monkeypatch):
    """端到端复现用户反馈（TV-941201 场景）：

    经办人回复"wifi porting 的问题在 Jira TV-943550 中 follow，其他 cl 都已经 merge"
    之后，不应再反复催办。
    """
    projects = [{"name": "P1", "enabled": True, "cl_monitoring_enabled": True,
                 "advanced_jql": "project = TV"}]
    fake = _FakeMCP({"TV-941201": "Resolved"}, {"TV-941201": [{"body": CL_OPEN_BODY}]})
    # 本用例要验证真实的回复检测链路，先取出真函数再让 _patch 打桩
    from app.services import cl_auto_notify_service as can
    real_rd_scan = can.scan_rd_replies_for_suppression
    _patch(monkeypatch, fake, projects)
    monkeypatch.setattr(can, "scan_rd_replies_for_suppression", real_rd_scan)

    # 第一轮：没有经办人回复 -> 正常提醒
    stats1 = await run_daily_scan(1, dry_run=True)
    assert stats1["reminded"] == 1

    # 第二轮：经办人回复了整体性声明（评论数变化 -> 触发重新分析）
    fake.comments["TV-941201"] = [
        {"body": CL_OPEN_BODY},
        {"body": "wifi porting 的问题在Jira TV-943550 中follow，其他cl 都已经merge。"},
    ]
    fake.issues["TV-941201"] = "Resolved"
    # 让提醒间隔到期，确保"没再提醒"是 ack 起的作用而不是 24h 去重
    from app.db.session import AsyncSessionLocal
    from datetime import timedelta
    async with AsyncSessionLocal() as db:
        st = (await db.execute(select(ClJiraState).where(
            ClJiraState.issue_key == "TV-941201"))).scalar_one()
        st.last_reminded_at = NOW - timedelta(days=2)
        await db.commit()

    stats2 = await run_daily_scan(1, dry_run=True)
    assert stats2["acked"] == 1
    assert stats2["reminded"] == 0

    async with AsyncSessionLocal() as db:
        st = (await db.execute(select(ClJiraState).where(
            ClJiraState.issue_key == "TV-941201"))).scalar_one()
        assert st.ack_at is not None
        assert "TV-943550" in st.ack_reason
        assert st.queued is True      # 仍留在队列，PM summary 里能看到

    # 第三轮：再扫一次也不该重新开始提醒
    async with AsyncSessionLocal() as db:
        st = (await db.execute(select(ClJiraState).where(
            ClJiraState.issue_key == "TV-941201"))).scalar_one()
        st.last_reminded_at = NOW - timedelta(days=2)
        await db.commit()
    stats3 = await run_daily_scan(1, dry_run=True)
    assert stats3["reminded"] == 0


def _patch(monkeypatch, fake, projects, rd_scan=None):
    """装配扫描所需的 mock：MCP、监控配置，以及 RD 回复检测。

    RD 检测默认打桩为 no-op —— 它自己会拉评论，会干扰"CL 确认是否复用了
    上次结果"的断言；该子流程由 test_cl_auto_notify 单独覆盖。
    """
    async def _cfg(user_id=None):
        return {"projects": projects}

    async def _no_rd_scan(user_id):
        return {"suppressed": 0, "analyzed": [], "acks": {}}

    monkeypatch.setattr(capability, "call", fake.call)
    monkeypatch.setattr(capability, "get_servers", fake.get_servers)
    monkeypatch.setattr(
        "app.services.dashboard_service.get_cl_monitoring_config", _cfg)
    monkeypatch.setattr(
        "app.services.cl_auto_notify_service.scan_rd_replies_for_suppression",
        rd_scan or _no_rd_scan)


@pytest.mark.asyncio
async def test_scan_queues_and_reminds(db_engine, monkeypatch):
    """端到端：新题入队 -> Resolved 提醒 -> 状态变化到 Verified 再提醒。"""
    projects = [{"name": "P1", "enabled": True, "cl_monitoring_enabled": True,
                 "advanced_jql": "project = TV"}]

    # 第一轮：TV-1 处于 Resolved 且有未合并 CL
    fake = _FakeMCP({"TV-1": "Resolved"}, {"TV-1": [{"body": CL_OPEN_BODY}]})
    _patch(monkeypatch, fake, projects)
    stats = await run_daily_scan(1, dry_run=True)

    assert stats["new"] == 1
    assert stats["cl_checked"] == 1
    assert stats["reminded"] == 1
    assert fake.add_comment_calls == []  # dry_run 不发评论

    from app.db.session import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        state = (await db.execute(select(ClJiraState).where(
            ClJiraState.issue_key == "TV-1"))).scalar_one()
        assert state.status == "Resolved"
        assert state.queued is True
        assert state.cl_unmerged == 1
        assert state.last_reminded_at is not None

    # 第二轮（同日）：状态未变 + updated 未变 -> 不重复拉评论、不重复提醒
    before_calls = len(fake.comment_calls)
    stats2 = await run_daily_scan(1, dry_run=True)
    assert stats2["unchanged"] == 1
    assert len(fake.comment_calls) == before_calls  # 复用了上次 CL 结果
    assert stats2["reminded"] == 0


@pytest.mark.asyncio
async def test_scan_detects_skip_to_closed(db_engine, monkeypatch):
    """用例 5/6：题从 JQL 结果消失且复查为 Closed -> 留队列进 summary，不评论 owner。"""
    projects = [{"name": "P1", "enabled": True, "cl_monitoring_enabled": True,
                 "advanced_jql": "status not in (Closed)"}]

    # 第一轮：TV-1 在 Resolved，CL 未合并
    fake = _FakeMCP({"TV-1": "Resolved"}, {"TV-1": [{"body": CL_OPEN_BODY}]})
    _patch(monkeypatch, fake, projects)
    await run_daily_scan(1, dry_run=True)

    # 第二轮：TV-1 不再命中 JQL（已 Closed），但 get_issue 复查能查到 Closed
    fake.issues = {"TV-1": "Closed"}
    fake.comment_calls.clear()
    fake.add_comment_calls.clear()
    orig_call = fake.call

    async def call(server, tool, params, user_id=None, timeout=None):
        if tool == "search_issues":
            return {"issues": [], "total": 0}   # `status not in (Closed)` 不再返回它
        return await orig_call(server, tool, params, user_id, timeout)

    monkeypatch.setattr(capability, "call", call)
    stats = await run_daily_scan(1)

    assert stats["disappeared"] == 1
    assert stats["changed"] == 1
    assert "TV-1" in fake.get_issue_calls        # 消失即复查
    assert fake.add_comment_calls == []          # Closed 不评论 owner

    from app.db.session import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        state = (await db.execute(select(ClJiraState).where(
            ClJiraState.issue_key == "TV-1"))).scalar_one()
        assert state.status == "Closed"
        assert state.prev_status == "Resolved"
        assert state.queued is True              # CL 未合并 -> 留在队列
        assert state.cl_unmerged == 1

        summary = queue.build_summary([state])
        assert [i["jira_key"] for i in summary["groups"]["Closed"]] == ["TV-1"]


@pytest.mark.asyncio
async def test_scan_exits_closed_and_merged(db_engine, monkeypatch):
    """用例 7：Closed 且 CL 全部合并 -> 退出队列。"""
    projects = [{"name": "P1", "enabled": True, "cl_monitoring_enabled": True,
                 "advanced_jql": "project = TV"}]
    fake = _FakeMCP({"TV-1": "Closed"}, {"TV-1": [{"body": CL_MERGED_BODY}]})
    _patch(monkeypatch, fake, projects)
    await run_daily_scan(1, dry_run=True)

    from app.db.session import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        state = (await db.execute(select(ClJiraState).where(
            ClJiraState.issue_key == "TV-1"))).scalar_one()
        # 首轮即 Closed -> 归档，不查 CL；归档题不参与退出判定
        assert state.archived is True
        assert state.cl_checked_at is None

    # 题重新活跃 -> 唤醒并做 CL 确认
    fake.issues = {"TV-1": "Verified"}
    await run_daily_scan(1, dry_run=True)
    async with AsyncSessionLocal() as db:
        state = (await db.execute(select(ClJiraState).where(
            ClJiraState.issue_key == "TV-1"))).scalar_one()
        assert state.archived is False
        assert state.status == "Verified"
        assert state.cl_total == 1
        assert state.cl_merged == 1          # 直接由"Change merged"评论判定
        assert state.cl_checked_at is not None
