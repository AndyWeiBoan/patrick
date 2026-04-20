"""LanceDB storage layer — session_summaries + turn_chunks tables."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa

logger = logging.getLogger(__name__)

from .config import DATA_DIR, TOP_K_CHUNKS, TOP_K_SESSIONS, MIN_SESSION_SCORE
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
    pa.field("text_hash", pa.string()),
    pa.field("source_file", pa.string()),  # nullable
    pa.field("created_at", pa.string()),
])


class Storage:
    """Manages LanceDB connection and all table operations."""

    _instance: "Storage | None" = None

    def __new__(cls) -> "Storage":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

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

        self._initialized = True

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

    def add_chunks(self, chunks: list[dict]) -> None:
        """Append turn chunks (no upsert — append-only)."""
        assert self._initialized
        if not chunks:
            return
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
            "text_hash": [c["text_hash"] for c in chunks],
            "source_file": [c.get("source_file") for c in chunks],
            "created_at": [c["created_at"] for c in chunks],
        }))

    def search_chunks(
        self,
        query_vector: list[float],
        top_k: int = TOP_K_CHUNKS,
        session_ids: list[str] | None = None,
    ) -> list[dict]:
        """Vector search over turn_chunks, optionally filtered by session_ids."""
        assert self._initialized
        search = self._chunks.search(query_vector).metric("cosine").limit(top_k)
        if session_ids:
            id_list = ", ".join(f"'{s}'" for s in session_ids)
            search = search.where(f"session_id IN ({id_list})", prefilter=True)
        df = search.to_pandas()
        return df.to_dict(orient="records")

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
    ) -> list[dict]:
        """Build chunk dicts from texts + vectors, assigning turn_id + dedup."""
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
                "text_hash": text_hash,
                "source_file": source_file,
                "created_at": now,
            })
        return records


# Module-level singleton
storage = Storage()
