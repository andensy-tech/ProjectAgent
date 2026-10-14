"""
FocusSet 健康度计算单元测试（P-C：看板状态图）。

健康度 = fix rate 达成 + 距截止天数 + 停滞成员（方案 8.3）。
"""

from datetime import date, timedelta

from app.models.focus_set import FocusSetMember
from app.services.focus_set_service import compute_health


def _member(key: str, status: str, stagnant: bool = False) -> FocusSetMember:
    return FocusSetMember(
        issue_key=key, summary=f"summary-{key}", status=status,
        stagnant=stagnant, stagnant_days=5 if stagnant else 0,
    )


def _days(n: int) -> date:
    return date.today() + timedelta(days=n)


class TestComputeHealth:
    """健康度计算。"""

    def test_green_when_target_achieved(self):
        # 5 成员 4 done → 80% 达标 → green
        members = [
            _member("TV-1", "Resolved"), _member("TV-2", "Closed"),
            _member("TV-3", "Verified"), _member("TV-4", "Done"),
            _member("TV-5", "Open"),
        ]
        h = compute_health(_days(5), 0.8, members)
        assert h["progress"] == 0.8
        assert h["diff_to_target"] == 0
        assert h["status"] == "green"

    def test_red_when_overdue(self):
        members = [_member("TV-1", "Open")]
        h = compute_health(_days(-1), None, members)
        assert h["days_left"] == -1
        assert h["status"] == "red"

    def test_red_when_under_target_and_due_soon(self):
        # 距截止 2 天 + 未达标 → red
        members = [
            _member("TV-1", "Resolved"), _member("TV-2", "Open"),
            _member("TV-3", "Open"), _member("TV-4", "Open"), _member("TV-5", "Open"),
        ]
        h = compute_health(_days(2), 0.8, members)
        assert h["progress"] == 0.2
        assert h["diff_to_target"] == 3  # ceil(5*0.8)-1 = 4-1
        assert h["status"] == "red"

    def test_yellow_when_under_target_far(self):
        members = [_member("TV-1", "Resolved"), _member("TV-2", "Open")]
        h = compute_health(_days(10), 0.8, members)
        assert h["progress"] == 0.5
        assert h["status"] == "yellow"

    def test_yellow_when_stagnant(self):
        members = [_member("TV-1", "Open", stagnant=True), _member("TV-2", "Open")]
        h = compute_health(_days(10), None, members)
        assert h["status"] == "yellow"
        assert len(h["stagnant_members"]) == 1

    def test_empty_set_yellow(self):
        h = compute_health(_days(5), 0.8, [])
        assert h["total"] == 0
        assert h["status"] == "yellow"

    def test_status_distribution(self):
        members = [
            _member("TV-1", "Open"), _member("TV-2", "Open"),
            _member("TV-3", "In Progress"), _member("TV-4", "Resolved"),
        ]
        h = compute_health(None, None, members)
        assert h["status_dist"] == {"Open": 2, "In Progress": 1, "Resolved": 1}

    def test_done_statuses(self):
        # 中英文已完成状态都计入 done
        members = [_member("TV-1", "已解决"), _member("TV-2", "Closed"), _member("TV-3", "Open")]
        h = compute_health(None, 0.5, members)
        assert h["done"] == 2
        assert h["status"] == "green"


class TestEpicBaseline:
    """Epic 周基线健康度计算。"""

    def test_fix_rate_uses_baseline_total(self):
        """基线 10 个，当前完成 7 个，目标 80% → progress 0.7，差 1 个达标。"""
        members = [
            _member("TV-1", "Resolved"), _member("TV-2", "Verified"),
            _member("TV-3", "Closed"), _member("TV-4", "Done"),
            _member("TV-5", "Resolved"), _member("TV-6", "Resolved"),
            _member("TV-7", "Resolved"), _member("TV-8", "Open"),
            _member("TV-9", "Open"), _member("TV-10", "Open"),
        ]
        # 当前总数 10，基线 10；若新增 2 个 issue 也不影响分母
        members.append(_member("TV-11", "Resolved"))
        members.append(_member("TV-12", "Open"))
        baseline_members = [{"key": f"TV-{i}", "status": "Open"} for i in range(1, 11)]
        h = compute_health(_days(5), 0.8, members, baseline_total=10, baseline_members=baseline_members)
        assert h["total"] == 12
        assert h["done"] == 8
        assert h["baseline_total"] == 10
        assert h["progress"] == 0.8  # 8/10
        assert h["diff_to_target"] == 0
        assert h["status"] == "green"

    def test_weekly_changes(self):
        """基线 Open 的 issue 当前 Resolved → newly_done；基线 Closed 当前 Open → reopened。"""
        members = [
            _member("TV-1", "Resolved"),
            _member("TV-2", "Open"),
            _member("TV-3", "Open"),
        ]
        baseline_members = [
            {"key": "TV-1", "status": "Open"},
            {"key": "TV-2", "status": "Closed"},
            {"key": "TV-4", "status": "Open"},  # 当前已移除
        ]
        h = compute_health(None, None, members, baseline_members=baseline_members)
        wc = h["weekly_changes"]
        assert [m["key"] for m in wc["newly_done"]] == ["TV-1"]
        assert [m["key"] for m in wc["reopened"]] == ["TV-2"]
        assert [m["key"] for m in wc["added"]] == ["TV-3"]
        assert [m["key"] for m in wc["removed"]] == ["TV-4"]

    def test_done_delta(self):
        """last_done_count 存在时，done_delta = 当前完成数 - 上次完成数。"""
        members = [
            _member("TV-1", "Resolved"), _member("TV-2", "Resolved"),
            _member("TV-3", "Open"),
        ]
        h = compute_health(None, None, members, last_done_count=1)
        assert h["done"] == 2
        assert h["done_delta"] == 1


class TestReviewFixes:
    """2026-09-09 review 修复回归测试（#3/#8）。"""

    def test_all_fix_requires_all_done_without_target(self):
        """#3 metric_type=all_fix 未设 target → 按 1.0 判定，未全完成不达标。"""
        members = [_member("TV-1", "Resolved"), _member("TV-2", "Open")]
        h = compute_health(None, None, members, metric_type="all_fix")
        assert h["target"] == 1.0  # 生效目标按全部完成
        assert h["progress"] == 0.5
        assert h["achieved"] is False
        assert h["status"] != "green"

    def test_all_fix_achieved_when_all_done(self):
        members = [_member("TV-1", "Resolved"), _member("TV-2", "Closed")]
        h = compute_health(None, None, members, metric_type="all_fix")
        assert h["achieved"] is True
        assert h["status"] == "green"

    def test_all_fix_explicit_target_wins(self):
        """#3 all_fix 显式设了 target 时以显式值为准。"""
        members = [_member("TV-1", "Resolved"), _member("TV-2", "Open")]
        h = compute_health(None, 0.5, members, metric_type="all_fix")
        assert h["target"] == 0.5
        assert h["achieved"] is True

    def test_fix_rate_default_unchanged(self):
        """#3 默认（None/fix_rate）行为不变。"""
        members = [_member("TV-1", "Resolved"), _member("TV-2", "Open")]
        h = compute_health(None, None, members)
        assert h["target"] is None
        assert h["achieved"] is None  # 无目标不判定

    def test_split_goal_tag(self):
        """#8 identifier #goal 后缀拆分。"""
        from app.services.focus_set_service import _split_goal_tag
        assert _split_goal_tag("TV-918177") == ("TV-918177", "")
        assert _split_goal_tag("TV-918177#week80") == ("TV-918177", "week80")
        assert _split_goal_tag("60513#month100") == ("60513", "month100")
        assert _split_goal_tag("  TV-1  #  tag  ") == ("TV-1", "tag")

    def test_parse_epic_identifier_strips_goal_tag(self):
        """#8 epic 解析剥掉 #goal 后缀（URL/纯 key/带后缀）。"""
        from app.services.focus_set_service import _parse_epic_identifier
        assert _parse_epic_identifier("TV-918177#week80") == "TV-918177"
        assert _parse_epic_identifier("https://jira.example.com/browse/TV-918177#week80") == "TV-918177"
        assert _parse_epic_identifier("https://jira.example.com/browse/TV-918177") == "TV-918177"

    def test_build_epic_jql_uses_base_key(self):
        """#8 epic JQL 用基础 key 而非带后缀 identifier。"""
        from app.services.focus_set_service import _build_epic_jql
        assert _build_epic_jql("TV-918177#week80") == '"Epic Link" = TV-918177'
