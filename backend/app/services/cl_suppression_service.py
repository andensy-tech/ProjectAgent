"""CL 不关注过滤 — 供 cl_snapshot_service 读取快照后实时过滤、cl_auto_notify_service 复用。

过滤依据：用户标记的"不需要关注"CL 列表（cl_suppressed），
命中 (instance, change_id, jira_key) 的 item 从快照中移除，并重算所有计数。

注意：**只对"有 CL"的题做整组移除**。本来就没有 CL 的题（未登记 CL）必须保留 ——
它们是最需要 PM 确认是否漏提的一类，用同一条 `if not items: continue` 会把它们
静默丢掉（实测 20 个未登记 CL 的题因此从主页面消失）。
"""
from typing import Dict, List, Set, Tuple

from app.services.gerrit_service import is_merged


def _suppressed_keys(suppressed: list) -> Set[Tuple[str, str, str]]:
    """将 suppressed 列表转为 (instance, change_id, jira_key) 集合。"""
    return {
        (e.get("instance"), e.get("change_id"), e.get("jira_key"))
        for e in suppressed if isinstance(e, dict)
    }


def filter_suppressed_projects(projects: list, suppressed_keys: Set[Tuple[str, str, str]]) -> List[Dict]:
    """过滤快照中的不关注 CL 并重算 jira_group 与 project 全部计数。

    返回新列表，不改原 dict。error 占位项目（无 jira_groups 结构）原样保留；
    jira_groups 被过滤为空的 project 保留并清零计数（前端显示占位，R3 不再误报）。
    """
    out: List[Dict] = []
    for proj in projects:
        groups = proj.get("jira_groups")
        if not isinstance(groups, list):
            out.append(proj)
            continue

        new_groups = []
        for g in groups:
            jira_key = g.get("jira_key")
            original = g.get("items", []) or []
            # 本来就没有 CL 的题（「未登记 CL」，最需要 PM 确认是否漏提）必须保留：
            # 此前空 items 被当成"全部被忽略"整组丢弃，导致这些题在主页面完全不可见。
            if not original:
                new_groups.append(dict(g))
                continue
            items = [
                it for it in original
                if (it.get("instance"), it.get("change_id"), jira_key) not in suppressed_keys
            ]
            if not items:
                continue  # 原有 CL 全部被标记不关注 -> 整组移除
            merged = sum(1 for it in items if is_merged(it.get("status")))
            ng = dict(g)
            ng["items"] = items
            ng["total_cls"] = len(items)
            ng["merged_cls"] = merged
            ng["unmerged_cls"] = len(items) - merged
            new_groups.append(ng)

        merged_total = sum(g["merged_cls"] for g in new_groups)
        unmerged_total = sum(g["unmerged_cls"] for g in new_groups)
        np_ = dict(proj)
        np_["jira_groups"] = new_groups
        np_["total_cls"] = merged_total + unmerged_total
        np_["merged_cls"] = merged_total
        np_["unmerged_cls"] = unmerged_total
        np_["total_jiras"] = len(new_groups)
        np_["jiras_unmerged"] = sum(1 for g in new_groups if g["unmerged_cls"] > 0)
        np_["jiras_merged"] = len(new_groups) - np_["jiras_unmerged"]
        out.append(np_)
    return out
