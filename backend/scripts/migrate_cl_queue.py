"""CL 监控 V1 -> V2 存量迁移：把旧快照/持续跟踪/提醒记录灌进追踪队列。

背景（docs/design/cl_monitoring_v2.md 第六节）：
  V2 用 `cl_jira_states`（一题一行的追踪队列）取代
    - `cl_monitor_snapshots` 的"整轮重查"写入
    - `cl_verified_tracking`（曾 Verified 的持续跟踪列表）
    - `cl_reminders`（同一 Jira 同一状态只提醒一次的记录）
  若不迁移，上线首轮扫描会把所有存量题当成"新题"，并可能一次性给 owner
  发一大批提醒；同时失去"上一轮状态"，无法识别状态迁移。

本脚本做三件事（幂等，可重复执行）：
  1. 建表 `cl_jira_states`（create_all，与 init_db 一致）
  2. 用每个用户的最新快照 + 持续跟踪列表回填队列行（queue_reason='migrated'）
     - status/prev_status 都置为快照里的当前状态，首轮扫描不会误判成"状态变化"
     - cl_* 沿用快照里的 CL 计数
  3. 把 `cl_reminders` 的时间戳迁到 `last_reminded_at`，避免上线当天重复轰炸

用法：
    cd backend && source venv/bin/activate
    python scripts/migrate_cl_queue.py            # 预览（默认，不写库）
    python scripts/migrate_cl_queue.py --apply    # 实际写库

迁移后旧数据保持原样（不删除），确认新流程稳定后再考虑清理。
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.db.session import AsyncSessionLocal, init_db  # noqa: E402
from app.models import User, UserSetting  # noqa: E402
from app.models.cl_jira_state import ClJiraState  # noqa: E402
from app.models.cl_monitor import ClMonitorSnapshot  # noqa: E402


def _parse_dt(value) -> datetime:
    """把各类时间戳字符串解析为 aware datetime。"""
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    text = str(value or "").replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return datetime.now(timezone.utc)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def _load_setting(db, user_id: int, key: str):
    row = (await db.execute(select(UserSetting).where(
        UserSetting.user_id == user_id, UserSetting.key == key))).scalar_one_or_none()
    if row is None:
        return None
    try:
        return json.loads(row.value)
    except (json.JSONDecodeError, TypeError):
        return None


async def _latest_snapshots(db, user_id: int) -> list:
    """取该用户每个项目的最新一条快照。"""
    rows = (await db.execute(
        select(ClMonitorSnapshot).where(ClMonitorSnapshot.user_id == user_id)
        .order_by(ClMonitorSnapshot.id))).scalars().all()
    latest: dict = {}
    for row in rows:
        latest[row.project_name] = row  # 按 id 升序，后者覆盖
    out = []
    for row in latest.values():
        try:
            out.append(json.loads(row.data_json))
        except (json.JSONDecodeError, TypeError):
            continue
    return out


def _plan_rows(snapshots: list, tracking: dict, reminders: dict) -> list:
    """根据快照/跟踪/提醒记录，算出待写入的队列行（纯函数，便于预览）。"""
    planned: dict = {}

    for proj in snapshots:
        project_name = proj.get("project", "")
        for group in proj.get("jira_groups", []) or []:
            key = group.get("jira_key", "")
            if not key:
                continue
            status = group.get("status", "")
            planned[key] = {
                "project_name": project_name,
                "summary": group.get("summary", ""),
                "assignee": group.get("assignee", ""),
                "status": status,
                # 首轮把它当作"已见过这个状态"，避免整批题被判成状态变化
                "prev_status": status,
                "cl_total": group.get("total_cls", 0),
                "cl_merged": group.get("merged_cls", 0),
                "cl_unmerged": group.get("unmerged_cls", 0),
                "cl_noregister": group.get("total_cls", 0) == 0,
                "cl_items_json": json.dumps(group.get("items", []), ensure_ascii=False),
                "queue_reason": "migrated",
            }

    # 持续跟踪列表里的题（可能已不在快照中，如已 Closed）：只保证入队，
    # status 留空由首轮扫描的 get_issue 复查补齐
    for key, entry in (tracking or {}).items():
        if key in planned:
            continue
        planned[key] = {
            "project_name": entry.get("project", ""),
            "summary": "",
            "assignee": "",
            "status": "",
            "prev_status": "",
            "cl_total": 0, "cl_merged": 0, "cl_unmerged": 0,
            "cl_noregister": False,
            "cl_items_json": "[]",
            "queue_reason": "migrated",
        }

    # 提醒时间戳：取该 Jira 各状态里最近的一次
    for key, row in planned.items():
        stamps = (reminders or {}).get(key) or {}
        times = [_parse_dt(v) for v in stamps.values() if v]
        if times:
            row["last_reminded_at"] = max(times)
    return [{"issue_key": k, **v} for k, v in planned.items()]


async def migrate(apply: bool) -> None:
    """执行迁移；apply=False 时只预览。"""
    await init_db()

    async with AsyncSessionLocal() as db:
        user_ids = [r[0] for r in (await db.execute(select(User.id))).fetchall()]
        total_rows = 0

        for user_id in user_ids:
            snapshots = await _latest_snapshots(db, user_id)
            tracking = await _load_setting(db, user_id, "cl_verified_tracking") or {}
            reminders = await _load_setting(db, user_id, "cl_reminders") or {}
            if not snapshots and not tracking:
                continue

            rows = _plan_rows(snapshots, tracking, reminders)
            print(f"\n用户 {user_id}: 待写入 {len(rows)} 条"
                  f"（快照 {len(snapshots)} 个项目 / 跟踪 {len(tracking)} 条）")

            for row in rows:
                issue_key = row["issue_key"]
                existing = (await db.execute(select(ClJiraState).where(
                    ClJiraState.user_id == user_id,
                    ClJiraState.issue_key == issue_key))).scalar_one_or_none()
                if existing is not None:
                    print(f"  - {issue_key} 已存在，跳过")
                    continue
                print(f"  + {issue_key} status={row['status'] or '?'} "
                      f"unmerged={row['cl_unmerged']} "
                      f"last_reminded={row.get('last_reminded_at') or '—'}")
                if apply:
                    db.add(ClJiraState(
                        user_id=user_id,
                        issue_key=issue_key,
                        first_seen_at=datetime.now(timezone.utc),
                        last_seen_at=datetime.now(timezone.utc),
                        queued=True, archived=False,
                        **row,
                    ))
                total_rows += 1

        if apply:
            await db.commit()
            print(f"\n✅ 已写入 {total_rows} 条队列记录")
        else:
            print(f"\nℹ️  预览模式：将写入 {total_rows} 条。加 --apply 实际执行。")


def main() -> None:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description="CL 监控 V1->V2 存量迁移")
    parser.add_argument("--apply", action="store_true", help="实际写库（默认只预览）")
    args = parser.parse_args()
    asyncio.run(migrate(apply=args.apply))


if __name__ == "__main__":
    main()
