"""
紧迫度评分模型单元测试（P-A：monitoring_optimization）。

公式：紧迫度 = 优先级权重 × 时间紧迫度 × 状态停滞度
分档：≥6 HIGH / ≥3 MEDIUM / ≥1.5 LOW / <1.5 NONE
"""

from datetime import datetime, timedelta

from app.services.urgency import (
    priority_weight,
    score_issue,
    stagnation_factor,
    time_urgency,
    urgency_level,
    urgency_score,
)


def _date(days_from_now: int) -> str:
    """相对今天的日期字符串（YYYY-MM-DD）。"""
    return (datetime.now() + timedelta(days=days_from_now)).strftime("%Y-%m-%d")


def _updated(days_ago: int) -> str:
    """相对今天的更新时间（ISO）。"""
    return (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S")


class TestPriorityWeight:
    """优先级权重映射。"""

    def test_p0_and_aliases(self):
        assert priority_weight("P0") == 3
        assert priority_weight("Blocker") == 3
        assert priority_weight("Highest") == 3

    def test_p1_and_aliases(self):
        assert priority_weight("P1") == 2
        assert priority_weight("Critical") == 2
        assert priority_weight("High") == 2

    def test_p2_and_aliases(self):
        assert priority_weight("P2") == 1
        assert priority_weight("Major") == 1
        assert priority_weight("Medium") == 1

    def test_low_and_unset(self):
        assert priority_weight("P3") == 0.5
        assert priority_weight("") == 0.5
        assert priority_weight(None) == 0.5
        assert priority_weight("Unknown-Priority") == 0.5


class TestTimeUrgency:
    """时间紧迫度（距目标日期剩余天数）。"""

    def test_no_date_neutral(self):
        assert time_urgency("") == 1.0
        assert time_urgency(None) == 1.0

    def test_overdue(self):
        assert time_urgency(_date(-1)) == 3.0

    def test_less_than_3_days(self):
        assert time_urgency(_date(1)) == 2.5

    def test_less_than_7_days(self):
        assert time_urgency(_date(5)) == 2.0

    def test_less_than_14_days(self):
        assert time_urgency(_date(10)) == 1.5

    def test_less_than_30_days(self):
        assert time_urgency(_date(20)) == 1.0

    def test_far_future(self):
        assert time_urgency(_date(60)) == 1.0


class TestStagnationFactor:
    """状态停滞度。"""

    def test_stale_over_threshold(self):
        assert stagnation_factor(_updated(10), stale_days=7) == 1.5

    def test_recent_update(self):
        assert stagnation_factor(_updated(3), stale_days=7) == 1.0

    def test_no_updated_time(self):
        assert stagnation_factor("") == 1.0


class TestUrgencyScore:
    """紧迫度组合计算。"""

    def test_p0_overdue_stale(self):
        # 3 × 3 × 1.5 = 13.5 → 🔴
        score = urgency_score("P0", _date(-1), _updated(10))
        assert score == 13.5
        assert urgency_level(score) == "HIGH"

    def test_p2_no_eta_normal(self):
        # 1 × 1 × 1 = 1 → 🟢
        score = urgency_score("P2", "", _updated(1))
        assert score == 1.0
        assert urgency_level(score) == "NONE"

    def test_p1_epic_target_5d_stale(self):
        # 2 × 2 × 1.5 = 6 → 🔴（方案 3.4 示例）
        score = urgency_score("P1", _date(5), _updated(10))
        assert score == 6.0
        assert urgency_level(score) == "HIGH"

    def test_p2_due_10d_normal(self):
        # 1 × 1.5 × 1 = 1.5 → 🟡
        score = urgency_score("P2", _date(10), _updated(1))
        assert score == 1.5
        assert urgency_level(score) == "LOW"


class TestUrgencyLevel:
    """分档边界。"""

    def test_thresholds(self):
        assert urgency_level(6.0) == "HIGH"
        assert urgency_level(3.0) == "MEDIUM"
        assert urgency_level(1.5) == "LOW"
        assert urgency_level(1.49) == "NONE"
        assert urgency_level(0) == "NONE"


class TestScoreIssue:
    """score_issue 返回结构化评分结果。"""

    def test_score_issue_structure(self):
        issue = {"key": "TV-1", "summary": "测试", "priority": "P0",
                 "duedate": _date(-1), "updated": _updated(10)}
        result = score_issue(issue)
        assert result["key"] == "TV-1"
        assert result["score"] == 13.5
        assert result["level"] == "HIGH"
        assert result["priority"] == "P0"
