"""CL 解析与合并状态判定测试（2026-09-16 修复回归）。

背景：TV-950814/TV-950475/TV-950813/TV-950824 排查发现三个解析层缺陷：
  1. 跨 Jira 去重键缺 jira_key —— 同一个 CL 被多个 Jira 引用时，后一个 Jira 整组消失
  2. CL URL 正则只认完整形式 —— 评论里短链（https://gerrit-1.example.com/<id>）完全漏掉
  3. MERGED_STATUSES 只认英文 —— 中文实例返回「已合并」会被静默判为未合并

覆盖：纯函数解析（extract_cls_from_comments / is_merged）+ get_cl_status_for_issues 去重。
"""

import pytest

from app.mcp.capability import capability
from app.services.gerrit_service import (
    extract_cls_from_comments, get_cl_status_for_issues, is_merged,
)

# 真实评论格式（2026-09 从 TV-950813 抓取；URL 与括号之间是换行）
PROPOSED_FULL = (
    "Change proposed: 'releaseDTVKit: update prebuilts [1/1]' "
    "(platform/vendor/example/reference u-tv-release-branch)\n"
    "https://gerrit-2.example.com/c/platform/vendor/example/reference/+/71439"
)
MERGED_FULL = (
    "Change merged from "
    "https://gerrit-2.example.com/c/platform/vendor/example/reference/+/71439 to\n"
    "https://gerrit-2.example.com/platform/vendor/example/reference/commit/?id=abc"
)
PROPOSED_SHORT = (
    "Change proposed: 'ATSCCore: add CRC check for EIT [1/1]' "
    "(DTVKit/ATSC p-reference-chip-tv)\n"
    "https://gerrit-1.example.com/743538"
)
MERGED_SHORT = (
    "Change merged from https://gerrit-1.example.com/743538 to\n"
    "http://git.example.com/DTVKit/ATSC/commit/?id=d818d70c"
)


def _comments(*bodies: str) -> list:
    return [{"body": b} for b in bodies]


class TestIsMerged:
    """合并状态判定（大小写不敏感 + 中文实例）。"""

    @pytest.mark.parametrize("status", ["MERGED", "Merged", "Merging", "merged", "已合并", "已合入"])
    def test_merged_statuses(self, status):
        assert is_merged(status) is True

    @pytest.mark.parametrize("status", ["NEW", "PROPOSED", "ABANDONED", "Error", "Unknown", "", None])
    def test_unmerged_statuses(self, status):
        assert is_merged(status) is False


class TestExtractClsFromComments:
    """评论中的 CL 引用与合并证据提取。"""

    def test_full_url_with_metadata(self):
        """完整 URL + Change proposed → 带 subject/project/branch 元信息。"""
        items = extract_cls_from_comments(_comments(PROPOSED_FULL), "TV-1")
        assert len(items) == 1
        it = items[0]
        assert it["instance"] == "gerrit2"
        assert it["change_id"] == "71439"
        assert it["project"] == "platform/vendor/example/reference"
        assert it["branch"] == "u-tv-release-branch"
        assert it["subject"] == "releaseDTVKit: update prebuilts [1/1]"
        assert it["url_form"] == "full"
        assert it["merged_evidence"] is False

    def test_short_link_second_instance_parsed(self):
        """短链不是 Gerrit permalink，但机器人评论带齐元信息 → 仍能入库。"""
        items = extract_cls_from_comments(_comments(PROPOSED_SHORT), "TV-1")
        assert len(items) == 1
        it = items[0]
        assert it["instance"] == "gerrit1"
        assert it["change_id"] == "743538"
        assert it["project"] == "DTVKit/ATSC"
        assert it["subject"] == "ATSCCore: add CRC check for EIT [1/1]"
        assert it["url_form"] == "short"

    def test_change_merged_marks_evidence(self):
        """Change merged from <url> → 该 CL 标记 merged_evidence（无需查 Gerrit）。"""
        items = extract_cls_from_comments(
            _comments(PROPOSED_SHORT, MERGED_SHORT), "TV-1")
        assert items[0]["merged_evidence"] is True

    def test_merged_evidence_for_full_url(self):
        items = extract_cls_from_comments(
            _comments(PROPOSED_FULL, MERGED_FULL), "TV-1")
        assert items[0]["merged_evidence"] is True

    def test_dedup_within_issue_across_comments(self):
        """同一 CL 在多条评论中重复出现 → 同一 Jira 内只保留一条。"""
        items = extract_cls_from_comments(
            _comments(PROPOSED_FULL, MERGED_FULL, PROPOSED_FULL), "TV-1")
        assert len(items) == 1

    def test_proposed_only_is_not_merged(self):
        """只有 Change proposed 没有 merged → 不算已合并。"""
        items = extract_cls_from_comments(_comments(PROPOSED_SHORT), "TV-1")
        assert items[0]["merged_evidence"] is False

    def test_no_cl_returns_empty(self):
        assert extract_cls_from_comments(_comments("普通评论，无 CL"), "TV-1") == []

    def test_non_dict_comment_ignored(self):
        assert extract_cls_from_comments(["not-a-dict", None], "TV-1") == []


class TestCrossJiraDedup:
    """回归：同一 CL 被多个 Jira 引用时，两个 Jira 都要出现。

    修复前去重键为 (project, change_id)，第二个 Jira 整组消失 ——
    TV-950813 / TV-950824 共用同一对 CL，实际只显示了 TV-950813。
    """

    @pytest.mark.asyncio
    async def test_shared_cl_keeps_both_jiras(self, monkeypatch):
        # 同一个 CL 同时出现在两个 Jira 的评论中（模拟 TV-950813 / TV-950824 共用 CL 的情形）
        body = PROPOSED_FULL + "\n" + MERGED_FULL

        async def _fake_call(server, tool, params, user_id=None, timeout=None):
            if tool == "get_issue_comments":
                return {"comments": [{"body": body}]}
            return {}

        async def _fake_servers(user_id=None):
            return {"jira": {"url": "x"}, "gerrit_gerrit2": {"url": "x"}}

        monkeypatch.setattr(capability, "call", _fake_call)
        monkeypatch.setattr(capability, "get_servers", _fake_servers)

        issues = [
            {"key": "TV-1", "fields": {"summary": "a", "status": {"name": "Closed"}}},
            {"key": "TV-2", "fields": {"summary": "b", "status": {"name": "Closed"}}},
        ]
        data = await get_cl_status_for_issues(issues, user_id=1)

        keys = {g["jira_key"] for g in data["jira_groups"]}
        assert keys == {"TV-1", "TV-2"}, "共用同一 CL 的两个 Jira 都必须出现在结果中"
        assert data["total_jiras"] == 2
        assert data["total_cls"] == 2
        assert data["merged_cls"] == 2  # 机器人"Change merged"评论直接判定，无需查 Gerrit
