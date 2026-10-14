"""清理 CL 监控 V1 遗留的 UserSetting 孤儿数据。

背景（2026-09-18 CL 监控 V2 收尾）：
  提醒去重已由 `cl_jira_states.last_reminded_at` 承载，曾 Verified 的持续跟踪
  已由同表的 `queued`/`archived` 字段承载。但 user_settings 表里还留着 V1 的
  两份 JSON —— `cl_reminders` / `cl_verified_tracking` —— 已经没有代码读写它们，
  留着会让人误以为还在生效。本脚本删除这些孤儿行。

幂等：删除后重复运行只会报"无可清理项"。

用法：
    cd backend && source venv/bin/activate
    python scripts/cleanup_cl_legacy.py            # 预览（默认，不删）
    python scripts/cleanup_cl_legacy.py --apply    # 实际删除
"""

import argparse
import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, select  # noqa: E402

from app.db.session import AsyncSessionLocal  # noqa: E402
from app.models import UserSetting  # noqa: E402

LEGACY_KEYS = ("cl_reminders", "cl_verified_tracking")


async def cleanup(apply: bool) -> None:
    """删除 V1 遗留的两份 UserSetting；apply=False 时只统计。"""
    async with AsyncSessionLocal() as db:
        for key in LEGACY_KEYS:
            rows = (await db.execute(select(UserSetting).where(
                UserSetting.key == key))).scalars().all()
            total = len(rows)
            deleted = 0
            for row in rows:
                print(f"  [{row.user_id}] {key} 长度={len(row.value)} 字节")
                if apply:
                    await db.execute(delete(UserSetting).where(UserSetting.id == row.id))
                    deleted += 1
            if apply:
                print(f"  -> 已删除 {deleted}/{total} 条 {key}")
            else:
                print(f"  -> 预览：{total} 条 {key} 待删除（--apply 实际执行）")
        if apply:
            await db.commit()
            print("\n✅ 清理完成")
        else:
            print("\nℹ️  预览模式，未做任何修改")


def main() -> None:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description="清理 CL 监控 V1 遗留 UserSetting")
    parser.add_argument("--apply", action="store_true", help="实际删除（默认只预览）")
    args = parser.parse_args()
    asyncio.run(cleanup(apply=args.apply))


if __name__ == "__main__":
    main()
