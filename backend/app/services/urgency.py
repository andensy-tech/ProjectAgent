"""
问题紧迫度评分模型（P-A 阶段落地，见 docs/design/monitoring_optimization.md）

紧迫度 = 优先级权重 × 时间紧迫度 × 状态停滞度
分档：🔴 ≥6 HIGH / 🟠 ≥3 MEDIUM / 🟡 ≥1.5 LOW / 🟢 <1.5 无

P-A 阶段时间紧迫度以 issue.duedate 为目标日期；项目计划知识库（P-D 阶段）
接入后替换目标日期来源。纯函数模块，无 IO，可独立单测。
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

_BJS_TZ = timezone(timedelta(hours=8))  # 北京时间

# 优先级权重（3.1 方案表）
_PRIORITY_WEIGHTS = {
    "P0": 3, "Blocker": 3, "Highest": 3,
    "P1": 2, "Critical": 2, "High": 2,
    "P2": 1, "Major": 1, "Medium": 1,
    "P3": 0.5, "Minor": 0.5, "Low": 0.5, "Lowest": 0.5,
}
_DEFAULT_PRIORITY_WEIGHT = 0.5

# 分档阈值：(score 下限, 档位)
_LEVELS = (
    (6.0, "HIGH"),
    (3.0, "MEDIUM"),
    (1.5, "LOW"),
)


def _parse_dt(value: str, tz: timezone) -> Optional[datetime]:
    """解析日期时间字符串（支持 ISO 时间 / 纯日期）。失败返回 None。"""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        try:
            dt = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%f%z")
        except (ValueError, TypeError):
            try:
                dt = datetime.strptime(value, "%Y-%m-%d")
            except (ValueError, TypeError):
                return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt


def priority_weight(priority: Optional[str]) -> float:
    """优先级权重：P0/Blocker/Highest=3，P1/Critical/High=2，P2/Major/Medium=1，其余/未设置=0.5。"""
    if not priority:
        return _DEFAULT_PRIORITY_WEIGHT
    return _PRIORITY_WEIGHTS.get(str(priority).strip(), _DEFAULT_PRIORITY_WEIGHT)


def time_urgency(duedate: Optional[str], eta_days: int = 7) -> float:
    """时间紧迫度：距目标日期剩余天数 D。无日期 → 1（中性）。

    已超期→3 / <3天→2.5 / <7天→2 / <14天→1.5 / <30天→1 / 更远→1。
    """
    due = _parse_dt(duedate, _BJS_TZ) if duedate else None
    if due is None:
        return 1.0
    # duedate 是纯日期（无时间），按"日期"比较剩余天数，避免当天 00:00 的时差误判
    remaining = (due.date() - datetime.now(_BJS_TZ).date()).days
    if remaining < 0:
        return 3.0   # 已超期
    if remaining < 3:
        return 2.5
    if remaining < 7:
        return 2.0
    if remaining < 14:
        return 1.5
    if remaining < 30:
        return 1.0
    return 1.0


def stagnation_factor(updated: Optional[str], stale_days: int = 7) -> float:
    """状态停滞度：无更新时间 > stale_days → 1.5；正常更新 → 1.0。"""
    upd = _parse_dt(updated, _BJS_TZ) if updated else None
    if upd is None:
        return 1.0
    if (datetime.now(_BJS_TZ) - upd).days >= stale_days:
        return 1.5
    return 1.0


def urgency_score(priority: Optional[str], duedate: Optional[str],
                  updated: Optional[str], eta_days: int = 7,
                  stale_days: int = 7) -> float:
    """紧迫度 = 优先级权重 × 时间紧迫度 × 状态停滞度。"""
    return round(
        priority_weight(priority)
        * time_urgency(duedate, eta_days)
        * stagnation_factor(updated, stale_days),
        2,
    )


def urgency_level(score: float) -> str:
    """分档：≥6 HIGH / ≥3 MEDIUM / ≥1.5 LOW / 其他 NONE。"""
    for threshold, level in _LEVELS:
        if score >= threshold:
            return level
    return "NONE"


def score_issue(issue: dict, eta_days: int = 7, stale_days: int = 7) -> dict:
    """对单个 issue 计算紧迫度，返回评分结果 dict。"""
    priority = issue.get("priority", "")
    duedate = issue.get("duedate", "")
    updated = issue.get("updated", "")
    score = urgency_score(priority, duedate, updated, eta_days, stale_days)
    return {
        "key": issue.get("key", ""),
        "summary": issue.get("summary", ""),
        "priority": priority,
        "duedate": duedate,
        "updated": updated,
        "score": score,
        "level": urgency_level(score),
    }
