"""
Migration: Recreate tables missing FOREIGN KEY constraints.

SQLite only stores FK constraints in the table schema when PRAGMA foreign_keys=ON
at creation time. Existing tables (created with PRAGMA OFF) lack FK enforcement.

This script recreates affected tables with proper FK constraints using the
CREATE TABLE IF NOT EXISTS ... AS SELECT pattern, then re-enables PRAGMA.

Usage: python scripts/migrate_fk_constraints.py
"""

import sqlite3
import sys
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "projectagent.db")

# Tables that have user_id FK with ondelete=CASCADE in models
# but are missing the FK constraint in their SQLite schema
AFFECTED_TABLES = [
    "cl_monitor_snapshots",
    "dashboard_snapshots",
    "metric_snapshots",
    "skill_configs",
    "learning_records",
    "memory_logs",
    "user_profiles",
    "session_memories",
]


def recreate_table(conn, table: str, create_sql: str):
    """Recreate a table with FK constraint enabled."""
    # Read existing data
    try:
        data = conn.execute(f"SELECT * FROM {table}").fetchall()
    except sqlite3.OperationalError:
        print(f"  ⚠️  Table {table} does not exist, skipping")
        return False

    # Get column names
    col_names = [d[0] for d in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    cols = ", ".join(col_names)

    # Create new table with FK
    conn.execute(f"DROP TABLE IF EXISTS {table}_new")
    conn.execute(f"PRAGMA foreign_keys=ON")
    conn.execute(create_sql)

    # Copy data
    if data:
        placeholders = ", ".join(["?" for _ in col_names])
        conn.executemany(f"INSERT INTO {table}_new ({cols}) VALUES ({placeholders})", data)

    # Swap tables
    conn.execute(f"DROP TABLE {table}")
    conn.execute(f"ALTER TABLE {table}_new RENAME TO {table}")
    conn.commit()
    print(f"  ✅ {table}: recreated with FK constraint ({len(data)} rows preserved)")
    return True


def main():
    conn = sqlite3.connect(DB_PATH, timeout=30)

    # Verify FK are missing
    missing = []
    for t in AFFECTED_TABLES:
        sql = conn.execute(f"SELECT sql FROM sqlite_master WHERE type='table' AND name='{t}'").fetchone()
        if sql and "FOREIGN KEY" not in sql[0]:
            missing.append(t)

    if not missing:
        print("✅ 所有表已有FK约束")
        conn.close()
        return

    print(f"发现 {len(missing)} 张表缺失FK约束: {', '.join(missing)}")
    print()

    # Collect CREATE TABLE statements for affected tables
    # We import models within the context of the project
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

    from sqlalchemy import MetaData, create_engine

    # Create a temporary engine with FK enabled to get proper DDL
    tmp_engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    conn_tmp = tmp_engine.connect()
    conn_tmp.execute("PRAGMA foreign_keys=ON")

    # Import models to register them with metadata
    from app.models import (
        ClMonitorSnapshot, DashboardSnapshot, MetricSnapshot,
        SkillConfig, LearningRecord, MemoryLog, UserProfile, SessionMemory,
    )
    from app.models.base import Base

    for table_name in missing:
        # Get the model class for this table
        mapper = None
        for tbl_name, tbl in Base.metadata.tables.items():
            if tbl_name == table_name:
                mapper = tbl
                break
        if mapper is None:
            print(f"  ⚠️  Cannot find model for {table_name}")
            continue

        # Create the table on temp engine (this generates proper DDL with FK)
        Base.metadata.create_all(tmp_engine, tables=[mapper])

        # Get the CREATE TABLE SQL from temp engine
        result = conn_tmp.execute(
            f"SELECT sql FROM sqlite_master WHERE type='table' AND name='{table_name}'"
        ).fetchone()
        if result:
            create_sql = result[0]
            # Replace CREATE TABLE with our table name
            create_sql = create_sql.replace(f'"{table_name}"', f'"{table_name}_new"')
            recreate_table(conn, table_name, create_sql)

    conn_tmp.close()
    tmp_engine.dispose()
    conn.close()

    print("\n✅ 迁移完成")


if __name__ == "__main__":
    main()
