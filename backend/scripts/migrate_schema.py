"""
Schema migration script — multi-user model changes.

Migrates existing SQLite database to reflect model changes:
  1. alerts      — ADD project_name, user_id (project_id FK removed at code level)
  2. metric_snapshots — ADD user_id
  3. memory tables    — user_id String→Integer (requires table recreation)

Usage:
    cd backend && source venv/bin/activate
    python scripts/migrate_schema.py          # dry-run (no changes)
    python scripts/migrate_schema.py --apply  # execute changes

Safe to run multiple times (idempotent).
"""

import asyncio
import sqlite3
import sys
import os

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.config import settings

DRY_RUN = "--apply" not in sys.argv


def log(msg: str) -> None:
    tag = "🔍 DRY-RUN" if DRY_RUN else "✅ MIGRATE"
    print(f"[{tag}] {msg}")


def has_column(cursor: sqlite3.Cursor, table: str, col: str) -> bool:
    cursor.execute(f"PRAGMA table_info({table})")
    return any(row[1] == col for row in cursor.fetchall())


async def migrate():
    db_url = settings.DATABASE_URL
    if db_url.startswith("sqlite"):
        db_path = db_url.replace("sqlite+aiosqlite:///", "")
    elif db_url.startswith("sqlite:///"):
        db_path = db_url.replace("sqlite:///", "")
    else:
        log(f"Unsupported DB URL (only SQLite supported): {db_url}")
        return

    if not os.path.exists(db_path):
        log(f"Database not found: {db_path} — nothing to migrate")
        return

    log(f"Database: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = OFF")
    cursor = conn.cursor()

    # ── 1. Alerts: add project_name, user_id ────────────
    if not has_column(cursor, "alerts", "project_name"):
        log("alerts → ADD project_name (VARCHAR(200))")
        if not DRY_RUN:
            cursor.execute("ALTER TABLE alerts ADD COLUMN project_name VARCHAR(200) DEFAULT ''")
    else:
        log("alerts → project_name already exists, skipping")

    if not has_column(cursor, "alerts", "user_id"):
        log("alerts → ADD user_id (INTEGER)")
        if not DRY_RUN:
            cursor.execute("ALTER TABLE alerts ADD COLUMN user_id INTEGER")
    else:
        log("alerts → user_id already exists, skipping")

    # ── 1.5. Users: add feishu_open_id ─────────────────
    if not has_column(cursor, "users", "feishu_open_id"):
        log("users → ADD feishu_open_id (VARCHAR(64) UNIQUE)")
        if not DRY_RUN:
            cursor.execute("ALTER TABLE users ADD COLUMN feishu_open_id VARCHAR(64)")
    else:
        log("users → feishu_open_id already exists, skipping")

    # ── 2. MetricSnapshots: add user_id ──────────────────
    if not has_column(cursor, "metric_snapshots", "user_id"):
        log("metric_snapshots → ADD user_id (INTEGER)")
        if not DRY_RUN:
            cursor.execute("ALTER TABLE metric_snapshots ADD COLUMN user_id INTEGER")
    else:
        log("metric_snapshots → user_id already exists, skipping")

    # ── 3. Drop deprecated projects table ────────────────
    if has_column(cursor, "projects", "id"):
        log("projects → DROP TABLE (replaced by user_settings JSON config)")
        if not DRY_RUN:
            cursor.execute("DROP TABLE IF EXISTS projects")
    else:
        log("projects → table already dropped, skipping")

    # ── 4. Recreate skill_configs: project_id → user_id ──
    if has_column(cursor, "skill_configs", "project_id"):
        log("skill_configs → DROP + recreate (project_id → user_id)")
        if not DRY_RUN:
            cursor.execute("DROP TABLE IF EXISTS skill_configs")
            cursor.execute("""
                CREATE TABLE skill_configs (
                    id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    skill_name VARCHAR(100) NOT NULL,
                    config_json JSON DEFAULT '{}' NOT NULL,
                    description TEXT,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    PRIMARY KEY (id)
                )
            """)
            cursor.execute("CREATE UNIQUE INDEX ix_skillcfg_user_skill ON skill_configs (user_id, skill_name)")
    else:
        log("skill_configs → already migrated, skipping")

    # ── 5. Memory tables: user_id String→Integer ────────
    # SQLite cannot ALTER COLUMN type, so recreate tables.
    MEMORY_TABLES = [
        ("user_profiles", "user_profiles_old"),
        ("learning_records", "learning_records_old"),
        ("memory_logs", "memory_logs_old"),
        ("session_memories", "session_memories_old"),
    ]

    for table, old_table in MEMORY_TABLES:
        if not has_column(cursor, table, "user_id"):
            log(f"{table} → user_id column missing, skipping")
            continue

        # Check if user_id is already INTEGER (via PRAGMA table_info type)
        cursor.execute(f"PRAGMA table_info({table})")
        cols = {row[1]: row[2] for row in cursor.fetchall()}
        current_type = cols.get("user_id", "").upper()

        if current_type in ("INTEGER", "INT"):
            log(f"{table} → user_id already INTEGER, skipping")
            continue

        log(f"{table} → user_id type is {current_type}, recreating table...")

        if DRY_RUN:
            continue

        # Step 1: Rename old table
        cursor.execute(f"ALTER TABLE {table} RENAME TO {old_table}")

        # Step 2: Recreate with correct schema (via PRAGMA schema of old table with Integer user_id)
        cursor.execute(f"SELECT sql FROM sqlite_master WHERE type='table' AND name='{old_table}'")
        create_sql = cursor.fetchone()[0]
        # Replace user_id VARCHAR/STRING/TEXT type with INTEGER
        import re
        new_sql = re.sub(
            r'user_id\s+\w+(?:\(\d+\))?',
            'user_id INTEGER',
            create_sql.replace(old_table, table),
            flags=re.IGNORECASE,
        )
        cursor.execute(new_sql)

        # Step 3: Copy data — cast existing user_id values to INTEGER
        # SQLite's flexible typing means VARCHAR values like "1" get cast to INTEGER 1.
        cursor.execute(f"SELECT * FROM {old_table}")
        rows = cursor.fetchall()
        col_names = [desc[0] for desc in cursor.description]

        if rows:
            placeholders = ",".join("?" for _ in col_names)
            col_list = ",".join(col_names)
            for row in rows:
                cursor.execute(
                    f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})",
                    row,
                )

        # Step 4: Drop old table
        cursor.execute(f"DROP TABLE {old_table}")
        log(f"{table} → migrated {len(rows)} rows")

    conn.commit()
    conn.close()
    log("Migration complete!")


if __name__ == "__main__":
    if DRY_RUN:
        print("=== DRY-RUN MODE (pass --apply to execute) ===")
    else:
        print("=== APPLY MODE ===")
    asyncio.run(migrate())
