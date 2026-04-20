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
    (4) ALWAYS required after memory_deep_search — pass your synthesis as both text and summary.

    Do NOT call for every turn — that wastes tokens.
    Expected frequency: 0–2 times per session (plus mandatory post-deep-search call).

    summary: 50–1000 character LLM synthesis stored as a searchable hint. It is embedded
             and used as the Layer 1 search vector, improving future recall accuracy.

    session_id: pass the current session's UUID if known (from session-start hook context).
    If not available, omit — a new UUID will be generated for this standalone memory.
    """
    # memory_save is temporarily disabled to prevent stale hint accumulation.
    # All conversation data is captured automatically via hooks.
    return {"status": "disabled", "reason": "manual saves temporarily disabled; hooks handle all storage"}

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

    # Recompute centroid and upsert session_summaries.
    # Delegate all centroid math to storage (pure numpy, no embedding provider needed).
    hint_provided = bool(summary and summary.strip())

    if hint_provided:
        # Agent-supplied LLM synthesis: embed hint text, pass vector to storage so
        # it can use it as the Layer 1 search anchor (better than centroid alone).
        assert summary is not None
        hint_vector = (await provider.embed_async([summary]))[0]
        centroid_updated = storage.compute_and_upsert_centroid(
            session_id, hint=summary, hint_vector=hint_vector
        )
    else:
        centroid_updated = storage.compute_and_upsert_centroid(session_id)

    return {
        "status": "saved",
        "chunks_written": len(records),
        "hint_saved": hint_provided,
        "centroid_updated": centroid_updated,
    }


async def memory_search(query: str, top_k: int = TOP_K_CHUNKS) -> list[dict]:
    """Fast semantic search — directly searches all turn chunks.

    Use for: quick lookup of a specific fact or phrase.
    No session context aggregation.
    """
    vectors = await provider.embed_async([query])
    results = storage.search_chunks(query_vector=vectors[0], top_k=top_k)
    return _format_chunks(results)


async def memory_deep_search(query: str, top_k: int = TOP_K_CHUNKS) -> dict:
    """Deep search with session context — two-layer retrieval.

    WHEN TO CALL PROACTIVELY: Call this at the start of a session when the user references
    an ongoing project, past decisions, previous debugging, or any prior work — even implicitly.
    Trigger phrases: "last time", "we decided", "remember", "continue", "what was", "as before",
    any mention of a named project or feature you may have worked on previously.
    Do not wait for an explicit "search memory" request — use judgment.

    REPLACES FILE READING: If this tool returns relevant results, you do NOT need to separately
    read MEMORY.md, ask the user to paste previous context, or read project files to reconstruct
    history. Patrick's verbatim turn-by-turn record is more complete than any summary file.
    Call this first — skip the file reads.

    Layer 1: Find relevant sessions by summary similarity.
    Layer 2: Search turn chunks within those sessions.
    Graceful degradation: if <3 sessions found or scores too low, falls back to global search.

    DO NOT save back what you retrieved: After calling this tool, do NOT call memory_save
    to re-store the retrieved content. The hooks already captured everything verbatim.
    Re-saving retrieved content creates stale data amplification loops — old incorrect
    conclusions get reinforced with each search cycle.

    ONLY call memory_save if you reach a genuinely NEW conclusion or decision during THIS
    session that was not present in the retrieved results (e.g. you fixed a bug, made a
    new architectural decision, or explicitly want to record a current-session insight).
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
    formatted = _format_chunks(expanded)

    # Sort by recency (newest first) so Claude naturally sees the most recent info first.
    # Chunks without created_at are pushed to the end.
    formatted.sort(key=lambda c: c.get("created_at") or "", reverse=True)

    # Collect unique session_ids from results (for reference only — do NOT reuse in memory_save)
    retrieved_from_sessions = list(dict.fromkeys(c["session_id"] for c in formatted if c.get("session_id")))

    # Build session metadata map so Claude can judge recency of retrieved summaries
    session_meta = {}
    for r in session_results:
        sid = r.get("session_id")
        if sid and sid in retrieved_from_sessions:
            session_meta[sid] = {
                "updated_at": r.get("updated_at", "unknown"),
                "hint": r.get("hint") if not pd.isna(r.get("hint", float("nan"))) else None,
                "similarity_score": round(1 - float(r.get("_distance", 1.0)) / 2, 4),
            }

    return {
        "results": formatted,
        "retrieved_from_sessions": retrieved_from_sessions,
        "session_metadata": session_meta,
        "note": (
            "Check session_metadata.updated_at to judge recency — older sessions may contain "
            "outdated information. role field shows 'user' vs 'assistant' to help distinguish "
            "questions from answers. Do NOT call memory_save to re-store this retrieved content."
        ),
    }


async def memory_sessions() -> list[dict]:
    """List all sessions with metadata.

    Returns: [{session_id, created_at, summary_text, hint}]
    summary_text: centroid auto-summary, always present once any chunk exists.
    hint: agent LLM synthesis, null if not yet provided for that session.
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
