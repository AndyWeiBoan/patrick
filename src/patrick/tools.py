"""MCP tool implementations — memory_save, memory_search, memory_deep_search, memory_sessions.

Phase 2 additions:
- memory_search / memory_deep_search accept mode="hybrid" to use BM25+vector fusion
- Cross-encoder rerank is applied when RERANK_ENABLED=True and mode="hybrid"
- Latency tracked in response metadata when mode="hybrid"
"""

from __future__ import annotations

import logging
import time
import uuid

import pandas as pd

from .config import (
    HYBRID_RECALL_N,
    MIN_SESSION_SCORE,
    RERANK_ENABLED,
    RERANK_TOP_N,
    TOP_K_CHUNKS,
    TOP_K_SESSIONS,
)
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


async def memory_search(
    query: str,
    top_k: int = TOP_K_CHUNKS,
    mode: str = "vector",
    use_recency: bool = False,
    hook_type: str | list[str] | None = None,
) -> list[dict] | dict:
    """Fast semantic search — directly searches all turn chunks.

    Use for: quick lookup of a specific fact or phrase.
    No session context aggregation.

    mode="vector" (default): pure embedding cosine search (fast, Phase 1 behaviour)
    mode="hybrid": BM25 + vector RRF fusion with optional cross-encoder rerank
                   Returns {results, latency_ms, mode} instead of plain list.
    use_recency=True: apply time-decay weighting (newer memories rank higher).
                      Uses hybrid search internally; halflife controlled by
                      TIME_DECAY_HALFLIFE_DAYS in config.py (default: 30 days).
    hook_type: filter results to a specific source type. Common values:
        "assistant_text" — only assistant responses (highest semantic quality)
        "user_prompt"    — only user inputs
        "tool_use"       — only tool call records
        ["assistant_text", "user_prompt"] — pass a list to match multiple types
        None (default)   — no filter, search all chunks
    """
    t0 = time.perf_counter()
    vectors = await provider.embed_async([query])

    if use_recency:
        # Time-decay path: hybrid search re-ranked by recency
        candidates = storage.search_chunks_with_recency(
            query_vector=vectors[0],
            query_text=query,
            top_k=top_k,
            hook_type=hook_type,
        )
        latency_ms = round((time.perf_counter() - t0) * 1000, 1)
        return {
            "results": _format_chunks(candidates),
            "mode": "recency",
            "rerank_applied": False,
            "latency_ms": latency_ms,
        }
    elif mode == "hybrid":
        # Hybrid: vector + BM25 fusion, then optional cross-encoder rerank
        recall_n = max(top_k, HYBRID_RECALL_N)
        candidates = storage.search_chunks_hybrid(
            query_vector=vectors[0],
            query_text=query,
            top_k=recall_n,  # retrieve more for reranker
            recall_n=recall_n,
            hook_type=hook_type,
        )
        if RERANK_ENABLED and candidates:
            reranked = await provider.rerank_async(
                query=query,
                candidates=_format_chunks(candidates),
                top_k=top_k,
            )
            results = reranked
        else:
            results = _format_chunks(candidates[:top_k])

        latency_ms = round((time.perf_counter() - t0) * 1000, 1)
        return {
            "results": results,
            "mode": "hybrid",
            "rerank_applied": RERANK_ENABLED and bool(candidates),
            "latency_ms": latency_ms,
        }
    else:
        # Vector-only (original behaviour)
        results = storage.search_chunks(query_vector=vectors[0], top_k=top_k, hook_type=hook_type)
        return _format_chunks(results)


async def memory_deep_search(
    query: str,
    top_k: int = TOP_K_CHUNKS,
    mode: str = "vector",
    use_recency: bool = False,
    hook_type: str | list[str] | None = None,
) -> dict:
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
    t0 = time.perf_counter()
    vectors = await provider.embed_async([query])
    q_vec = vectors[0]

    # Layer 1: session summary coarse filter
    session_results = storage.search_sessions(q_vec, top_k=TOP_K_SESSIONS)
    good_sessions = [
        r["session_id"]
        for r in session_results
        if r.get("_distance", 1.0) < (1 - MIN_SESSION_SCORE)
    ]

    use_scoped = len(good_sessions) >= 3
    if not use_scoped:
        logger.debug("Layer 1 insufficient (%d sessions), falling back to global", len(good_sessions))

    if use_recency:
        # Phase 3: time-decay path — hybrid re-ranked by recency
        recall_n = max(top_k, HYBRID_RECALL_N)
        chunks = storage.search_chunks_with_recency(
            query_vector=q_vec,
            query_text=query,
            top_k=recall_n,
            session_ids=good_sessions if use_scoped else None,
            hook_type=hook_type,
        )
    elif mode == "hybrid":
        # Hybrid path: BM25 + vector fusion within the scoped session set
        recall_n = max(top_k, HYBRID_RECALL_N)
        chunks = storage.search_chunks_hybrid(
            query_vector=q_vec,
            query_text=query,
            top_k=recall_n,
            recall_n=recall_n,
            session_ids=good_sessions if use_scoped else None,
            hook_type=hook_type,
        )
    else:
        # Vector-only path (original Phase 1 behaviour)
        chunks = (
            storage.search_chunks(q_vec, top_k=top_k, session_ids=good_sessions, hook_type=hook_type)
            if use_scoped
            else storage.search_chunks(q_vec, top_k=top_k, hook_type=hook_type)
        )

    # Optional cross-encoder rerank (hybrid mode only, skipped when use_recency=True
    # because recency scoring already produced the final ranking).
    # C5: rerank on the compact recall set (top-RECALL_N) BEFORE context expansion,
    # so the cross-encoder sees 50 candidates instead of 50×5=250.
    rerank_applied = False
    if not use_recency and mode == "hybrid" and RERANK_ENABLED and chunks:
        pre_rerank = _format_chunks(chunks)
        reranked = await provider.rerank_async(
            query=query,
            candidates=pre_rerank,
            top_k=top_k,
        )
        rerank_applied = True
        # Context expansion: expand only the reranked top-K, not all recall candidates
        seen_turn_ids: set[str] = set()
        expanded_formatted: list[dict] = []
        for fmt_chunk in reranked:
            turn_id = fmt_chunk.get("turn_id")
            if not turn_id:
                expanded_formatted.append(fmt_chunk)
                continue
            if turn_id in seen_turn_ids:
                continue
            seen_turn_ids.add(turn_id)
            siblings = storage.get_turn_chunks(turn_id)
            if siblings:
                expanded_formatted.extend(_format_chunks(siblings))
            else:
                expanded_formatted.append(fmt_chunk)
        formatted = expanded_formatted
    else:
        # Vector-only: expand context on all recall candidates, then truncate
        expanded = _expand_context(chunks)
        formatted = _format_chunks(expanded)[:top_k]

    # Sort by recency (newest first) so Claude naturally sees the most recent info first.
    # NOTE: intentionally placed AFTER top_k truncation / rerank so that eval can measure
    # clean semantic-ranking scores (nDCG@10) without recency order polluting the baseline.
    formatted.sort(key=lambda c: c.get("created_at") or "", reverse=True)

    latency_ms = round((time.perf_counter() - t0) * 1000, 1)

    # Collect unique session_ids from results
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
        "mode": mode,
        "rerank_applied": rerank_applied,
        "latency_ms": latency_ms,
        "note": (
            "Check session_metadata.updated_at to judge recency — older sessions may contain "
            "outdated information. role field shows 'user' vs 'assistant' to help distinguish "
            "questions from answers. Do NOT call memory_save to re-store this retrieved content."
        ),
    }


async def memory_sessions(
    limit: int = 50,
    offset: int = 0,
    include_body: bool = False,
    session_type: str | None = None,
    after: str | None = None,
) -> dict:
    """Browse conversation history. Start here for 'what did we work on?' or 'which session covered X?'.

    Default call returns the 50 most recent sessions with opening only (compact).
    Use filters to narrow down, include_body=True for full detail.

    Args:
        limit: max sessions to return (default 50, 0 = all). Use 10-20 for quick scan.
        offset: skip first N sessions (for pagination). E.g. offset=50 for page 2.
        include_body: False (default) = only opening (≤200 chars per session, compact list).
                      True = include full body, summary_text, hint (for drill-down).
        session_type: "regular" (1-on-1 Claude session) or "multi_agent" (discussion room).
                      None (default) = all types.
        after: ISO date string, e.g. "2026-04-20". Only sessions created on/after this date.

    Returns: {sessions: [{session_id, created_at, opening, session_type, summary_status, ...}],
              total: int, limit: int, offset: int}

    Typical usage:
        memory_sessions()                                        → latest 50, opening only
        memory_sessions(limit=10, session_type="regular")        → recent regular sessions
        memory_sessions(after="2026-04-24", include_body=True)   → recent sessions with full detail
        memory_sessions(limit=0)                                 → all sessions (opening only)
    """
    return storage.list_sessions(
        limit=limit,
        offset=offset,
        include_body=include_body,
        session_type=session_type,
        after=after,
    )


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
