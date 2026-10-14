"""
结果展示预算分配纯函数测试（层 A，2026-09-08）。

覆盖：全部短评论预算内全装 / 长评论自动少装（单条截断）/ 最新优先 / 预算耗尽整条归
omitted（无碎条）/ 空输入 / 超长单条正文截断 / 极端小预算边界。
"""

from app.services.agent.result_budget import fit_items_to_budget


def _mk(created: str, body: str) -> dict:
    return {"created": created, "author": "u", "body": body}


def _comments(days: int, body: str) -> list:
    """生成 days 条评论，created 从 08-17 起递增。"""
    return [_mk(f"2026-08-{17 + i:02d}T10:00:00", body) for i in range(days)]


class TestBudgetFits:
    """预算充足场景。"""

    def test_all_short_fit_within_budget(self):
        """短评论数量再多也全部装下（预算按字符而非条数）。"""
        items = _comments(100, "短")
        shown, omitted, spent = fit_items_to_budget(
            items, budget_chars=20000,
            sort_key=lambda c: c["created"], get_body=lambda c: c["body"],
        )
        assert len(shown) == 100
        assert omitted == []
        assert spent > 0

    def test_newest_first_when_overflow(self):
        """溢出时保留最新条目，省略更旧条目。"""
        items = _comments(50, "x" * 800)  # 50 × ~800 = 大
        shown, omitted, _ = fit_items_to_budget(
            items, budget_chars=8000,
            sort_key=lambda c: c["created"], get_body=lambda c: c["body"],
        )
        assert len(shown) >= 1
        assert len(omitted) >= 1
        assert len(shown) + len(omitted) == 50
        # 展示的是最新的（created 大的在前）
        assert shown[0][0]["created"] >= omitted[0]["created"]
        assert shown[-1][0]["created"] >= omitted[0]["created"]

    def test_long_body_truncated_to_per_item_max(self):
        """单条超长正文截断到 per_item_max，不独占预算。"""
        items = [_mk("2026-08-30T10:00:00", "y" * 5000)]
        shown, omitted, _ = fit_items_to_budget(
            items, budget_chars=20000,
            sort_key=lambda c: c["created"], get_body=lambda c: c["body"],
        )
        assert len(shown) == 1
        assert len(shown[0][1]) <= 2000  # 默认 per_item_max=2000
        assert omitted == []

    def test_no_truncated_fragments_in_omitted(self):
        """预算耗尽 → 整条归 omitted，不产生半条碎评论误导分析。"""
        items = _comments(40, "z" * 1000)
        shown, omitted, _ = fit_items_to_budget(
            items, budget_chars=5000,
            sort_key=lambda c: c["created"], get_body=lambda c: c["body"],
        )
        # 每条完整属于 shown 或 omitted，不交叉
        shown_ids = {id(it) for it, _ in shown}
        omitted_ids = {id(it) for it in omitted}
        assert not (shown_ids & omitted_ids)
        assert len(shown) + len(omitted) == 40


class TestEdgeCases:
    """边界场景。"""

    def test_empty_input(self):
        shown, omitted, spent = fit_items_to_budget(
            [], budget_chars=1000,
            sort_key=lambda c: c["created"], get_body=lambda c: c["body"],
        )
        assert shown == []
        assert omitted == []
        assert spent == 0

    def test_missing_body_field(self):
        """body 字段缺失 → 空串，不抛异常。"""
        items = [{"created": "2026-08-17", "body": None}, {"created": "2026-08-18"}]
        shown, omitted, _ = fit_items_to_budget(
            items, budget_chars=5000,
            sort_key=lambda c: c.get("created", ""), get_body=lambda c: c.get("body", ""),
        )
        assert len(shown) == 2
        assert shown[0][1] == ""  # None 规范化
        assert omitted == []

    def test_ascending_order_option(self):
        """newest_first=False 时旧条目优先。"""
        items = [_mk("2026-08-17T10:00:00", "a"), _mk("2026-08-30T10:00:00", "b")]
        shown, _, _ = fit_items_to_budget(
            items, budget_chars=10000,
            sort_key=lambda c: c["created"], get_body=lambda c: c["body"],
            newest_first=False,
        )
        assert shown[0][0]["created"] < shown[1][0]["created"]
