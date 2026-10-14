"""
Hybrid Search Engine — FTS5 BM25 full-text search + optional vector embedding search.

Replaces the old pure-keyword-substring memory_search with a proper
SQLite FTS5 index that supports BM25 scoring and CJK tokenization.

Architecture:
  - FTS5 index file: data/search/index.db (local SQLite, independent of main DB)
  - BM25 keyword search via FTS5 trigram tokenizer (handles CJK for 3+ char queries)
  - Python substring fallback for short queries (1-2 chars)
  - Vector search via LLM embedding + cosine similarity (optional, graceful fallback)
  - Hybrid: weighted combination of BM25 + vector scores
"""

import json
import math
import re
import logging
import sqlite3
import asyncio
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


def _default_index_path() -> str:
    """Default path for the FTS5 search index database."""
    # <project_root>/data/search/index.db
    return str(Path(__file__).resolve().parent.parent.parent.parent / "data" / "search" / "index.db")


class SearchEngine:
    """Hybrid search engine backed by SQLite FTS5.

    Usage:
        engine = SearchEngine()
        await engine.sync_from_db(db, user_id)  # sync on startup
        results = await engine.search("项目延期风险")
        await engine.add_entry("key", "content", "category", "learning_record")
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or _default_index_path()
        self._init_schema()

    # ── Schema Management ──────────────────────────────────────────────

    def _get_conn(self) -> sqlite3.Connection:
        """Get a thread-local SQLite connection."""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_schema(self) -> None:
        """Create FTS5 tables if they don't exist."""
        conn = self._get_conn()
        try:
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS memory_idx USING fts5(
                    key UNINDEXED,
                    content,
                    category UNINDEXED,
                    entry_type UNINDEXED,
                    tokenize='trigram'
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memory_embeddings (
                    key TEXT PRIMARY KEY,
                    embedding BLOB,
                    updated_at TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            conn.commit()
        finally:
            conn.close()

    # ── Public API ─────────────────────────────────────────────────────

    async def sync_from_db(self, db: AsyncSession, user_id: int) -> Dict[str, Any]:
        """Sync the search index from the main application DB.

        Loads learning_records and user_profile preferences,
        then bulk-upserts into the FTS5 index with user_id prefix in keys
        to isolate each user's entries.

        Safe to call on every startup (only updates if data changed).
        """
        from app.crud.memory import (
            list_learning_records as db_list_learning_records,
            get_user_profile as db_get_user_profile,
        )

        stats = {"learning_records": 0, "preferences": 0, "errors": 0}

        # Run DB queries in executor (they're async)
        records = await db_list_learning_records(db, user_id, limit=200)
        profile = await db_get_user_profile(db, user_id)

        def _bulk_sync():
            conn = self._get_conn()
            try:
                # 1. Sync learning records with user_id prefix
                for r in records:
                    content = f"{r.pattern or ''} {r.context or ''}"
                    if not content.strip():
                        continue
                    key = f"u{user_id}_lr_{r.id}"
                    conn.execute(
                        "INSERT OR REPLACE INTO memory_idx(key, content, category, entry_type) VALUES (?, ?, ?, ?)",
                        (key, content, "learning_record", "learning_record"),
                    )
                    stats["learning_records"] += 1

                # 2. Sync user profile preferences with user_id prefix
                if profile and profile.learned_preferences:
                    for pref_key, pref_value in profile.learned_preferences.items():
                        content = f"{pref_key}: {json.dumps(pref_value, ensure_ascii=False)}"
                        conn.execute(
                            "INSERT OR REPLACE INTO memory_idx(key, content, category, entry_type) VALUES (?, ?, ?, ?)",
                            (f"u{user_id}_pref_{pref_key}", content, "user_preference", "user_preference"),
                        )
                        stats["preferences"] += 1

                conn.commit()
            except Exception as e:
                logger.error("FTS5 sync failed: %s", e)
                stats["errors"] += 1
            finally:
                conn.close()

        await asyncio.to_thread(_bulk_sync)
        logger.debug("FTS5 index synced: %s", stats)
        return stats

    async def search(
        self,
        query: str,
        mode: str = "hybrid",
        limit: int = 20,
        user_id: Optional[int] = None,
        default_provider: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Search the memory index using hybrid (FTS5 + vector) retrieval.

        For short queries (< 3 chars), falls back to Python substring matching
        since the trigram tokenizer requires 3+ characters.

        Results are filtered by user_id using the key prefix (u{user_id}_),
        ensuring cross-user memory isolation.

        Args:
            query: Search query string.
            mode: "fts5" | "vector" | "hybrid" (default: "hybrid").
            limit: Max results to return.
            user_id: User ID for filtering. Results are scoped to this user.

        Returns:
            Dict with entries, total_matches, query, and message.
        """
        results = []

        # For very short queries, trigram won't work — use Python substring match
        if len(query.strip()) < 3:
            fts5_results = await self._substring_search(query, limit, user_id=user_id)
            results.extend(fts5_results)
        else:
            # 1. FTS5 BM25 search (trigram handles CJK for 3+ char queries)
            fts5_results = await self._fts5_search(query, limit, user_id=user_id)
            results.extend(fts5_results)

        # 2. Vector search (optional, depends on LLM embedding)
        if mode in ("vector", "hybrid") and len(query.strip()) >= 3:
            try:
                vector_results = await self._vector_search(query, limit * 2, user_id=user_id, default_provider=default_provider)
                results = self._hybrid_merge(results, vector_results)
            except Exception as e:
                logger.debug("Vector search unavailable, falling back to FTS5: %s", e)

        # 3. Filter by user_id (all results from FTS/substring already filtered,
        #    but vector results may include other users' entries)
        if user_id is not None:
            prefix = f"u{user_id}_"
            results = [r for r in results if r.get("key", "").startswith(prefix)]

        # 4. Score normalization and sorting
        results = self._deduplicate(results)
        results.sort(key=lambda x: x.get("score", 0), reverse=True)

        top = results[:limit]

        return {
            "result": "memory_search",
            "query": query,
            "entries": top,
            "total_matches": len(top),
            "search_mode": mode,
            "message": f"找到 {len(top)} 条相关记忆" if top else "未找到匹配的记忆。",
        }

    async def add_entry(
        self,
        key: str,
        content: str,
        category: str = "note",
        entry_type: str = "learning_record",
        user_id: Optional[int] = None,
    ) -> None:
        """Add or update a single entry in the search index.

        If user_id is provided, the key is automatically prefixed with
        u{user_id}_ to maintain per-user isolation.

        Called after memory_save to keep the index in sync.
        """
        if not content.strip():
            return

        # Prefix key with user_id for cross-user isolation
        storage_key = f"u{user_id}_{key}" if user_id is not None else key

        def _add():
            conn = self._get_conn()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO memory_idx(key, content, category, entry_type) VALUES (?, ?, ?, ?)",
                    (storage_key, content, category, entry_type),
                )
                conn.commit()
            finally:
                conn.close()

        await asyncio.to_thread(_add)

    async def remove_entry(self, key: str) -> None:
        """Remove an entry from the search index."""
        def _remove():
            conn = self._get_conn()
            try:
                conn.execute("DELETE FROM memory_idx WHERE key = ?", (key,))
                conn.execute("DELETE FROM memory_embeddings WHERE key = ?", (key,))
                conn.commit()
            finally:
                conn.close()

        await asyncio.to_thread(_remove)

    # ── FTS5 Search ────────────────────────────────────────────────────

    async def _fts5_search(self, query: str, limit: int,
                           user_id: Optional[int] = None) -> List[Dict]:
        """FTS5 BM25 full-text search.

        Uses SQLite FTS5's built-in BM25 ranking. The unicode61 tokenizer
        handles CJK characters by treating each character as a token,
        which gives reasonable Chinese search without an external segmenter.
        """
        # Sanitize query for FTS5 syntax
        safe_query = self._sanitize_fts5_query(query)

        def _search():
            conn = self._get_conn()
            try:
                if safe_query:
                    sql = """SELECT key, content, category, entry_type, bm25(memory_idx, 0, 1.0, 1.0) AS score
                               FROM memory_idx
                               WHERE memory_idx MATCH ?"""
                    params = [safe_query]
                    if user_id is not None:
                        sql += " AND key LIKE ?"
                        params.append(f"u{user_id}_%")
                    sql += " ORDER BY score LIMIT ?"
                    params.append(limit)
                    cursor = conn.execute(sql, params)
                else:
                    sql = """SELECT key, content, category, entry_type, 0.0 AS score
                               FROM memory_idx"""
                    params = []
                    if user_id is not None:
                        sql += " WHERE key LIKE ?"
                        params.append(f"u{user_id}_%")
                    sql += " LIMIT ?"
                    params.append(limit)
                    cursor = conn.execute(sql, params)

                rows = cursor.fetchall()
                # BM25 returns lower = better. Convert to higher = better, normalize to 0-1.
                if not rows:
                    return []
                scores = [r[4] for r in rows]
                min_s = min(scores) if scores else 0
                max_s = max(scores) if scores else 1
                range_s = max_s - min_s if max_s != min_s else 1

                results = []
                for r in rows:
                    normalized = 1.0 - ((r[4] - min_s) / range_s) if safe_query else 0.5
                    results.append({
                        "key": r[0],
                        "content": r[1],
                        "category": r[2],
                        "entry_type": r[3],
                        "score": round(normalized, 4),
                        "source": "fts5",
                    })
                return results

            finally:
                conn.close()

        return await asyncio.to_thread(_search)

    # ── Vector Search ──────────────────────────────────────────────────

    async def _vector_search(self, query: str, limit: int,
                               user_id: Optional[int] = None,
                               default_provider: Optional[str] = None) -> List[Dict]:
        """Vector embedding search via cosine similarity.

        Requires an LLM client that supports embed(). If embedding fails
        or no embeddings exist, returns empty list (graceful fallback).

        Results are scoped to user_id via the key prefix (u{user_id}_),
        filtering at the SQL level rather than in post-processing.
        """
        from app.services.llm import llm_router, TaskType
        from app.services.llm.client import llm_client

        # 1. Generate embedding for query
        model_type = default_provider or "commercial"
        fallback_type = "local" if model_type == "commercial" else "commercial"
        try:
            query_embedding = await llm_client.embed([query], model_type=model_type)
        except Exception:
            try:
                query_embedding = await llm_client.embed([query], model_type=fallback_type)
            except Exception:
                logger.debug("Embedding service unavailable for vector search")
                return []

        if not query_embedding or not query_embedding[0]:
            return []

        query_vec = query_embedding[0]

        # 2. Load stored embeddings (filtered by user_id at SQL level) and compute cosine similarity
        def _search_vectors():
            conn = self._get_conn()
            try:
                sql = "SELECT key, embedding FROM memory_embeddings WHERE embedding IS NOT NULL"
                params = []
                if user_id is not None:
                    sql += " AND key LIKE ?"
                    params.append(f"u{user_id}_%")
                cursor = conn.execute(sql, params)
                rows = cursor.fetchall()
            finally:
                conn.close()

            if not rows:
                return []

            results = []
            for key, embedding_blob in rows:
                try:
                    stored_vec = json.loads(embedding_blob)
                    sim = self._cosine_similarity(query_vec, stored_vec)
                    results.append({"key": key, "score": sim, "source": "vector"})
                except (json.JSONDecodeError, TypeError, ValueError):
                    continue

            results.sort(key=lambda x: x["score"], reverse=True)
            return results[:limit]

        return await asyncio.to_thread(_search_vectors)

    # ── Hybrid Merge ───────────────────────────────────────────────────

    def _hybrid_merge(
        self,
        fts5_results: List[Dict],
        vector_results: List[Dict],
        vector_weight: float = 0.7,
        keyword_weight: float = 0.3,
    ) -> List[Dict]:
        """Weighted merge of FTS5 and vector results.

        All results are combined by key. If a key appears in both sources,
        the final score is: keyword_weight * fts5_score + vector_weight * vec_score.
        If a key appears only in one source, use that score (with the weight).

        Default weights (vector 0.7, keyword 0.3) match OpenClaw's defaults.
        """
        merged = {}

        # Index FTS5 results by key
        for r in fts5_results:
            merged[r.get("key", "")] = {
                "key": r.get("key", ""),
                "content": r.get("content", ""),
                "category": r.get("category", ""),
                "entry_type": r.get("entry_type", ""),
                "score": r.get("score", 0.5) * keyword_weight,
                "source": "hybrid",
            }

        # Merge vector results
        for r in vector_results:
            key = r.get("key", "")
            vec_score = r.get("score", 0) * vector_weight
            if key in merged:
                merged[key]["score"] = merged[key]["score"] + vec_score
            else:
                # Look up content from FTS5 index or leave empty
                merged[key] = {
                    "key": key,
                    "content": "",
                    "category": "",
                    "entry_type": "",
                    "score": vec_score,
                    "source": "hybrid",
                }

        # Enrich vector-only results with content if possible
        result_list = list(merged.values())

        # Normalize final scores to 0-1
        if result_list:
            scores = [r["score"] for r in result_list]
            min_s, max_s = min(scores), max(scores)
            score_range = max_s - min_s if max_s != min_s else 1
            for r in result_list:
                r["score"] = round((r["score"] - min_s) / score_range, 4)

        return result_list

    # ──── Embedding Management ─────────────────────────────────────────

    async def _compute_and_store_embedding(self, key: str, content: str, default_provider: Optional[str] = None) -> None:
        """Compute embedding for content and store it."""
        from app.services.llm.client import llm_client

        model_type = default_provider or "commercial"
        fallback_type = "local" if model_type == "commercial" else "commercial"
        try:
            embedding = await llm_client.embed([content], model_type=model_type)
        except Exception:
            try:
                embedding = await llm_client.embed([content], model_type=fallback_type)
            except Exception:
                return

        if not embedding or not embedding[0]:
            return

        blob = json.dumps(embedding[0])

        def _store():
            conn = self._get_conn()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO memory_embeddings(key, embedding, updated_at) VALUES (?, ?, ?)",
                    (key, blob, datetime.utcnow().isoformat()),
                )
                conn.commit()
            finally:
                conn.close()

        await asyncio.to_thread(_store)

    async def compute_all_embeddings(self) -> Dict[str, Any]:
        """Backfill embeddings for all entries in the FTS5 index.

        Call this once after setting up the search index, or via a maintenance task.
        """
        def _get_keys_without_embeddings():
            conn = self._get_conn()
            try:
                cursor = conn.execute(
                    """SELECT idx.key, idx.content FROM memory_idx idx
                       LEFT JOIN memory_embeddings emb ON idx.key = emb.key
                       WHERE emb.key IS NULL"""
                )
                return cursor.fetchall()
            finally:
                conn.close()

        entries = await asyncio.to_thread(_get_keys_without_embeddings)
        stats = {"total": len(entries), "computed": 0, "failed": 0}

        for key, content in entries:
            try:
                await self._compute_and_store_embedding(key, content)
                stats["computed"] += 1
            except Exception:
                stats["failed"] += 1

            # Small delay to avoid rate limiting
            if stats["computed"] % 5 == 0:
                await asyncio.sleep(0.1)

        logger.info("Embedding backfill: %s", stats)
        return stats

    # ── Helpers ────────────────────────────────────────────────────────

    async def _substring_search(self, query: str, limit: int,
                                 user_id: Optional[int] = None) -> List[Dict]:
        """Python substring matching fallback for short queries (< 3 chars).

        The FTS5 trigram tokenizer requires 3+ characters, so for very short
        queries (1-2 chars) we scan all entries and do Python-side matching.
        Score is based on match position (earlier match = higher score).
        """
        query_lower = query.strip().lower()
        if not query_lower:
            return []

        def _search():
            conn = self._get_conn()
            try:
                cursor = conn.execute(
                    "SELECT key, content, category, entry_type FROM memory_idx"
                )
                rows = cursor.fetchall()
            finally:
                conn.close()

            results = []
            for r in rows:
                key, content, category, entry_type = r
                # Filter by user_id prefix if specified
                if user_id is not None and not key.startswith(f"u{user_id}_"):
                    continue
                if not content:
                    continue
                if query_lower in content.lower():
                    pos = content.lower().find(query_lower)
                    score = max(0, 1.0 - (pos / max(len(content), 1)) * 0.5)
                    results.append({
                        "key": key,
                        "content": content,
                        "category": category,
                        "entry_type": entry_type,
                        "score": round(score, 4),
                        "source": "substring",
                    })

            results.sort(key=lambda x: x["score"], reverse=True)
            return results[:limit]

        return await asyncio.to_thread(_search)

    @staticmethod
    def _sanitize_fts5_query(query: str) -> str:
        """Sanitize user query for FTS5 syntax.

        Escapes special FTS5 characters and handles CJK text.
        For short CJK queries without special operators, wraps in double
        quotes for phrase matching.
        """
        if not query or not query.strip():
            return ""

        # Escape FTS5 special characters
        special_chars = ['^', '*', '"', '(', ')', '{', '}', '~', ':']
        sanitized = query.strip()

        for ch in special_chars:
            sanitized = sanitized.replace(ch, ' ')

        # Collapse whitespace
        sanitized = ' '.join(sanitized.split())

        if not sanitized:
            return ""

        # For short queries, use prefix matching (better for CJK)
        if len(sanitized) <= 10:
            return f'"{sanitized}"*'

        return f'"{sanitized}"'

    @staticmethod
    def _cosine_similarity(vec_a: List[float], vec_b: List[float]) -> float:
        """Compute cosine similarity between two vectors."""
        if not vec_a or not vec_b or len(vec_a) != len(vec_b):
            return 0.0

        dot = sum(a * b for a, b in zip(vec_a, vec_b))
        norm_a = math.sqrt(sum(a * a for a in vec_a))
        norm_b = math.sqrt(sum(b * b for b in vec_b))

        if norm_a == 0 or norm_b == 0:
            return 0.0

        return dot / (norm_a * norm_b)

    @staticmethod
    def _deduplicate(results: List[Dict]) -> List[Dict]:
        """Remove duplicate entries by key, keeping the highest score."""
        seen = {}
        for r in results:
            key = r.get("key", "")
            if key not in seen or r.get("score", 0) > seen[key].get("score", 0):
                seen[key] = r
        return list(seen.values())


# ── Global search engine singleton ──
search_engine = SearchEngine()
