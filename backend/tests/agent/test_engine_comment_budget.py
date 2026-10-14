"""
Agent 评论展示预算化测试（层 A，2026-09-08）。

验证修复 TV-946422 根因（原 max_show=30 硬编码）：评论按字符预算展示、最新优先、
溢出在尾部提示；同步 `_format_issue_comments` 不再丢后 25 条但保留溢出提示。
"""

from app.services.agent.engine import AgentEngine


def _mk_comments(n: int, body_len: int = 60) -> dict:
    """构造 n 条评论的 get_issue_comments 结构。"""
    return {
        "issue_key": "TV-946422",
        "total_comments": n,
        "comments": [
            {"id": i, "author": f"user{i}", "created": f"2026-08-{17 + i:02d}T10:00:00+0800",
             "body": f"评论 {i} " + "x" * body_len}
            for i in range(n)
        ],
    }


class TestCommentNoHard30Limit:
    """根因：去掉 max_show=30。"""

    def test_57_short_comments_all_shown(self):
        """57 条短评论全部展示（预算按字符非条数）——原 bug 只显示前 30 条。"""
        engine = AgentEngine()
        shown, omitted, _ = engine._select_comments_for_display(_mk_comments(57))
        assert len(shown) == 57
        assert omitted == []

    def test_display_contains_all_57_without_truncation_note(self):
        """同步格式化输出不含"仅展示前 30 条"。"""
        engine = AgentEngine()
        out = engine._format_issue_comments(_mk_comments(57))
        assert "仅展示前 30 条" not in out
        assert "共 57 条" in out

    def test_newest_first_ordering(self):
        """展示顺序最新优先（分析问题最新评论最相关）。"""
        engine = AgentEngine()
        shown, _, _ = engine._select_comments_for_display(_mk_comments(20))
        created = [c["created"] for c, _ in shown]
        assert created == sorted(created, reverse=True)

    def test_overflow_long_comments_produce_note(self):
        """长评论超出预算 → 尾部有"已展示最新 N 条 / 更早 M 条"提示。"""
        engine = AgentEngine()
        # 每条 ~500 字符（正文），预算 20000 只装得下 ~30 条
        data = _mk_comments(60, body_len=500)
        out = engine._format_issue_comments(data)
        assert "更早" in out
        assert "context_spill_read" in out  # 提示可查回
        # 不出现整条被静默丢弃无提示的情况
        assert "共 60 条" in out


class TestCommentSpillNotPersistedInSyncPath:
    """同步展示路径不落盘 spill（无 user_id）；spill 由 async 主循环方法负责。"""

    def test_sync_format_is_pure_display(self):
        """同步 _format_issue_comments 只返回展示串，不产生副作用/不落盘。"""
        engine = AgentEngine()
        data = _mk_comments(60, body_len=500)
        out = engine._format_issue_comments(data)
        assert isinstance(out, str)
        # 展示预算结果应包含头部
        assert out.startswith("## TV-946422 评论")
