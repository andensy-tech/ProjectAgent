"""
将 config/user_settings.json 中的配置迁移到 user_settings 表 (user_id=1 admin)。

用法:
    cd backend && python -m scripts.migrate_user_settings

说明:
    多用户改造后,用户的仪表盘/CL监控配置存储在 DB user_settings 表中,
    通过 user_id 关联。旧配置还留在 config/user_settings.json 文件中,
    此脚本将其迁移到 admin 用户 (user_id=1) 的 DB 记录中。

    迁移后 JSON 文件保留不动,后台任务仍会读取它。
"""

import json
import asyncio
import os
import sys

# 确保能找到 backend 包
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

SETTINGS_FILE = os.path.join(
    os.path.dirname(__file__), "..", "config", "user_settings.json",
)


async def migrate():
    # 1. 读取 JSON 文件
    if not os.path.exists(SETTINGS_FILE):
        print(f"[迁移] user_settings.json 不存在: {SETTINGS_FILE}")
        return

    with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
        file_data = json.load(f)

    if not file_data:
        print("[迁移] user_settings.json 为空,无需迁移")
        return

    print(f"[迁移] 从 {SETTINGS_FILE} 读取到顶层 key: {list(file_data.keys())}")

    # 2. 写入 DB user_settings 表 (user_id=1)
    from app.db.session import AsyncSessionLocal
    from app.models import UserSetting
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        for key, value in file_data.items():
            # 检查是否已存在同名 key
            result = await db.execute(
                select(UserSetting).where(
                    UserSetting.user_id == 1,
                    UserSetting.key == key,
                )
            )
            existing = result.scalar_one_or_none()
            if existing:
                old_val = existing.value[:100]
                new_val = json.dumps(value, ensure_ascii=False)
                existing.value = new_val
                print(f"[迁移] 更新 user_id=1, key={key} (旧值前100字符: {old_val}...)")
            else:
                db.add(UserSetting(
                    user_id=1,
                    key=key,
                    value=json.dumps(value, ensure_ascii=False),
                ))
                print(f"[迁移] 新增 user_id=1, key={key}")

        await db.commit()

    # 3. 验证（直接用 SQL 查询验证写入结果）
    from app.db.session import AsyncSessionLocal
    from app.models import UserSetting
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(UserSetting).where(UserSetting.user_id == 1)
        )
        rows = result.scalars().all()
        print(f"\n[迁移] 验证结果: user_settings 表共 {len(rows)} 条 (user_id=1)")
        for row in rows:
            val = row.value
            if len(val) > 120:
                val = val[:120] + "..."
            print(f"   key={row.key}, value={val}")

    print("\n[迁移] 完成! JSON 文件已保留,后台任务仍会用该文件读取配置。")


if __name__ == "__main__":
    asyncio.run(migrate())
