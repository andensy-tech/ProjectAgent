"""结果展示预算分配纯函数（层 A，2026-09-08）。

背景：MCP 工具（get_issue_comments 等）不分页，一次返回大数据（57 条评论）。此前用
写死的条数上限（max_show=30）截断，导致 LLM 看不到更早条目；本模块用**字符预算**驱动
展示决策：在给定字符预算内尽量多放条目、且保证最新/最重要的在前；放不下的作为
omitted 交由调用方 spill 暂存（可查回）。

设计原则（与用户对齐）：
  - 不感知模型 context 窗口：预算由调用方给一个保守常量（如 20000 字符/结果），
    而非按模型窗口实时估算。
  - 按字符而非条数：短评论预算内全部装入，长评论自动少装几条——直接对应
    "碰到单条非常长的评论可能几条就撑爆" 的原始诉求。
  - 最新优先：默认按 sort_key 降序处理，最新的先进 shown。
  - 单条正文截断到 per_item_max（防单条超长评论独占预算）。
  - 预算装不下下一条 → 该条及之后更旧的**整条**归 omitted（展示区不放半条碎评论，
    避免误导 LLM 分析），由调用方 spill 完整暂存供精确查回。

本模块为纯函数，无 IO/DB/LLM 依赖，便于单元测试。
"""

from typing import Any, Callable, Dict, List, Tuple


def fit_items_to_budget(
    items: List[Dict[str, Any]],
    *,
    budget_chars: int,
    sort_key: Callable[[Dict[str, Any]], Any],
    get_body: Callable[[Dict[str, Any]], str],
    newest_first: bool = True,
    per_item_max: int = 2000,
    overhead_per_item: int = 100,
) -> Tuple[List[Tuple[Dict[str, Any], str]], List[Dict[str, Any]], int]:
    """在字符预算内挑选可展示条目。

    Args:
        items: 候选条目（dict），如评论 [{created, author, body}, ...]。
        budget_chars: 本结果可用总字符预算（含 overhead 估算）。
        sort_key: 从条目取排序键的回调（如 lambda c: c["created"]）。
        get_body: 从条目取正文文本的回调（如 lambda c: c["body"]）。
        newest_first: True 时按 sort_key 降序（最新优先）；False 升序。
        per_item_max: 单条正文截断上限（超过则截断，防单条独占预算）。
        overhead_per_item: 每条固定展示开销估算（编号/作者/时间/换行等），计入预算。

    Returns:
        (shown, omitted, spent):
          shown:   [(原条目, 最终展示用正文)]，顺序即推荐展示顺序（最新在前）。
          omitted: 预算装不下的更旧条目（整条，含完整正文），调用方应 spill 暂存。
          spent:   实际占用的估算字符数。

    Example:
        shown, omitted, spent = fit_items_to_budget(
            comments, budget_chars=20000,
            sort_key=lambda c: c.get("created", ""),
            get_body=lambda c: c.get("body", ""),
        )
    """
    if not items:
        return [], [], 0

    def body_of(item: Dict[str, Any]) -> str:
        return _normalize_body(get_body(item))

    ordered = sorted(
        items,
        key=lambda it: sort_key(it),
        reverse=newest_first,
    )

    shown: List[Tuple[Dict[str, Any], str]] = []
    omitted: List[Dict[str, Any]] = []
    spent = 0

    for idx, item in enumerate(ordered):
        body = body_of(item)[:per_item_max]
        cost = overhead_per_item + len(body)

        # 预算装不下下一条 → 当前及之后更旧的整条归 omitted
        if spent + cost > budget_chars:
            omitted = ordered[idx:]
            break

        shown.append((item, body))
        spent += cost

    return shown, omitted, spent


def _normalize_body(body: Any) -> str:
    """正文字符串化并压缩连续空行（与评论原格式化一致：折叠 ≥3 空行为 ≤2）。"""
    if body is None:
        return ""
    text = str(body)
    lines = text.split("\n")
    result: List[str] = []
    blank_run = 0
    for ln in lines:
        if not ln.strip():
            blank_run += 1
            if blank_run <= 2:
                result.append("")
            continue
        blank_run = 0
        result.append(ln)
    return "\n".join(result)
