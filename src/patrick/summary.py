"""Phase 4 — Session summary generation.

Generates searchable summaries for completed sessions:
- Regular session: opening = first user_prompt (≤200 chars),
                   body = first 5 assistant_text chunks (cosine ≥ 0.8 deduped)
- Multi-agent session: opening = Discussion topic,
                       body = Andy's [broadcast] messages (deduped)

Called by the background backfill task in server.py.
"""

from __future__ import annotations

import logging
import re

import numpy as np

from .embedding import provider
from .storage import storage

logger = logging.getLogger(__name__)

OPENING_MAX_CHARS = 200
BODY_DEDUP_COSINE_THRESHOLD = 0.8
MAX_BODY_ITEMS = 5

_MULTI_AGENT_MARKERS = (
    "You are claude-",
    "participating in a multi-agent discussion room",
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _is_multi_agent(text: str) -> bool:
    """Check if first user_prompt indicates a multi-agent session."""
    for marker in _MULTI_AGENT_MARKERS:
        if marker in text:
            return True
    return False


def _truncate(text: str, max_chars: int = OPENING_MAX_CHARS) -> str:
    """Truncate text to max_chars, adding … if exceeded."""
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "…"


def _extract_discussion_topic(text: str) -> str:
    """Extract 'Discussion topic:' from multi-agent system prompt."""
    match = re.search(r"Discussion topic:\s*(.+?)(?:\n|$)", text)
    if match:
        return _truncate(match.group(1).strip())
    return ""


def _extract_broadcasts(chunks: list[dict]) -> list[str]:
    """Extract Andy's [broadcast] messages from user_prompt chunks.

    Scans all user_prompt chunks for lines like:
        [owner] Andy: [broadcast] some message
    Returns deduplicated list preserving first-seen order.
    """
    broadcasts: list[str] = []
    seen: set[str] = set()

    for chunk in chunks:
        if chunk.get("hook_type") != "user_prompt":
            continue
        text = chunk.get("text", "")
        for match in re.finditer(
            r"\[owner\]\s*Andy:\s*\[broadcast\]\s*(.+?)(?:\n|$)", text
        ):
            msg = match.group(1).strip()
            if msg and msg not in seen:
                seen.add(msg)
                broadcasts.append(msg)

    return broadcasts


def _get_first_user_prompt(chunks: list[dict]) -> str:
    """Get the full text of the first user_prompt chunk (for pattern matching)."""
    for chunk in sorted(
        chunks, key=lambda c: (c.get("created_at", ""), c.get("chunk_index", 0))
    ):
        if chunk.get("hook_type") == "user_prompt":
            return chunk.get("text", "")
    return ""


def _get_assistant_chunks(chunks: list[dict]) -> list[tuple[str, list[float]]]:
    """Get assistant_text chunks ordered by time.

    Returns [(text, vector)] for cosine dedup.
    """
    assistant = [
        c
        for c in chunks
        if c.get("hook_type") == "assistant_text" and c.get("text")
    ]
    assistant.sort(
        key=lambda c: (c.get("created_at", ""), c.get("chunk_index", 0))
    )
    return [(c["text"], c.get("vector", [])) for c in assistant]


def _cosine_dedup(
    items: list[tuple[str, list[float]]],
    threshold: float,
    max_keep: int,
) -> list[str]:
    """Greedy cosine dedup: keep items whose cosine sim to all kept < threshold.

    Returns up to max_keep texts. Falls back to first item if all are filtered.
    """
    if not items:
        return []

    valid = [(text, vec) for text, vec in items if vec is not None and len(vec) > 0]
    if not valid:
        return [items[0][0]]

    mat = np.array([v[1] for v in valid], dtype=np.float32)
    norms = np.linalg.norm(mat, axis=1, keepdims=True) + 1e-10
    mat_norm = mat / norms

    kept_indices: list[int] = []
    for i in range(len(valid)):
        if len(kept_indices) >= max_keep:
            break
        if not kept_indices:
            kept_indices.append(i)
            continue
        kept_mat = mat_norm[kept_indices]
        sims = kept_mat @ mat_norm[i]
        if float(sims.max()) < threshold:
            kept_indices.append(i)

    # Edge case: all filtered out → fallback to first item
    if not kept_indices:
        return [valid[0][0]]
    return [valid[i][0] for i in kept_indices]


# ── Main entry point ─────────────────────────────────────────────────────────


async def generate_summary(session_id: str) -> dict | None:
    """Generate Phase 4 summary for a session.

    Returns dict with: opening, body, session_type, summary_text, vector
    Or None if session should be skipped (no usable data).
    """
    chunks = storage.get_session_chunks(session_id)
    if not chunks:
        return None

    first_prompt = _get_first_user_prompt(chunks)
    if not first_prompt:
        return None

    is_multi = _is_multi_agent(first_prompt)
    session_type = "multi_agent" if is_multi else "regular"

    if is_multi:
        # ── Multi-agent session ──────────────────────────────────────────
        opening = _extract_discussion_topic(first_prompt)
        if not opening:
            opening = _truncate(first_prompt)

        broadcasts = _extract_broadcasts(chunks)
        body = "\n".join(broadcasts) if broadcasts else ""
    else:
        # ── Regular session ──────────────────────────────────────────────
        opening = _truncate(first_prompt)

        assistant_items = _get_assistant_chunks(chunks)
        if assistant_items:
            deduped = _cosine_dedup(
                assistant_items,
                threshold=BODY_DEDUP_COSINE_THRESHOLD,
                max_keep=MAX_BODY_ITEMS,
            )
            body = "\n".join(deduped)
        else:
            # No assistant_text — use subsequent user_prompts as fallback
            user_chunks = [
                c
                for c in chunks
                if c.get("hook_type") == "user_prompt" and c.get("text")
            ]
            user_chunks.sort(
                key=lambda c: (c.get("created_at", ""), c.get("chunk_index", 0))
            )
            # Skip first (used for opening), take next few
            fallback_texts = [c["text"] for c in user_chunks[1 : MAX_BODY_ITEMS + 1]]
            body = "\n".join(fallback_texts)

    # Edge case: both empty → skip
    if not opening and not body:
        return None

    # Compose summary_text = opening + "\n" + body
    summary_text = opening
    if body:
        summary_text += "\n" + body

    # Embed the combined summary for Layer 1 search
    vectors = await provider.embed_async([summary_text])
    vector = vectors[0]

    return {
        "opening": opening,
        "body": body,
        "session_type": session_type,
        "summary_text": summary_text,
        "vector": vector,
    }
