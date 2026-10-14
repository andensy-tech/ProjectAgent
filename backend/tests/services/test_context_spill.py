"""
Context Spill 暂存服务单元测试（层 A，2026-09-08）。

覆盖：save_spill 落盘结构 / read_spill_slice 有界取回 / query 过滤 / 越权 user 隔离 /
TTL 过期 / delete / cleanup_expired 兜底清理。全部用 pytest tmp_path，不触碰真实 data 目录。
"""

import json
import os
import time
from pathlib import Path

import pytest

from app.services.context_spill import (
    SPILL_DIR_NAME,
    delete_spill,
    read_spill_slice,
    save_spill,
    cleanup_expired_spills,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture
def spill_root(tmp_path: Path) -> str:
    """隔离的 spill 根目录（每次测试独立）。"""
    return str(tmp_path / SPILL_DIR_NAME)


def _comments(n: int) -> dict:
    """构造 n 条 Jira 评论结构的原始结果。"""
    return {
        "issue_key": "TV-1",
        "total_comments": n,
        "comments": [
            {"id": i, "author": f"user{i}", "created": f"2026-08-{17 + i:02d}T10:00:00+0800", "body": f"评论 {i} 内容"}
            for i in range(n)
        ],
    }


class TestSaveSpill:
    """落盘结构。"""

    async def test_save_returns_id_and_writes_file(self, spill_root: str):
        sid = await save_spill(1, "get_issue_comments", {"issue_key": "TV-1"}, _comments(5), spill_root=spill_root)
        assert sid
        path = Path(spill_root) / "1" / f"{sid}.json"
        assert path.exists()
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert doc["spill_id"] == sid
        assert doc["user_id"] == 1
        assert doc["kind"] == "generic"
        assert doc["total_entries"] == 5

    async def test_save_extracts_list_from_dict(self, spill_root: str):
        """dict 含 comments 列表 → 自动提取为切片 entries。"""
        sid = await save_spill(1, "get_issue_comments", {}, _comments(10), kind="comments", spill_root=spill_root)
        res = await read_spill_slice(1, sid, spill_root=spill_root)
        assert res["success"]
        assert res["total_all"] == 10
        assert res["kind"] == "comments"

    async def test_save_requires_user_id(self, spill_root: str):
        with pytest.raises(ValueError):
            await save_spill(None, "get_issue_comments", {}, _comments(1), spill_root=spill_root)

    async def test_write_is_atomic_no_tmp_leftover(self, spill_root: str):
        sid = await save_spill(1, "t", {}, [1, 2, 3], spill_root=spill_root)
        leftovers = list(Path(spill_root).rglob("*.tmp"))
        assert leftovers == []
        assert (Path(spill_root) / "1" / f"{sid}.json").exists()


class TestReadSpillSlice:
    """有界取回。"""

    async def test_read_with_offset_and_limit(self, spill_root: str):
        sid = await save_spill(1, "get_issue_comments", {}, _comments(57), kind="comments", spill_root=spill_root)
        res = await read_spill_slice(1, sid, offset=0, limit=10, spill_root=spill_root)
        assert res["success"]
        assert res["returned"] == 10
        assert res["total_all"] == 57
        assert len(res["entries"]) == 10

    async def test_read_with_query_filters(self, spill_root: str):
        data = {"comments": [
            {"id": 1, "body": "合入完成，关闭"},
            {"id": 2, "body": "待验证"},
            {"id": 3, "body": "已合入，无需处理"},
        ]}
        sid = await save_spill(1, "get_issue_comments", {}, data, kind="comments", spill_root=spill_root)
        res = await read_spill_slice(1, sid, query="合入", spill_root=spill_root)
        assert res["success"]
        assert res["total"] == 2  # 命中两条含"合入"
        assert res["total_all"] == 3

    async def test_read_nonexistent_returns_error(self, spill_root: str):
        res = await read_spill_slice(1, "nosuchspill", spill_root=spill_root)
        assert not res["success"]
        assert "不存在" in res["error"]

    async def test_read_cross_user_is_denied(self, spill_root: str):
        """user A 不能读 user B 的 spill（目录隔离）。"""
        sid = await save_spill(1, "get_issue_comments", {}, _comments(3), spill_root=spill_root)
        res = await read_spill_slice(2, sid, spill_root=spill_root)
        assert not res["success"]
        assert "不属于当前用户" in res["error"] or "不存在" in res["error"]

    async def test_read_requires_user_id(self, spill_root: str):
        res = await read_spill_slice(None, "x", spill_root=spill_root)
        assert not res["success"]


class TestDeleteAndCleanup:
    """删除与 TTL 清扫。"""

    async def test_delete_removes_file(self, spill_root: str):
        sid = await save_spill(1, "t", {}, [1], spill_root=spill_root)
        assert (Path(spill_root) / "1" / f"{sid}.json").exists()
        ok = await delete_spill(1, sid, spill_root=spill_root)
        assert ok
        assert not (Path(spill_root) / "1" / f"{sid}.json").exists()

    async def test_cleanup_expired_removes_only_expired(self, spill_root: str):
        # 未过期 spill
        fresh = await save_spill(1, "t", {}, [2], ttl_hours=24, spill_root=spill_root)
        # 手动构造一个 expires_at 已过的文件
        expired_dir = Path(spill_root) / "1"
        expired_dir.mkdir(parents=True, exist_ok=True)
        expired_path = expired_dir / "expired001.json"
        expired_path.write_text(json.dumps({
            "spill_id": "expired001", "user_id": 1,
            "created_at": "2020-01-01T00:00:00", "expires_at": "2020-01-02T00:00:00",
            "entries": [1], "meta": {}, "total_entries": 1,
        }, ensure_ascii=False), encoding="utf-8")

        removed = await cleanup_expired_spills(spill_root=spill_root)
        assert removed >= 1
        assert not expired_path.exists()
        assert (Path(spill_root) / "1" / f"{fresh}.json").exists()

    async def test_cleanup_removes_corrupt_files_by_mtime(self, spill_root: str):
        """损坏文件（无法解析 JSON）按 mtime 兜底清理。"""
        corrupt_dir = Path(spill_root) / "1"
        corrupt_dir.mkdir(parents=True, exist_ok=True)
        bad = corrupt_dir / "corrupt.json"
        bad.write_text("{not valid json", encoding="utf-8")
        # 把 mtime 改到很久以前 → 视为过期
        old = time.time() - 30 * 3600
        os.utime(bad, (old, old))

        removed = await cleanup_expired_spills(spill_root=spill_root)
        assert removed >= 1
        assert not bad.exists()


class TestReadFullScenario:
    """端到端：评论超预算 spill → 精确查回。"""

    async def test_spill_then_query_older_comment(self, spill_root: str):
        data = _comments(60)
        sid = await save_spill(1, "get_issue_comments", {"issue_key": "TV-1"}, data, kind="comments", spill_root=spill_root)
        # 有界取回，不整读
        res = await read_spill_slice(1, sid, offset=0, limit=5, spill_root=spill_root)
        assert res["success"]
        assert res["returned"] == 5
        assert res["total_all"] == 60
        # 按关键词查回
        res2 = await read_spill_slice(1, sid, query="评论 30", spill_root=spill_root)
        assert res2["total"] >= 1
        # 越界 offset → 返回空但不报错
        res3 = await read_spill_slice(1, sid, offset=100, limit=5, spill_root=spill_root)
        assert res3["success"]
        assert res3["returned"] == 0
