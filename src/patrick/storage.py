"""LanceDB storage layer — session_summaries + turn_chunks tables.

Phase 2 additions:
- BM25Index: in-memory BM25 index over all turn_chunks (cached, invalidated on write)
- search_chunks_bm25(): BM25 keyword search (jieba tokenizer for CJK)
- search_chunks_hybrid(): RRF fusion of vector + BM25 results
- cosine_dedup_session(): remove semantically-duplicate chunks within a session
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa

logger = logging.getLogger(__name__)


# ── BM25 tokenizer ────────────────────────────────────────────────────────────

def _tokenize_for_bm25(text: str) -> list[str]:
    """Tokenize text for BM25 index and query.

    Uses jieba for Chinese word segmentation, which correctly handles:
    - Pure Chinese text (成語、複合詞)
    - Mixed CJK/Latin/code text
    - Error messages and identifiers

    Falls back to char-level split only when jieba is not installed.
    Char-level is suboptimal (idioms get fragmented, IDF diluted) but still
    far better than whitespace-only split that treats whole CJK sentences as
    a single token.

    IMPORTANT: query tokenization in search_chunks_bm25 must use the same
    function so index tokens and query tokens are in the same token space.
    """
    try:
        import jieba  # type: ignore[import]
        # cut() returns a generator; filter empty/whitespace-only tokens
        return [tok for tok in jieba.cut(text.lower()) if tok.strip()]
    except ImportError:
        logger.warning(
            "jieba not installed — falling back to char-level BM25 tokenization. "
            "Chinese keyword search will be degraded. Run: pip install jieba"
        )
        return list(text.lower())  # char-level fallback


from .config import (
    ASSISTANT_TEXT_BOOST,
    BM25_B,
    BM25_K1,
    BM25_WEIGHT,
    COSINE_DEDUP_THRESHOLD,
    DATA_DIR,
    HYBRID_RECALL_N,
    MIN_SESSION_SCORE,
    RECENCY_BLEND,
    RRF_K,
    TIME_DECAY_HALFLIFE_DAYS,
    TOP_K_CHUNKS,
    TOP_K_SESSIONS,
    VECTOR_WEIGHT,
)
from .embedding import provider


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_null(val: Any) -> bool:
    """Null check that works for both None and pandas float NaN."""
    if val is None:
        return True
    try:
        return pd.isna(val)
    except (TypeError, ValueError):
        return False


# ── Schema definitions ────────────────────────────────────────────────────────

_SESSION_SCHEMA = pa.schema([
    pa.field("session_id", pa.string()),
    pa.field("summary_text", pa.string()),   # centroid auto-summary, always present
    pa.field("hint", pa.string()),           # agent LLM synthesis, optional upgrade
    pa.field("vector", pa.list_(pa.float32(), 384)),
    pa.field("created_at", pa.string()),
    pa.field("updated_at", pa.string()),
])

_CHUNK_SCHEMA = pa.schema([
    pa.field("chunk_id", pa.string()),
    pa.field("session_id", pa.string()),
    pa.field("turn_id", pa.string()),
    pa.field("chunk_index", pa.int32()),
    pa.field("total_chunks", pa.int32()),
    pa.field("role", pa.string()),
    pa.field("text", pa.string()),
    pa.field("vector", pa.list_(pa.float32(), 384)),
    pa.field("source", pa.string()),
    pa.field("hook_type", pa.string()),  # e.g. "user_prompt","tool_use","assistant_text","reindex","manual"
    pa.field("text_hash", pa.string()),
    pa.field("source_file", pa.string()),  # nullable
    pa.field("created_at", pa.string()),
])


class Storage:
    """Manages LanceDB connection and all table operations."""

    _instance: "Storage | None" = None
    # BM25 cache: maps frozenset(session_ids) | None → (BM25Okapi, list[dict])
    # Invalidated on any write (add_chunks, cosine_dedup_session delete, reindex --wipe).
    _bm25_cache: dict

    def __new__(cls) -> "Storage":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
            cls._instance._bm25_cache = {}
        return cls._instance

    def _invalidate_bm25_cache(self) -> None:
        """Clear the BM25 index cache. Call after any write that changes turn_chunks."""
        self._bm25_cache.clear()
        logger.debug("BM25 cache invalidated")

    def initialize(self) -> None:
        """Open DB and ensure tables exist. Called once at server start."""
        if self._initialized:
            return
        import lancedb

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._db = lancedb.connect(str(DATA_DIR))

        # Open or create tables
        existing = self._db.table_names()
        if "session_summaries" not in existing:
            self._sessions = self._db.create_table(
                "session_summaries", schema=_SESSION_SCHEMA
            )
        else:
            self._sessions = self._db.open_table("session_summaries")
            # Migrate: add hint column if missing (older DBs won't have it)
            try:
                col_names = self._sessions.schema.names
                if "hint" not in col_names:
                    # Try multiple SQL variants for LanceDB/DuckDB compatibility
                    for expr in ("CAST(NULL AS VARCHAR)", "CAST(NULL AS TEXT)", "NULL"):
                        try:
                            self._sessions.add_columns({"hint": expr})
                            logger.info("Migrated session_summaries: added hint column (expr=%s)", expr)
                            break
                        except Exception as e:
                            logger.warning("add_columns hint with expr=%s failed: %s", expr, e)
            except Exception as e:
                logger.error("session_summaries migration failed: %s", e)

        if "turn_chunks" not in existing:
            self._chunks = self._db.create_table(
                "turn_chunks", schema=_CHUNK_SCHEMA
            )
        else:
            self._chunks = self._db.open_table("turn_chunks")
            # Migration: add hook_type column if missing (older DBs won't have it)
            try:
                col_names = self._chunks.schema.names
                if "hook_type" not in col_names:
                    for expr in ("CAST(NULL AS VARCHAR)", "CAST(NULL AS TEXT)", "NULL"):
                        try:
                            self._chunks.add_columns({"hook_type": expr})
                            logger.info("Migrated turn_chunks: added hook_type column (expr=%s)", expr)
                            break
                        except Exception as e:
                            logger.warning("add_columns hook_type with expr=%s failed: %s", expr, e)
            except Exception as e:
                logger.error("turn_chunks migration failed: %s", e)

        self._initialized = True

    def reset_database(self) -> None:
        """Drop all tables and delete all stored memories (irreversible).

        Uses full directory removal (same as reindex --wipe) to avoid
        LanceDB stale fragment issues after a crash. Recreates empty
        tables so the server can resume without restart.
        """
        import shutil
        import lancedb as _lancedb

        if DATA_DIR.exists():
            shutil.rmtree(DATA_DIR)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        db = _lancedb.connect(str(DATA_DIR))
        db.create_table("session_summaries", schema=_SESSION_SCHEMA)
        db.create_table("turn_chunks", schema=_CHUNK_SCHEMA)
        # Re-open fresh table handles on the singleton
        self._sessions = db.open_table("session_summaries")
        self._chunks = db.open_table("turn_chunks")
        self._db = db
        self._bm25_cache.clear()
        self._initialized = True  # tables exist and are ready
        logger.info("Database reset: all memories deleted")

    # ── Session summaries ─────────────────────────────────────────────────────

    def upsert_session_summary(
        self,
        session_id: str,
        summary_text: str,
        vector: list[float],
        hint: str | None = None,
    ) -> None:
        """Insert or update a session summary.

        summary_text: centroid-derived auto-summary (always provided).
        vector: embedding to use for Layer 1 search — pass embed(hint) when hint
                is provided so hint-equipped sessions rank higher on topic match.
        hint: optional agent LLM synthesis; stored separately, survives centroid
              recalculation because it is only written here when explicitly given.
        """
        assert self._initialized
        now = _now()
        # Check if exists to preserve created_at and existing hint
        existing_hint = hint  # caller-supplied wins
        created_at = now
        try:
            existing = (
                self._sessions.search()
                .where(f"session_id = '{session_id}'", prefilter=True)
                .limit(1)
                .to_pandas()
            )
            if len(existing) > 0:
                created_at = existing.iloc[0]["created_at"]
                if existing_hint is None:
                    raw = existing.iloc[0].get("hint")
                    existing_hint = None if _is_null(raw) else str(raw)
        except Exception:
            pass

        # Build write payload — include 'hint' only if the column exists in table
        # (older DBs may not have it if migration failed; guard prevents schema error).
        table_col_names = self._sessions.schema.names
        row: dict = {
            "session_id": [session_id],
            "summary_text": [summary_text],
            "vector": pa.array([vector], type=pa.list_(pa.float32(), 384)),
            "created_at": [created_at],
            "updated_at": [now],
        }
        if "hint" in table_col_names:
            row["hint"] = [existing_hint]

        new_data = pa.table(row)
        (
            self._sessions.merge_insert("session_id")
            .when_matched_update_all()
            .when_not_matched_insert_all()
            .execute(new_data)
        )

    def get_session(self, session_id: str) -> dict | None:
        """Return a single session row or None."""
        assert self._initialized
        try:
            df = (
                self._sessions.search()
                .where(f"session_id = '{session_id}'", prefilter=True)
                .limit(1)
                .to_pandas()
            )
            if len(df) == 0:
                return None
            row = df.iloc[0]
            return {
                "session_id": row["session_id"],
                "summary_text": None if _is_null(row.get("summary_text")) else row["summary_text"],
                "hint": None if _is_null(row.get("hint")) else row["hint"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        except Exception:
            return None

    def get_session_chunks(self, session_id: str) -> list[dict]:
        """Return all turn chunks for a session (ordered by created_at, chunk_index).

        Uses table scan (not vector search) to avoid LanceDB's default row cap
        on .search() calls — centroid must see every chunk, not just the first ~10.
        """
        assert self._initialized
        try:
            # to_pandas() with a filter string does a full table scan; no row cap.
            df = self._chunks.to_pandas(
                filter=f"session_id = '{session_id}'"
            )
            df = df.sort_values(["created_at", "chunk_index"])
            return df.to_dict(orient="records")
        except Exception:
            # Fallback: if filter API unavailable, use search with a very large limit
            try:
                df = (
                    self._chunks.search()
                    .where(f"session_id = '{session_id}'", prefilter=True)
                    .limit(100_000)
                    .to_pandas()
                )
                df = df.sort_values(["created_at", "chunk_index"])
                return df.to_dict(orient="records")
            except Exception:
                return []

    def update_summary_text_only(self, session_id: str, summary_text: str) -> None:
        """Update only summary_text for an existing session (centroid recalculation).

        Does NOT touch the hint or the search vector — those are owned by the
        agent-hint write path inside upsert_session_summary.
        """
        assert self._initialized
        now = _now()
        try:
            self._sessions.update(
                where=f"session_id = '{session_id}'",
                values={"summary_text": summary_text, "updated_at": now},
            )
        except Exception:
            pass  # Non-fatal; centroid is best-effort

    def search_sessions(
        self, query_vector: list[float], top_k: int = TOP_K_SESSIONS
    ) -> list[dict]:
        """Vector search over session summaries."""
        assert self._initialized
        results = (
            self._sessions.search(query_vector)
            .metric("cosine")
            .limit(top_k)
            .to_pandas()
        )
        return results.to_dict(orient="records")

    def list_sessions(self) -> list[dict]:
        """Return all sessions with metadata."""
        assert self._initialized
        try:
            df = self._sessions.to_pandas()
            out = []
            for _, row in df.iterrows():
                out.append({
                    "session_id": row["session_id"],
                    "created_at": row["created_at"],
                    "summary_text": None if _is_null(row.get("summary_text")) else row["summary_text"],
                    "hint": None if _is_null(row.get("hint")) else row["hint"],
                })
            return out
        except Exception:
            return []

    # ── Turn chunks ───────────────────────────────────────────────────────────

    def hash_exists(self, text_hash: str) -> bool:
        """Check if a text_hash already exists (exact dedup)."""
        assert self._initialized
        try:
            results = (
                self._chunks.search()
                .where(f"text_hash = '{text_hash}'", prefilter=True)
                .limit(1)
                .to_pandas()
            )
            return len(results) > 0
        except Exception:
            return False

    def fragment_count(self) -> dict[str, int]:
        """Count .lance fragment files in each table's data/ directory.

        Returns e.g. {"turn_chunks": 42, "session_summaries": 15}.
        """
        counts: dict[str, int] = {}
        for name in ("turn_chunks", "session_summaries"):
            data_dir = DATA_DIR / f"{name}.lance" / "data"
            if data_dir.is_dir():
                counts[name] = sum(1 for f in data_dir.iterdir() if f.suffix == ".lance")
            else:
                counts[name] = 0
        return counts

    def compact(self) -> None:
        """Compact LanceDB tables to reduce fragment file count."""
        from datetime import timedelta
        assert self._initialized
        for tbl in (self._chunks, self._sessions):
            try:
                tbl.optimize(cleanup_older_than=timedelta(0))
            except Exception as exc:
                logger.warning("LanceDB compaction failed: %s", exc)

    def add_chunks(self, chunks: list[dict]) -> None:
        """Append turn chunks (no upsert — append-only)."""
        assert self._initialized
        if not chunks:
            return
        # C3 fix: invalidate BM25 cache before write so next query rebuilds fresh index.
        self._invalidate_bm25_cache()
        self._chunks.add(pa.table({
            "chunk_id": [c["chunk_id"] for c in chunks],
            "session_id": [c["session_id"] for c in chunks],
            "turn_id": [c["turn_id"] for c in chunks],
            "chunk_index": pa.array([c["chunk_index"] for c in chunks], pa.int32()),
            "total_chunks": pa.array([c["total_chunks"] for c in chunks], pa.int32()),
            "role": [c["role"] for c in chunks],
            "text": [c["text"] for c in chunks],
            "vector": pa.array([c["vector"] for c in chunks], type=pa.list_(pa.float32(), 384)),
            "source": [c["source"] for c in chunks],
            "hook_type": [c.get("hook_type") or "" for c in chunks],
            "text_hash": [c["text_hash"] for c in chunks],
            "source_file": [c.get("source_file") for c in chunks],
            "created_at": [c["created_at"] for c in chunks],
        }))

    def search_chunks(
        self,
        query_vector: list[float],
        top_k: int = TOP_K_CHUNKS,
        session_ids: list[str] | None = None,
        hook_type: str | list[str] | None = None,
    ) -> list[dict]:
        """Vector search over turn_chunks, optionally filtered by session_ids / hook_type."""
        assert self._initialized
        search = self._chunks.search(query_vector).metric("cosine").limit(top_k)
        filters: list[str] = []
        if session_ids:
            id_list = ", ".join(f"'{s}'" for s in session_ids)
            filters.append(f"session_id IN ({id_list})")
        if hook_type:
            if isinstance(hook_type, list):
                type_list = ", ".join(f"'{t}'" for t in hook_type)
                filters.append(f"hook_type IN ({type_list})")
            else:
                filters.append(f"hook_type = '{hook_type}'")
        if filters:
            search = search.where(" AND ".join(filters), prefilter=True)
        df = search.to_pandas()
        return df.to_dict(orient="records")

    # ── Phase 2: BM25 + Hybrid search ────────────────────────────────────────

    def _build_bm25_index(
        self, session_ids: list[str] | None = None
    ) -> tuple["Any", list[dict]]:
        """Return a cached BM25 index over turn_chunks, building it on first call.

        Returns (BM25Okapi, [chunk_dicts_in_index_order]).
        The chunk list preserves the position mapping needed to look up scores.

        Cache key: frozenset(session_ids) for filtered queries, None for global.
        Cache is invalidated by _invalidate_bm25_cache() whenever turn_chunks change
        (add_chunks, cosine_dedup_session delete, reindex --wipe).

        session_ids: if provided, index only those sessions (faster, more focused).
        """
        cache_key = frozenset(session_ids) if session_ids else None
        if cache_key in self._bm25_cache:
            logger.debug("BM25 cache hit (key=%s)", cache_key)
            return self._bm25_cache[cache_key]

        try:
            from rank_bm25 import BM25Okapi  # type: ignore[import]
        except ImportError as e:
            raise RuntimeError(
                "rank-bm25 not installed. Run: pip install rank-bm25"
            ) from e

        assert self._initialized
        try:
            if session_ids:
                id_list = ", ".join(f"'{s}'" for s in session_ids)
                df = self._chunks.to_pandas(
                    filter=f"session_id IN ({id_list})"
                )
            else:
                df = self._chunks.to_pandas()
        except Exception:
            df = pd.DataFrame()

        if df.empty:
            return None, []

        chunks = df.to_dict(orient="records")
        # C4 fix: use jieba tokenizer (word-level for CJK, char-level fallback).
        # Must match the tokenizer used in search_chunks_bm25 for query tokens.
        tokenized = [_tokenize_for_bm25(str(c.get("text", ""))) for c in chunks]
        bm25 = BM25Okapi(tokenized, k1=BM25_K1, b=BM25_B)

        self._bm25_cache[cache_key] = (bm25, chunks)
        logger.debug("BM25 index built and cached (key=%s, chunks=%d)", cache_key, len(chunks))
        return bm25, chunks

    def search_chunks_bm25(
        self,
        query: str,
        top_k: int = TOP_K_CHUNKS,
        session_ids: list[str] | None = None,
        hook_type: str | list[str] | None = None,
    ) -> list[dict]:
        """BM25 keyword search over turn_chunks.

        Returns up to top_k chunks sorted by BM25 score descending.
        Each result has a '_bm25_score' field added.
        """
        bm25, chunks = self._build_bm25_index(session_ids=session_ids)
        if bm25 is None:
            return []

        # C4 fix: use same tokenizer as index build — jieba for CJK, char fallback.
        query_tokens = _tokenize_for_bm25(query)
        scores = bm25.get_scores(query_tokens)

        # Pair (score, chunk) and sort descending; apply hook_type filter post-index
        scored = sorted(
            zip(scores, chunks), key=lambda x: x[0], reverse=True
        )
        results = []
        for score, chunk in scored:
            if hook_type:
                chunk_ht = chunk.get("hook_type")
                if isinstance(hook_type, list):
                    if chunk_ht not in hook_type:
                        continue
                elif chunk_ht != hook_type:
                    continue
            results.append({**chunk, "_bm25_score": float(score)})
            if len(results) >= top_k:
                break
        return results

    def search_chunks_hybrid(
        self,
        query_vector: list[float],
        query_text: str,
        top_k: int = TOP_K_CHUNKS,
        recall_n: int = 50,
        session_ids: list[str] | None = None,
        rrf_k: int = RRF_K,
        hook_type: str | list[str] | None = None,
    ) -> list[dict]:
        """Hybrid search: RRF fusion of vector + BM25 results.

        1. Vector search top-recall_n candidates
        2. BM25 search top-recall_n candidates
        3. RRF fusion → top-recall_n unique candidates ranked by fused score
        4. Returns top_k (before cross-encoder rerank, which happens in tools.py)

        Each returned chunk has '_hybrid_score' (RRF fused), '_vector_rank',
        '_bm25_rank' fields for diagnostics.
        """
        # Step 1: vector recall
        vector_results = self.search_chunks(
            query_vector=query_vector,
            top_k=recall_n,
            session_ids=session_ids,
            hook_type=hook_type,
        )
        # Step 2: BM25 recall
        bm25_results = self.search_chunks_bm25(
            query=query_text,
            top_k=recall_n,
            session_ids=session_ids,
            hook_type=hook_type,
        )

        # Step 3: RRF fusion
        # Build rank maps: chunk_id → rank (0-based)
        vec_rank: dict[str, int] = {
            c["chunk_id"]: rank
            for rank, c in enumerate(vector_results)
            if c.get("chunk_id")
        }
        bm25_rank: dict[str, int] = {
            c["chunk_id"]: rank
            for rank, c in enumerate(bm25_results)
            if c.get("chunk_id")
        }

        # Collect unique chunk_ids across both result sets
        all_ids: dict[str, dict] = {}
        for c in vector_results + bm25_results:
            cid = c.get("chunk_id")
            if cid and cid not in all_ids:
                all_ids[cid] = c

        # Compute weighted RRF score:
        #   VECTOR_WEIGHT / (k + vec_rank + 1) + BM25_WEIGHT / (k + bm25_rank + 1)
        fused: list[dict] = []
        for cid, chunk in all_ids.items():
            vr = vec_rank.get(cid)
            br = bm25_rank.get(cid)
            rrf_score = 0.0
            if vr is not None:
                rrf_score += VECTOR_WEIGHT / (rrf_k + vr + 1)
            if br is not None:
                rrf_score += BM25_WEIGHT / (rrf_k + br + 1)
            # Boost assistant responses: higher information density than user prompts
            if chunk.get("hook_type") == "assistant_text":
                rrf_score *= ASSISTANT_TEXT_BOOST
            fused.append({
                **chunk,
                "_hybrid_score": rrf_score,
                "_vector_rank": vr,
                "_bm25_rank": br,
            })

        fused.sort(key=lambda x: x["_hybrid_score"], reverse=True)
        return fused[:top_k]

    # ── Phase 3: Time-decay search ────────────────────────────────────────────

    def search_chunks_with_recency(
        self,
        query_vector: list[float],
        query_text: str,
        top_k: int = TOP_K_CHUNKS,
        session_ids: list[str] | None = None,
        halflife_days: int = TIME_DECAY_HALFLIFE_DAYS,
        hook_type: str | list[str] | None = None,
    ) -> list[dict]:
        """Hybrid search with exponential time-decay weighting.

        Retrieves more candidates than needed, then re-ranks by:
            final_score = hybrid_score * exp(-age_days / halflife_days)

        This makes newer memories naturally rank higher while still
        respecting semantic relevance. halflife_days controls the trade-off:
        - smaller (e.g. 7)  → strongly prefer recent memories
        - larger  (e.g. 90) → mild recency boost, mostly semantic
        """
        import math
        from datetime import datetime, timezone

        # Retrieve a larger candidate pool so recency re-ranking has room to work
        recall_n = max(top_k * 3, HYBRID_RECALL_N)
        results = self.search_chunks_hybrid(
            query_vector=query_vector,
            query_text=query_text,
            top_k=recall_n,
            recall_n=recall_n,
            session_ids=session_ids,
            hook_type=hook_type,
        )

        if not results:
            return []

        now = datetime.now(timezone.utc)
        for chunk in results:
            created_at = chunk.get("created_at")
            if created_at:
                try:
                    dt = datetime.fromisoformat(created_at)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    age_days = max(0, (now - dt).days)
                except (ValueError, TypeError):
                    age_days = 9999
            else:
                age_days = 9999  # no timestamp → treat as very old

            decay = math.exp(-age_days / halflife_days)
            base_score = chunk.get("_hybrid_score", 0.0)
            effective_decay = RECENCY_BLEND * decay + (1.0 - RECENCY_BLEND)
            chunk["_recency_score"] = base_score * effective_decay

        results.sort(key=lambda c: c.get("_recency_score", 0.0), reverse=True)
        return results[:top_k]

    # ── Phase 2: Session-level cosine dedup ───────────────────────────────────

    def cosine_dedup_session(self, session_id: str) -> int:
        """Remove semantically-duplicate chunks within a session.

        Algorithm:
        1. Load all chunks for the session ordered by created_at DESC (newest first)
        2. Greedily build a "keep" set: add each chunk if its cosine similarity to
           ALL already-kept chunks is < COSINE_DEDUP_THRESHOLD
        3. Delete chunks NOT in the keep set from turn_chunks table

        Returns count of chunks deleted.

        Note: deletes from LanceDB are performed via .delete(where=...).
        The operation is session-scoped and safe to run after the stop hook.
        """
        assert self._initialized
        chunks = self.get_session_chunks(session_id)
        if len(chunks) < 2:
            return 0

        valid = [c for c in chunks if c.get("vector") is not None]
        if len(valid) < 2:
            return 0

        # Sort newest-first so we keep the most recent version of any near-duplicate
        valid_sorted = sorted(
            valid,
            key=lambda c: c.get("created_at") or "",
            reverse=True,
        )

        mat = np.array([c["vector"] for c in valid_sorted], dtype=np.float32)
        norms = np.linalg.norm(mat, axis=1, keepdims=True) + 1e-10
        mat_norm = mat / norms  # L2-normalised rows

        keep_indices: list[int] = []
        for i in range(len(valid_sorted)):
            if not keep_indices:
                keep_indices.append(i)
                continue
            kept_mat = mat_norm[keep_indices]  # (K, D)
            sims = kept_mat @ mat_norm[i]  # (K,)
            if float(sims.max()) < COSINE_DEDUP_THRESHOLD:
                keep_indices.append(i)
            # else: this chunk is semantically duplicated → drop

        keep_ids = {valid_sorted[i]["chunk_id"] for i in keep_indices}
        drop_ids = [
            c["chunk_id"]
            for c in valid_sorted
            if c["chunk_id"] not in keep_ids
        ]

        if not drop_ids:
            return 0

        try:
            id_list = ", ".join(f"'{cid}'" for cid in drop_ids)
            self._chunks.delete(f"chunk_id IN ({id_list})")
            # C3 fix: corpus changed — invalidate BM25 cache.
            self._invalidate_bm25_cache()
            logger.info(
                "cosine_dedup: session=%s kept=%d dropped=%d",
                session_id, len(keep_ids), len(drop_ids),
            )
        except Exception as e:
            logger.error("cosine_dedup delete failed: %s", e)
            return 0

        return len(drop_ids)

    def get_turn_chunks(self, turn_id: str) -> list[dict]:
        """Fetch all chunks for a turn_id ordered by chunk_index (context expansion)."""
        assert self._initialized
        try:
            df = (
                self._chunks.search()
                .where(f"turn_id = '{turn_id}'", prefilter=True)
                .to_pandas()
            )
            df = df.sort_values("chunk_index")
            return df.to_dict(orient="records")
        except Exception:
            return []

    # ── Centroid ──────────────────────────────────────────────────────────────

    def compute_and_upsert_centroid(
        self,
        session_id: str,
        hint: str | None = None,
        hint_vector: list[float] | None = None,
    ) -> bool:
        """Compute centroid from stored chunk vectors and upsert session_summaries.

        Pure numpy — no embedding provider needed. Safe to call from thread pool.

        If hint + hint_vector provided (memory_save with summary param):
          → uses centroid text as summary_text, hint_vector as Layer 1 search anchor.

        If no hint provided:
          → checks for existing agent hint on this session:
            - has hint  → only refreshes summary_text, preserves hint + its vector.
            - no hint   → uses centroid vector as search anchor (暫代).

        Returns True if centroid was written, False if session has no chunks.
        """
        chunks = self.get_session_chunks(session_id)
        if not chunks:
            return False

        valid = [(c["text"], c["vector"]) for c in chunks if c.get("vector") is not None]
        if not valid:
            return False

        texts = [v[0] for v in valid]
        mat = np.array([v[1] for v in valid], dtype=np.float32)  # (N, 384)

        centroid = mat.mean(axis=0)  # (384,)
        centroid_norm = centroid / (np.linalg.norm(centroid) + 1e-10)
        row_norms = np.linalg.norm(mat, axis=1, keepdims=True) + 1e-10
        sims = (mat / row_norms) @ centroid_norm  # (N,)

        top_k = min(3, len(sims))
        top_indices = np.argsort(sims)[::-1][:top_k]
        summary_text = " | ".join(texts[i] for i in top_indices)

        if hint is not None and hint_vector is not None:
            # Agent-supplied hint: centroid text + hint vector for search
            self.upsert_session_summary(
                session_id=session_id,
                summary_text=summary_text,
                vector=hint_vector,
                hint=hint,
            )
        else:
            # No hint from caller — check for existing agent hint
            existing = self.get_session(session_id)
            if existing and existing.get("hint"):
                # Preserve existing hint + its vector, only refresh summary text
                self.update_summary_text_only(session_id, summary_text)
            else:
                # No hint anywhere — use centroid vector as search anchor
                self.upsert_session_summary(
                    session_id=session_id,
                    summary_text=summary_text,
                    vector=centroid.tolist(),
                )

        logger.debug("Centroid updated for session %s (%d chunks)", session_id, len(valid))
        return True

    # ── Helpers ───────────────────────────────────────────────────────────────

    def make_chunk_records(
        self,
        texts: list[str],
        vectors: list[list[float]],
        session_id: str,
        role: str,
        source: str,
        source_file: str | None = None,
        hook_type: str | None = None,
    ) -> list[dict]:
        """Build chunk dicts from texts + vectors, assigning turn_id + dedup.

        hook_type: classifies the origin of this chunk, e.g.:
            "user_prompt"    — UserPromptSubmit hook
            "tool_use"       — PostToolUse hook
            "assistant_text" — Stop hook (transcript assistant text)
            "reindex"        — patrick reindex CLI command
            "manual"         — direct memory_save call
        """
        turn_id = str(uuid.uuid4())
        total = len(texts)
        now = _now()
        records = []
        for i, (text, vector) in enumerate(zip(texts, vectors)):
            text_hash = provider.text_hash(text)
            if self.hash_exists(text_hash):
                continue  # exact dedup
            records.append({
                "chunk_id": str(uuid.uuid4()),
                "session_id": session_id,
                "turn_id": turn_id,
                "chunk_index": i,
                "total_chunks": total,
                "role": role,
                "text": text,
                "vector": vector,
                "source": source,
                "hook_type": hook_type or "",
                "text_hash": text_hash,
                "source_file": source_file,
                "created_at": now,
            })
        return records


# Module-level singleton
storage = Storage()
