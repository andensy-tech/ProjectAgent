"""CL 的经办人回复检测 — 从 Jira 评论判断 CL 的处置情况。

2026-09-16 CL 监控 V2 拆分后，本模块只保留回复检测：提醒与扫描编排已移到
`cl_scan_service.run_daily_scan`（它在 CL 确认之后调用本模块，按结论做两件事）：

  1. **CL 级**：某 CL 被声明"内部不再需要 merge" -> 标记该 CL 不关注（cl_suppressed）
  2. **Jira 级**：经办人说明该题 CL 已全部处理完毕 -> 停止提醒该题（ack，见
     `cl_scan_service.apply_ack`）。典型句例："wifi porting 的问题在 Jira TV-943550
     中 follow，其他 cl 都已经 merge。"

双通道取并集：
  - LLM 语义判断（配置可用时）：理解整体性/间接的声明；
  - 规则匹配（始终执行）：`_rule_detect_suppressed`（逐 CL 否定词）+
    `_rule_detect_settled`（整体性表述），兜住 LLM 不可用或漏判的场景。

节流：cl_rd_scan 记录每个 Jira 上次分析的评论数，评论数未变则跳过（省 token）。
"""
import logging
import re
from typing import Dict, List, Optional

from app.services.gerrit_service import is_merged

logger = logging.getLogger(__name__)

# 仅对这些 Jira 状态做 RD 检测
NOTIFY_STATUSES = ("Resolved", "Verified")

# 超时
_MCP_TIMEOUT = 30

# LLM 判断评论的节流/长度上限
_RD_SCAN_MAX_COMMENTS = 10
_RD_SCAN_MAX_COMMENT_LEN = 500


async def scan_rd_replies_for_suppression(user_id: int) -> Dict:
    """检测经办人回复，得到 CL 级抑制与 Jira 级「已结算」两类结论。

    双通道取并集：
      - LLM 语义判断（配置可用时）：理解"其他 cl 都已经 merge"、"已转移到其他 Jira
        跟进"这类整体性声明；
      - 规则匹配（始终执行）：兜住 LLM 不可用或漏判的场景（逐 CL 否定词 / 整体性表述）。

    节流：cl_rd_scan 记录每个 Jira 上次分析的评论数，评论数未变则跳过（省 token）。

    Returns:
        {"suppressed": int,                  -- 本轮新增的 CL 级抑制条数
         "analyzed": [jira_key, ...],        -- 本轮真正重新分析的题
         "acks": {jira_key: reason, ...}}    -- 判定为"已结算"的题及依据
        `analyzed` 中不在 `acks` 里的题，表示本轮分析后**不再**判定为已结算，
        调用方据此清除旧的 ack（经办人的新回复推翻了旧结论）。
    """
    from app.services.cl_snapshot_service import load_latest_snapshots
    from app.services.settings_service import (
        add_cl_suppressed, get_cl_rd_scan, save_cl_rd_scan,
    )
    from app.mcp.capability import capability

    result: Dict = {"suppressed": 0, "analyzed": [], "acks": {}}
    snapshots = await load_latest_snapshots(user_id) or []
    if not snapshots:
        return result
    scan = await get_cl_rd_scan(user_id)
    added = 0
    for proj in snapshots:
        for group in proj.get("jira_groups", []) or []:
            # 常规 Resolved/Verified 之外，持续跟踪中的 Closed Jira（曾 Verified、
            # 仍有未合并 CL、会被周期重复 ping）也要扫描——RD 常在 Closed 后回复
            # "不需要合并"（实测 TV-943550 即如此，此前被状态门槛漏掉）
            if group.get("status", "") not in NOTIFY_STATUSES and not group.get("tracking"):
                continue
            # 未登记任何 CL 的题也要扫：经办人可能回复"本 Jira 不需要代码改动"
            if group.get("unmerged_cls", 0) <= 0 and not group.get("cl_unregistered"):
                continue
            jira_key = group.get("jira_key", "")
            try:
                resp = await capability.call(
                    "jira", "get_issue_comments", {"issue_key": jira_key}, user_id, timeout=_MCP_TIMEOUT,
                )
                comments = resp.get("comments", []) if isinstance(resp, dict) else []
            except Exception as e:
                logger.debug("CL auto-notify: get_issue_comments(%s) failed: %s", jira_key, e)
                continue
            n = len(comments)
            if scan.get(jira_key, {}).get("count") == n:
                continue  # 评论数未变，RD 未回复，跳过 LLM 分析

            result["analyzed"].append(jira_key)

            llm_result = await _llm_analyze_cl_suppression(user_id, group, comments)
            # 规则通道始终执行：LLM 不可用（None）时独立兜底；LLM 可用但漏判时
            # 兜住显式声明。两通道取并集，LLM reason 优先。
            rule_result = _rule_detect_suppressed(group, comments)
            per_cl: Dict[str, str] = {}
            for r in rule_result or []:
                per_cl[r["change_id"]] = r.get("reason", "rd_reply_rule")
            for r in (llm_result or {}).get("suppressed", []):
                cid = str(r.get("change_id", "")).strip()
                if cid and cid not in per_cl:
                    per_cl[cid] = r.get("reason", "rd_reply")
            result_items = [{"change_id": cid, "reason": reason}
                            for cid, reason in per_cl.items()]

            # Jira 级「已结算」：LLM 判定 或 规则命中（整体性表述 / 等待外部，取并集）
            settled_reason = ""
            if llm_result and llm_result.get("settled"):
                settled_reason = llm_result.get("reason") or "经办人已说明该 Jira 的 CL 处理完毕"
            if not settled_reason:
                hit = _rule_detect_settled(group, comments)
                if hit:
                    settled_reason = f"规则命中整体性声明：{hit}"
            if not settled_reason:
                hit = _rule_detect_waiting(comments)
                if hit:
                    settled_reason = f"规则命中等待外部：{hit}"
            if settled_reason:
                result["acks"][jira_key] = settled_reason[:500]

            # 只标记 LLM 确认且确实在该 Jira 未合并列表中的 change_id
            valid_ids = {
                it.get("change_id") for it in group.get("items", []) or []
                if not is_merged(it.get("status"))
            }
            entries: List[Dict] = []
            for r in result_items:
                cid = r.get("change_id", "")
                if cid not in valid_ids:
                    continue
                it = next(
                    (x for x in group.get("items", []) if x.get("change_id") == cid), None
                )
                if not it:
                    continue
                entries.append({
                    "instance": it.get("instance"),
                    "change_id": cid,
                    "jira_key": jira_key,
                    "reason": r.get("reason") or "rd_reply",
                })
            if entries:
                before = added
                await add_cl_suppressed(user_id, entries)
                added += len(entries)
                logger.info("CL auto-notify: marked %d CL suppressed for %s (%d new)",
                            len(entries), jira_key, added - before)
            scan[jira_key] = {"count": n}  # 记录已分析评论数
    if scan:
        await save_cl_rd_scan(user_id, scan)
    result["suppressed"] = added
    return result


# 规则兜底：评论中声明"不再需要合并"的明确否定词。
# 必须与 CL 号同时出现在同一条评论中才命中（避免"不需要"单独出现误伤）。
_RULE_NEG_PATTERNS = re.compile(
    r"不需要|不用合|不用了|无需合|不再合|不合入|不\s*[Mm]erge|废弃"
    r"|已转移|转移到|改用其他|取消合并"
)


def _rule_detect_suppressed(group: Dict, comments: list) -> List[Dict]:
    """规则兜底：某条评论中同时出现未合并 CL 的编号和明确否定词时，标记该 CL。

    覆盖典型 RD 声明，如 "68297 公版修改，不需要了"、"68298 已转移到 server 合并"。
    与 LLM 判断互补：未配置 LLM 的环境依赖此规则识别放弃合并的声明。

    Returns:
        [{"change_id": str, "reason": "rd_reply_rule"}, ...]（可为空列表）
    """
    hits: List[Dict] = []
    for it in group.get("items", []) or []:
        if is_merged(it.get("status")):
            continue
        cid = str(it.get("change_id", "") or "").strip()
        if not cid.isdigit():
            continue  # 只匹配纯数字 change 号，避免误匹配其他编号
        cid_re = re.compile(rf"(?<!\d){re.escape(cid)}(?!\d)")
        for c in comments or []:
            body = c.get("body") or ""
            if cid_re.search(body) and _RULE_NEG_PATTERNS.search(body):
                hits.append({"change_id": cid, "reason": "rd_reply_rule"})
                break
    return hits


# 整体性声明（Jira 级）：不点名具体 CL，但表明该题的 CL 已全部处理完毕。
# 必须明显是"整体"语义才命中，避免把"其他CL还没合并"这类继续待办的表述误判成已结算。
_RULE_SETTLED_PATTERNS = re.compile(
    # ①「（所有|其他|其余）CL 都已经 merge」
    r"(所有|全部|其他|其它|其余)\s*(的)?\s*(cl|CL|改动|修改|代码)\s*"
    r"(都|均|已|全部)\s*[^。；;，,]{0,8}(merge|Merge|MERGED|合并|合入)"
    # ②「CL 都合并完了」
    r"|(cl|CL)\s*(都|均)\s*(已经)?\s*(merge|Merge|合并|合入)\s*(完|完毕|了)?"
    # ③「不需要 / 没有 CL」
    r"|(没有|无|不涉及|不需要|无需|不用)\s*(任何)?\s*(cl|CL|代码改动|代码修改|改动)"
    # ④「（已）转移到 <Jira key> / 其他分支 / server」
    r"|(转移|移交)\s*(到|至)\s*([A-Za-z][A-Za-z0-9]*-\d+|其他|其它|别的|jira|Jira|JIRA|分支|server|服务器)"
    # ⑤「在 <Jira key> 中跟进 / follow」
    r"|(jira|Jira|JIRA)\s*[A-Za-z][A-Za-z0-9]*-\d+\s*(中|里)?\s*(follow|跟进|跟踪|处理)"
)


def _rule_detect_settled(group: Dict, comments: list) -> Optional[str]:
    """规则兜底：从评论识别"该 Jira 的 CL 已全部处理完毕"的整体性声明。

    与 LLM 通道取并集：未配置 LLM 时独立兜底；LLM 漏判时兜住显式的整体表述
    （实测句例："wifi porting 的问题在Jira TV-943550 中follow，其他cl 都已经merge。"）。

    Returns:
        命中的原话片段（截断）作为依据；未命中返回 None。
    """
    for c in comments or []:
        body = c.get("body") or ""
        m = _RULE_SETTLED_PATTERNS.search(body)
        if m:
            return body.strip().replace("\n", " ")[:200]
    return None


# 「等待外部」触发词：经办人明确表示需要第三方/客户/供应商配合，暂时无法推进。
# 这类评论与"CL 已全部合并"一样属于 jira_settled —— 催办无意义。
# 注意：要求明确指向"外部"对象（客户/iwedia/供应商/第三方/fae 等），
# 避免把"请内部同事看下"误当成等待外部。
_RULE_WAITING_PATTERNS = re.compile(
    r"(请|让|需要|等待|等)\s*(客户|iwedia|iWedia|供应商|第三方|vendor|external|fae|FAE)"
    r"|(客户|iwedia|iWedia|供应商|第三方|vendor|external)\s*(沟通|确认|回复|配合|处理|确认中)"
    r"|等\s*(客户|iwedia|iWedia|供应商|第三方|vendor)\s*(的)?\s*(回复|确认|答复)"
)


def _rule_detect_waiting(comments: list) -> Optional[str]:
    """规则兜底：识别"需要等待第三方/外部输入"的评论。

    Returns:
        命中的原话片段（截断）作为依据；未命中返回 None。
    """
    for c in comments or []:
        body = c.get("body") or ""
        m = _RULE_WAITING_PATTERNS.search(body)
        if m:
            return body.strip().replace("\n", " ")[:200]
    return None


async def _llm_analyze_cl_suppression(
    user_id: int, group: Dict, comments: list,
) -> Optional[Dict]:
    """用 LLM 判断该 Jira 评论中的 CL 处置声明（CL 级 + Jira 级）。

    两级结论：
      - CL 级 `suppressed`：点名某个 CL「内部不再需要 merge」-> 只抑制该 CL；
      - Jira 级 `settled`：owner 表示该 Jira 的 CL 已全部处理完毕
        （"其他 cl 都已经 merge"、"已转移到其他 Jira 跟进"、"不需要 CL"），
        或 **Jira 需要等待第三方/外部输入**（"请客户和 iwedia 沟通"、"等客户确认"、
        "等供应商回复"）-> 停止对该题的提醒（见 `cl_scan_service.apply_ack`）。

    Returns:
        {"suppressed": [{"change_id","reason"}], "settled": bool, "reason": str}；
        LLM 不可用/失败返回 None。
    """
    from app.services.llm import llm_router, TaskType

    llm_cfg = await _build_user_llm_config(user_id)
    if not llm_cfg:
        return None

    from app.services.gerrit_service import GERRIT_HOST_BY_INSTANCE

    jira_key = group.get("jira_key", "")
    cl_lines = []
    for it in group.get("items", []) or []:
        if is_merged(it.get("status")):
            continue
        host = GERRIT_HOST_BY_INSTANCE.get(it.get("instance", ""), it.get("instance", ""))
        cl_lines.append(
            f"- {it.get('change_id', '')} "
            f"https://{host}/c/"
            f"{it.get('project', '')}/+/{it.get('change_id', '')} "
            f"[{it.get('status', '')}] {it.get('subject', '')}"
        )
    cl_text = "\n".join(cl_lines) or "（无）"
    comments_text = _truncate_comments(comments)

    system_prompt = (
        "你是 Jira CL 合并监控助手。分析给定 Jira 的评论，做两个判断：\n"
        "1) CL 级：哪些 CL 被声明为『内部不再需要 merge』。典型情况："
        "『内部不merge，需要同步至google server merge』、『不需要合入』、"
        "『改用其他CL』、『该CL废弃』。这种必须点名具体 CL 编号。\n"
        "2) Jira 级：经办人是否已经说明『该 Jira 的 CL 全部处理完毕，无需再跟进』，"
        "或者『该 Jira 需要等待第三方/外部输入，暂时无法推进』。"
        "前者的典型情况：『其他 cl 都已经 merge』、『剩余的已在 XX 分支合并』、"
        "『该问题已转移到 Jira XXX 跟进』、『本 Jira 不需要代码改动/没有 CL』。"
        "后者的典型情况：『请客户和 XXX 沟通』、『等客户确认/回复后再看』、"
        "『需要 iwedia / 供应商 / 第三方配合，暂时无法推进』——"
        "这类评论**也是** jira_settled=true（等待外部 != 忘了验证，催办无意义）。\n"
        "注意：讨论『是否/何时/请求合入』、『正在合入』、『马上提交』一律不算。"
        "拿不准时 jira_settled 取 false（宁可不停止提醒，也不要漏掉真的待办）。"
        "只输出 JSON，不要任何额外文字。"
    )
    user_prompt = (
        f"Jira: {jira_key}\n"
        f"未合并 CL 列表:\n{cl_text}\n\n"
        f"该 Jira 的评论:\n{comments_text}\n\n"
        "请输出 JSON：\n"
        '{"suppressed": [{"change_id": "66835", "reason": "内部不merge需同步google"}],'
        ' "jira_settled": false, "jira_reason": ""}\n'
        "suppressed 只填评论中点名且属于上面未合并列表的 CL；没有就填空数组。\n"
        "jira_settled 表示经办人是否已说明该 Jira 的 CL 全部处理完毕；"
        "是则 true 并在 jira_reason 里用一句话写出依据（尽量引用原话）。"
    )

    schema = {
        "type": "object",
        "properties": {
            "suppressed": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "change_id": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                    "required": ["change_id"],
                },
            },
            "jira_settled": {"type": "boolean"},
            "jira_reason": {"type": "string"},
        },
        "required": ["suppressed", "jira_settled"],
    }

    try:
        data = await llm_router.route_and_complete_structured(
            task_type=TaskType.RISK_ANALYSIS,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            schema=schema,
            api_key=llm_cfg.get("api_key"),
            api_base=llm_cfg.get("api_base"),
            model=llm_cfg.get("model"),
            default_provider=llm_cfg.get("default_provider"),
        )
        if not isinstance(data, dict):
            return None
        suppressed = data.get("suppressed", []) or []
        return {
            "suppressed": [
                {"change_id": str(r.get("change_id", "")).strip(),
                 "reason": str(r.get("reason", "rd_reply"))}
                for r in suppressed if isinstance(r, dict) and r.get("change_id")
            ],
            "settled": bool(data.get("jira_settled")),
            "reason": str(data.get("jira_reason", "") or ""),
        }
    except Exception as e:
        logger.warning("CL auto-notify: LLM analyze suppressed failed for %s: %s", jira_key, e)
        return None


async def _build_user_llm_config(user_id: int) -> Optional[Dict]:
    """获取用户 LLM 配置（default_llm 对应 provider 的 api_key/api_base/model，未脱敏）。

    Returns:
        {"default_provider", "api_key", "api_base", "model"} 或 None（两 provider 均无 key）。
    """
    from app.services.settings_service import load_user_settings

    try:
        raw = await load_user_settings(user_id)
    except Exception as e:
        logger.warning("CL auto-notify: load_user_settings(%s) failed: %s", user_id, e)
        return None
    raw_llm = raw.get("llm", {}) if isinstance(raw, dict) else {}
    default_provider = raw_llm.get("default_llm", "commercial")
    prefix = "commercial_" if default_provider == "commercial" else "local_"
    api_key = raw_llm.get(f"{prefix}api_key", "")
    if not api_key:
        # 回退到另一 provider
        default_provider = "local" if default_provider == "commercial" else "commercial"
        prefix = "commercial_" if default_provider == "commercial" else "local_"
        api_key = raw_llm.get(f"{prefix}api_key", "")
    if not api_key:
        return None
    return {
        "default_provider": default_provider,
        "api_key": api_key,
        "api_base": raw_llm.get(f"{prefix}api_base", ""),
        "model": raw_llm.get(f"{prefix}model", ""),
    }


def _truncate_comments(comments: list) -> str:
    """把评论列表截断为 LLM 友好的文本（最近 N 条、每条限长）。"""
    items = []
    for c in comments or []:
        body = (c.get("body") or "")
        author = c.get("author", "Unknown")
        created = c.get("created", "")
        body = body.replace("\r", " ").replace("\n", " ")
        if len(body) > _RD_SCAN_MAX_COMMENT_LEN:
            body = body[:_RD_SCAN_MAX_COMMENT_LEN] + "..."
        items.append(f"[{author} @ {created}] {body}")
    return "\n".join(items[-_RD_SCAN_MAX_COMMENTS:])
