"""CL「不关注」过滤测试。

回归背景（2026-09-18）：
原实现用同一条 `if not items: continue` 处理两种截然不同的情况：
  a) 题**有 CL**，但全部被用户标记"不关注" -> 应该整组移除；
  b) 题**本来就没有 CL**（未登记 CL）-> 不该移除。
(b) 被误判成 (a) 后，20 个「未登记 CL」的题从主页面消失 —— 而这类题恰恰是
最需要 PM 确认是否漏提 CL 的（用户提这个需求的初衷）。
"""

from app.services.cl_suppression_service import (
    _suppressed_keys, filter_suppressed_projects,
)


def _project(*groups: dict) -> list:
    return [{"project": "P1", "jira_groups": list(groups)}]


def _group(jira_key: str, items: list) -> dict:
    merged = sum(1 for it in items if it.get("status") == "MERGED")
    return {
        "jira_key": jira_key,
        "status": "Verified",
        "items": items,
        "total_cls": len(items),
        "merged_cls": merged,
        "unmerged_cls": len(items) - merged,
    }


def _item(change_id: str, instance: str = "gerrit2", status: str = "NEW") -> dict:
    return {"instance": instance, "change_id": change_id, "status": status}


class TestSuppressedKeys:
    def test_builds_tuples(self):
        keys = _suppressed_keys([
            {"instance": "i", "change_id": "1", "jira_key": "TV-1"},
        ])
        assert keys == {("i", "1", "TV-1")}

    def test_ignores_non_dict(self):
        assert _suppressed_keys(["junk", None]) == set()


class TestFilterSuppressedProjects:
    def test_keeps_group_without_any_cl(self):
        """未登记 CL 的题必须保留（用户实测：TV-949609/TV-950327 曾被静默丢弃）。"""
        projects = _project(_group("TV-949609", []))
        out = filter_suppressed_projects(projects, {("i", "1", "TV-999")})
        assert len(out[0]["jira_groups"]) == 1
        assert out[0]["jira_groups"][0]["jira_key"] == "TV-949609"
        assert out[0]["total_jiras"] == 1

    def test_drops_group_when_all_cls_suppressed(self):
        """原有 CL 全部被标记不关注 -> 整组移除（保持原语义）。"""
        projects = _project(_group("TV-1", [_item("68297")]))
        out = filter_suppressed_projects(projects, {("gerrit2", "68297", "TV-1")})
        assert out[0]["jira_groups"] == []
        assert out[0]["total_cls"] == 0

    def test_partial_suppression_recounts(self):
        """部分 CL 被忽略 -> 保留其余的并重算计数。"""
        projects = _project(_group("TV-1", [_item("1"), _item("2", status="MERGED")]))
        out = filter_suppressed_projects(projects, {("gerrit2", "1", "TV-1")})
        group = out[0]["jira_groups"][0]
        assert group["total_cls"] == 1
        assert group["merged_cls"] == 1
        assert group["unmerged_cls"] == 0

    def test_suppression_is_per_jira(self):
        """同名 CL 挂在别的 Jira 下时，A 的标记不影响 B。"""
        projects = _project(_group("TV-A", [_item("1")]), _group("TV-B", [_item("1")]))
        out = filter_suppressed_projects(projects, {("gerrit2", "1", "TV-A")})
        keys = [g["jira_key"] for g in out[0]["jira_groups"]]
        assert keys == ["TV-B"]

    def test_error_placeholder_project_untouched(self):
        """无 jira_groups 结构的 error 占位项目原样保留。"""
        projects = [{"project": "error", "error": "Jira 查询失败"}]
        out = filter_suppressed_projects(projects, {("i", "1", "TV-1")})
        assert out[0]["error"] == "Jira 查询失败"

    def test_empty_suppressed_still_keeps_no_cl_group(self):
        """没有标记时也要保留未登记 CL 的题（走的是同一条保留分支）。"""
        projects = _project(_group("TV-2", []))
        out = filter_suppressed_projects(projects, set())
        assert out[0]["jira_groups"][0]["jira_key"] == "TV-2"
