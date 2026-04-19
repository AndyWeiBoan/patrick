"""LanceDB storage layer — session_summaries + turn_chunks tables."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import pyarrow as pa

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
    pa.field("summary_text", pa.string()),
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
    ) -> None:
        """Insert or update a session summary (last-write-wins)."""
        assert self._initialized
        now = _now()
        # Check if exists to preserve created_at
        try:
            existing = (
                self._sessions.search()
                .where(f"session_id = '{session_id}'", prefilter=True)
                .limit(1)
                .to_pandas()
            )
            created_at = existing.iloc[0]["created_at"] if len(existing) > 0 else now
        except Exception:
            created_at = now

        new_data = pa.table({
            "session_id": [session_id],
            "summary_text": [summary_text],
            "vector": pa.array([vector], type=pa.list_(pa.float32(), 384)),
            "created_at": [created_at],
            "updated_at": [now],
        })
        (
            self._sessions.merge_insert("session_id")
            .when_matched_update_all()
            .when_not_matched_insert_all()
            .execute(new_data)
        )

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
