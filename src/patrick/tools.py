"""MCP tool implementations — memory_save, memory_search, memory_deep_search, memory_sessions."""

from __future__ import annotations

import logging
import uuid

import pandas as pd

from .config import MIN_SESSION_SCORE, TOP_K_CHUNKS, TOP_K_SESSIONS
from .embedding import provider
from .storage import storage

logger = logging.getLogger(__name__)


async def memory_save(
    text: str,
    session_id: str | None = None,
    role: str = "user",
    summary: str | None = None,
    source_file: str | None = None,
) -> dict:
    """Save a memory. Provide summary only for important moments (decisions, conclusions).

    Only call when:
    (1) User explicitly asks to remember something
    (2) A major decision or conclusion was reached in this conversation
    (3) Session is ending and you want to summarize what happened

    Do NOT call for every turn — that wastes tokens.
    Expected frequency: 0–2 times per session.

    session_id: pass the current session's UUID if known (from session-start hook context).
    If not available, omit — a new UUID will be generated for this standalone memory.
    """
    session_id = session_id or str(uuid.uuid4())
    if not text.strip():
        return {"status": "skipped", "reason": "empty text"}

    chunks = provider.chunk_text(text)
    vectors = await provider.embed_async(chunks)

    records = storage.make_chunk_records(
        texts=chunks,
        vectors=vectors,
        session_id=session_id,
        role=role,
        source="manual",
        source_file=source_file,
    )
    if records:
        storage.add_chunks(records)

    # Upsert session summary if provided
    if summary and summary.strip():
        summary_vectors = await provider.embed_async([summary])
        storage.upsert_session_summary(
            session_id=session_id,
            summary_text=summary,
            vector=summary_vectors[0],
        )

    return {
        "status": "saved",
        "chunks_written": len(records),
        "summary_saved": bool(summary and summary.strip()),
    }


async def memory_search(query: str, top_k: int = TOP_K_CHUNKS) -> list[dict]:
    """Fast semantic search — directly searches all turn chunks.

    Use for: quick lookup of a specific fact or phrase.
    No session context aggregation.
    """
    vectors = await provider.embed_async([query])
    results = storage.search_chunks(query_vector=vectors[0], top_k=top_k)
    return _format_chunks(results)


async def memory_deep_search(query: str, top_k: int = TOP_K_CHUNKS) -> list[dict]:
    """Deep search with session context — two-layer retrieval.

    Layer 1: Find relevant sessions by summary similarity.
    Layer 2: Search turn chunks within those sessions.
    Graceful degradation: if <3 sessions found or scores too low, falls back to global search.

    Use for: questions needing full context or cross-session synthesis.
    """
    vectors = await provider.embed_async([query])
    q_vec = vectors[0]

    # Layer 1: session summary coarse filter
    session_results = storage.search_sessions(q_vec, top_k=TOP_K_SESSIONS)
    good_sessions = [
        r["session_id"]
        for r in session_results
        if r.get("_distance", 1.0) < (1 - MIN_SESSION_SCORE)
    ]

    if len(good_sessions) >= 3:
        chunks = storage.search_chunks(q_vec, top_k=top_k, session_ids=good_sessions)
    else:
        # Graceful degradation: not enough session signal, fall back to global
        logger.debug("Layer 1 insufficient (%d sessions), falling back to global", len(good_sessions))
        chunks = storage.search_chunks(q_vec, top_k=top_k)

    # Context expansion: for each hit, pull all sibling chunks from same turn
    expanded = _expand_context(chunks)
    return _format_chunks(expanded)


async def memory_sessions() -> list[dict]:
    """List all sessions with metadata.

    Returns: [{session_id, created_at, summary_text}]
    summary_text is null if no summary was saved for that session.
    """
    return storage.list_sessions()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _expand_context(chunks: list[dict]) -> list[dict]:
    """For each hit, fetch all sibling chunks from same turn_id.

    Once a turn_id is seen, all siblings are added; subsequent hits from the
    same turn are skipped entirely to avoid duplicates.
    """
    seen_turn_ids: set[str] = set()
    expanded: list[dict] = []
    for chunk in chunks:
        turn_id = chunk.get("turn_id")
        if not turn_id:
            expanded.append(chunk)
            continue
        if turn_id in seen_turn_ids:
            continue  # siblings already added; don't duplicate
        seen_turn_ids.add(turn_id)
        siblings = storage.get_turn_chunks(turn_id)
        if siblings:
            expanded.extend(siblings)
        else:
            expanded.append(chunk)
    return expanded


def _format_chunks(chunks: list[dict]) -> list[dict]:
    """Format chunk records for MCP response."""
    out = []
    for c in chunks:
        out.append({
            "chunk_id": c.get("chunk_id", ""),
            "session_id": c.get("session_id", ""),
            "turn_id": c.get("turn_id", ""),
            "chunk_index": c.get("chunk_index", 0),
            "total_chunks": c.get("total_chunks", 1),
            "role": c.get("role", ""),
            "text": c.get("text", ""),
            "source": c.get("source", ""),
            "created_at": c.get("created_at", ""),
            # cosine distance in [0,2]; score = 1 - dist/2 → [0,1]
            # context-expanded sibling chunks have no _distance → score=None
            # LanceDB returns float NaN for missing values, so use pd.isna
            "score": (
                None if pd.isna(c["_distance"])
                else round(1 - float(c["_distance"]) / 2, 4)
            ) if "_distance" in c else None,
        })
    return out
