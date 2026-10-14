"""
CL 的 RD 回复检测测试 — 从 Jira 评论识别"该 CL 不需要再 merge"的声明。

2026-09-16 CL 监控 V2 拆分后，本模块只保留 RD 检测：
  - 提醒触发与文案 -> `tests/services/test_cl_jira_state.py::TestReminderRules`
  - 扫描编排       -> `tests/services/test_cl_jira_state.py` 的扫描集成用例

覆盖：规则兜底命中/不命中、无 LLM 时的规则通道、LLM 漏判时的补齐、
无声明时不误伤、Closed 持续跟踪题也会被扫描。

capability.call（外部 Jira MCP）被 mock；cl_suppressed 走真实测试 DB。
"""

import pytest

from app.mcp.capability import capability
from app.services import cl_auto_notify_service as cl_auto_notify
from app.services.cl_auto_notify_service import (
    _rule_detect_settled, _rule_detect_suppressed, _rule_detect_waiting,
)

USER_ID = 1


def _group(jira_key: str, status: str = "Verified", total_cls: int = 0,
           unmerged_cls: int = 0, tracking: bool = False,
           items: list = None) -> dict:
    """构造一个 Jira 组（与页面缓存 jira_groups 结构一致）。"""
    return {
        "jira_key": jira_key,
        "summary": f"s-{jira_key}",
        "assignee": "Kai.Qin",
        "status": status,
        "total_cls": total_cls,
        "merged_cls": total_cls - unmerged_cls,
        "unmerged_cls": unmerged_cls,
        "tracking": tracking,
        "items": items if items is not None else [],
    }


def _snapshots(*groups: dict) -> list:
    """包装为快照列表 [{project, jira_groups: [...]}]。"""
    return [{"project": "P1", "jira_groups": list(groups)}]


class TestRuleDetectSuppressed:
    """规则兜底：LLM 不可用时从评论识别 RD 放弃合并的声明（如 "68297 公版修改，不需要了"）。"""

    def _group(self, *items: dict) -> dict:
        return {"jira_key": "TV-9", "items": list(items)}

    def test_match_owner_decline(self):
        """评论同时包含 CL 号和否定词 -> 命中。"""
        group = self._group(
            {"instance": "gerrit2", "change_id": "68297",
             "status": "NEW", "subject": "s1"},
            {"instance": "gerrit2", "change_id": "68298",
             "status": "NEW", "subject": "s2"},
        )
        comments = [{"body": "68297 公版修改，不需要了", "author": "Kai.Qin"}]
        hits = _rule_detect_suppressed(group, comments)
        assert [h["change_id"] for h in hits] == ["68297"]
        assert hits[0]["reason"] == "rd_reply_rule"

    def test_no_match_positive_comment(self):
        """同一条是请求合并的正向评论 -> 不命中。"""
        group = self._group({"instance": "gerrit2", "change_id": "68297",
                             "status": "NEW", "subject": "s1"})
        comments = [{"body": "68297 请尽快合并到主分支", "author": "Kai.Qin"}]
        assert _rule_detect_suppressed(group, comments) == []

    def test_neg_word_without_cl_id(self):
        """只有否定词、没有 CL 号 -> 不命中（避免误伤）。"""
        group = self._group({"instance": "gerrit2", "change_id": "68297",
                             "status": "NEW", "subject": "s1"})
        comments = [{"body": "公版修改，不需要了", "author": "Kai.Qin"}]
        assert _rule_detect_suppressed(group, comments) == []

    def test_id_substring_blocked(self):
        """更长的数字里包含该 CL 号（如 168297）-> 边界保护，不命中。"""
        group = self._group({"instance": "gerrit2", "change_id": "68297",
                             "status": "NEW", "subject": "s1"})
        comments = [{"body": "168297 不需要了", "author": "Kai.Qin"}]
        assert _rule_detect_suppressed(group, comments) == []

    def test_merged_cl_excluded(self):
        """已合并的 CL 不再参与匹配。"""
        group = self._group({"instance": "gerrit2", "change_id": "68297",
                             "status": "MERGED", "subject": "s1"})
        comments = [{"body": "68297 不需要了", "author": "Kai.Qin"}]
        assert _rule_detect_suppressed(group, comments) == []

    def test_multiple_cls_one_comment(self):
        """一条评论否定多个 CL -> 全部命中。"""
        group = self._group(
            {"instance": "gerrit2", "change_id": "68297",
             "status": "NEW", "subject": "s1"},
            {"instance": "gerrit2", "change_id": "68298",
             "status": "NEW", "subject": "s2"},
        )
        comments = [{"body": "68297 68298 公版修改，都不需要了", "author": "Kai.Qin"}]
        hits = _rule_detect_suppressed(group, comments)
        assert sorted(h["change_id"] for h in hits) == ["68297", "68298"]


class TestRuleDetectSettled:
    """整体性声明兜底：不点名 CL，但表明该题的 CL 已全部处理完毕。

    触发场景（2026-09-16 用户反馈 TV-941201）：经办人回复
    "wifi porting 的问题在Jira TV-943550 中follow，其他cl 都已经merge。"
    但系统之后仍继续催验证，因为旧机制只能按 CL 号逐条抑制。
    """

    def test_other_cls_all_merged(self):
        """用户实测原句 -> 判定已结算。"""
        comments = [{"body": "wifi porting 的问题在Jira TV-943550 中follow，其他cl 都已经merge。"}]
        assert _rule_detect_settled({"jira_key": "TV-941201"}, comments)

    @pytest.mark.parametrize("body", [
        "所有 CL 都已经合并完成",
        "其余改动均已 merge",
        "本 Jira 不需要代码改动",
        "该问题已转移到 TV-943550 跟进",
        "CL 都合并完毕",
    ])
    def test_blanket_statements_hit(self, body):
        assert _rule_detect_settled({}, [{"body": body}])

    @pytest.mark.parametrize("body", [
        "其他CL还没合并",              # 仍是待办
        "请尽快合并这个CL",             # 正向催办
        "该 CL 正在合并中",             # 进行中
        "目前还有两个 CL 未合并",       # 明确有未合并
    ])
    def test_pending_statements_miss(self, body):
        assert _rule_detect_settled({}, [{"body": body}]) is None

    def test_empty_comments(self):
        assert _rule_detect_settled({}, []) is None
        assert _rule_detect_settled({}, [{"body": ""}]) is None


class TestRuleDetectWaiting:
    """「等待外部」规则：经办人明确表示需要第三方/客户/供应商配合，暂时无法推进。

    触发场景（2026-09-20 用户反馈 TV-946422）：PM 评论"这题请客户和iwedia 沟通"，
    系统仍连续两天发"请尽快走验证流程"。等待外部 != 忘了验证，催办无意义。
    """

    @pytest.mark.parametrize("body", [
        "这题请客户和iwedia 沟通",            # 用户实测原句
        "等客户确认后再看",
        "需要 iwedia 配合，暂时无法推进",
        "等供应商回复",
        "请第三方 vendor 确认",
    ])
    def test_waiting_statements_hit(self, body):
        assert _rule_detect_waiting([{"body": body}])

    @pytest.mark.parametrize("body", [
        "请帮忙安排人看看",       # 内部协作，不是外部
        "请尽快合并这个CL",       # 催办
        "该 CL 正在合并中",       # 进行中
        "其他CL还没合并",         # 仍是待办
    ])
    def test_non_waiting_miss(self, body):
        assert _rule_detect_waiting([{"body": body}]) is None

    def test_empty_comments(self):
        assert _rule_detect_waiting([]) is None
        assert _rule_detect_waiting([{"body": ""}]) is None


class TestScanRdRepliesRuleFallback:
    """scan_rd_replies_for_suppression 在 LLM 不可用时走规则兜底并落库。"""

    def _snapshot_group(self) -> dict:
        return {
            "project": "P1",
            "total_cls": 1, "merged_cls": 0, "unmerged_cls": 1,
            "total_jiras": 1, "jiras_unmerged": 1, "jiras_merged": 0,
            "jira_groups": [{
                "jira_key": "TV-9", "summary": "s", "assignee": "Kai.Qin",
                "status": "Verified",
                "total_cls": 1, "merged_cls": 0, "unmerged_cls": 1,
                "items": [{"instance": "gerrit2", "change_id": "68297",
                           "project": "p", "status": "NEW", "subject": "s1"}],
            }],
        }

    @pytest.mark.asyncio
    async def test_scan_suppresses_without_llm(self, db_engine, monkeypatch):
        """LLM 返回 None（未配置）时，规则兜底识别 "68297 公版修改，不需要了" 并标记不关注。"""
        from app.services import cl_snapshot_service as svc

        await svc.save_snapshots([self._snapshot_group()], user_id=USER_ID)

        async def fake_call(server, tool, params, user_id=None, timeout=None):
            assert tool == "get_issue_comments"
            return {"comments": [{"body": "68297 公版修改，不需要了",
                                  "author": "Kai.Qin", "created": "2026-09-04T10:00:00"}]}
        monkeypatch.setattr(capability, "call", fake_call)

        async def no_llm(user_id, group, comments):
            return None  # 模拟 LLM 未配置
        monkeypatch.setattr(cl_auto_notify, "_llm_analyze_cl_suppression", no_llm)

        added = (await cl_auto_notify.scan_rd_replies_for_suppression(USER_ID))["suppressed"]
        assert added == 1

        # 快照重载后 68297 被过滤，组移除、项目计数清零（占位保留）
        snaps = await svc.load_latest_snapshots(user_id=USER_ID)
        assert snaps[0]["total_cls"] == 0
        assert snaps[0]["jira_groups"] == []

        # 评论数未变，第二轮扫描不再重复标记
        added2 = (await cl_auto_notify.scan_rd_replies_for_suppression(USER_ID))["suppressed"]
        assert added2 == 0

    @pytest.mark.asyncio
    async def test_rule_fills_llm_miss(self, db_engine, monkeypatch):
        """LLM 漏判返回空，但评论是显式放弃声明（CL号+否定词）-> 规则兜住，仍标记。"""
        from app.services import cl_snapshot_service as svc

        await svc.save_snapshots([self._snapshot_group()], user_id=USER_ID)

        async def fake_call(server, tool, params, user_id=None, timeout=None):
            return {"comments": [{"body": "68297 公版修改，不需要了",
                                  "author": "Kai.Qin", "created": "2026-09-04T10:00:00"}]}
        monkeypatch.setattr(capability, "call", fake_call)

        async def llm_empty(user_id, group, comments):
            return {"suppressed": [], "settled": False, "reason": ""}  # LLM 漏判：无放弃声明
        monkeypatch.setattr(cl_auto_notify, "_llm_analyze_cl_suppression", llm_empty)

        added = (await cl_auto_notify.scan_rd_replies_for_suppression(USER_ID))["suppressed"]
        assert added == 1

    @pytest.mark.asyncio
    async def test_llm_indirect_declaration_still_caught(self, db_engine, monkeypatch):
        """LLM 识别间接声明（如"已转移到其他Jira"），规则不命中时以 LLM 为准。"""
        from app.services import cl_snapshot_service as svc

        await svc.save_snapshots([self._snapshot_group()], user_id=USER_ID)

        async def fake_call(server, tool, params, user_id=None, timeout=None):
            return {"comments": [{"body": "这个问题已经转移到TV-950000跟进，本Jira的CL不用管了",
                                  "author": "Kai.Qin", "created": "2026-09-04T10:00:00"}]}
        monkeypatch.setattr(capability, "call", fake_call)

        async def llm_indirect(user_id, group, comments):
            return {"suppressed": [{"change_id": "68297", "reason": "已转移到TV-950000"}],
                    "settled": False, "reason": ""}
        monkeypatch.setattr(cl_auto_notify, "_llm_analyze_cl_suppression", llm_indirect)

        added = (await cl_auto_notify.scan_rd_replies_for_suppression(USER_ID))["suppressed"]
        assert added == 1

    @pytest.mark.asyncio
    async def test_no_declaration_no_suppression(self, db_engine, monkeypatch):
        """LLM与规则都不命中（正向评论）-> 不标记。"""
        from app.services import cl_snapshot_service as svc

        await svc.save_snapshots([self._snapshot_group()], user_id=USER_ID)

        async def fake_call(server, tool, params, user_id=None, timeout=None):
            return {"comments": [{"body": "68297 请尽快合并到主分支",
                                  "author": "Kai.Qin", "created": "2026-09-04T10:00:00"}]}
        monkeypatch.setattr(capability, "call", fake_call)

        async def llm_empty(user_id, group, comments):
            return []
        monkeypatch.setattr(cl_auto_notify, "_llm_analyze_cl_suppression", llm_empty)

        added = (await cl_auto_notify.scan_rd_replies_for_suppression(USER_ID))["suppressed"]
        assert added == 0

    @pytest.mark.asyncio
    async def test_closed_tracking_jira_also_scanned(self, db_engine, monkeypatch):
        """Closed 但持续跟踪中（仍会被周期 ping）的 Jira 也参与 RD 回复扫描。"""
        from app.services import cl_snapshot_service as svc

        group = self._snapshot_group()
        group["jira_groups"][0]["status"] = "Closed"
        group["jira_groups"][0]["tracking"] = True
        await svc.save_snapshots([group], user_id=USER_ID)

        async def fake_call(server, tool, params, user_id=None, timeout=None):
            return {"comments": [{"body": "68297 公版修改，不需要了",
                                  "author": "Kai.Qin", "created": "2026-09-04T10:00:00"}]}
        monkeypatch.setattr(capability, "call", fake_call)

        async def no_llm(user_id, group, comments):
            return None
        monkeypatch.setattr(cl_auto_notify, "_llm_analyze_cl_suppression", no_llm)

        added = (await cl_auto_notify.scan_rd_replies_for_suppression(USER_ID))["suppressed"]
        assert added == 1
