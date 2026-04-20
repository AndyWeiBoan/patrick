"""Observer — /observe endpoint + asyncio.Queue background worker."""

from __future__ import annotations

import asyncio
import logging
import re
import time

from starlette.requests import Request
from starlette.responses import JSONResponse

from .config import BATCH_SIZE, BATCH_TIMEOUT
from .embedding import provider
from .storage import storage

logger = logging.getLogger(__name__)

# Global queue for incoming hook events
_queue: asyncio.Queue[dict] = asyncio.Queue()
_worker_task: asyncio.Task | None = None

# UUID v4 pattern — validate all IDs before they touch SQL-like WHERE clauses
_UUID_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
    re.IGNORECASE,
)


def _validate_id(value: str) -> bool:
    """Return True if value is a valid UUID (safe to interpolate in LanceDB filters)."""
    return bool(_UUID_RE.match(value))


async def observe_handler(request: Request) -> JSONResponse:
    """/observe POST endpoint — accepts hook events, enqueues, returns 202 immediately."""
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    hook = data.get("hook", "")
    session_id = data.get("session_id", "")

    if not session_id:
        return JSONResponse({"error": "session_id required"}, status_code=400)

    # Validate UUID format before it propagates into LanceDB WHERE clauses.
    # Hook payloads come from external scripts; a malformed/malicious session_id
    # with quotes would break LanceDB filter expressions.
    if not _validate_id(session_id):
        return JSONResponse({"error": "session_id must be a UUID"}, status_code=400)

    await _queue.put(data)
    return JSONResponse({"status": "queued", "hook": hook}, status_code=202)


async def _process_item(item: dict) -> None:
    """Process a single hook event: embed text + write to storage."""
    hook = item.get("hook", "")
    session_id = item.get("session_id", "")
    text = item.get("text", "")
    role = item.get("role", "user")
    source = "hook"

    # stop hook carries no text but should trigger a final centroid update
    # so session_summaries reflects the completed session state.
    # Phase 2: also run session-level cosine dedup on stop.
    if hook == "stop" and session_id:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, storage.compute_and_upsert_centroid, session_id)
        logger.debug("Centroid triggered by stop hook for session %s", session_id)
        # Cosine dedup: remove semantically-redundant chunks within this session
        try:
            dropped = await loop.run_in_executor(None, storage.cosine_dedup_session, session_id)
            if dropped:
                logger.info("Cosine dedup: removed %d redundant chunks for session %s", dropped, session_id)
                # Re-run centroid after dedup so summary reflects cleaned data
                await loop.run_in_executor(None, storage.compute_and_upsert_centroid, session_id)
        except Exception as exc:
            logger.warning("Cosine dedup failed for session %s: %s", session_id, exc)
        return

    if not text or not session_id:
        return

    # Chunk text (token-aware)
    chunks = provider.chunk_text(text)

    # Embed — offloaded to thread pool, does NOT block event loop
    vectors = await provider.embed_async(chunks)

    # Build and write chunk records (with exact dedup)
    records = storage.make_chunk_records(
        texts=chunks,
        vectors=vectors,
        session_id=session_id,
        role=role,
        source=source,
    )
    if records:
        storage.add_chunks(records)
        logger.debug("Wrote %d chunks for session %s hook %s", len(records), session_id, hook)


async def _batch_worker() -> None:
    """Background worker: drain queue in batches with timeout flush."""
    logger.info("Observer worker started")
    while True:
        batch: list[dict] = []
        deadline = time.monotonic() + BATCH_TIMEOUT

        # Collect up to BATCH_SIZE items or until BATCH_TIMEOUT
        while len(batch) < BATCH_SIZE:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                item = await asyncio.wait_for(_queue.get(), timeout=remaining)
                batch.append(item)
            except asyncio.TimeoutError:
                break

        if not batch:
            continue

        # Process each item (each has its own embed call)
        for item in batch:
            try:
                await _process_item(item)
            except Exception as exc:
                logger.error("Error processing hook event: %s", exc, exc_info=True)
            finally:
                _queue.task_done()

        # After batch completes, update centroid for every session that received new chunks.
        # Batch layer is the natural throttle — one centroid update per session per batch,
        # regardless of how many chunks that session produced in this batch.
        seen_sessions = {
            item["session_id"]
            for item in batch
            if item.get("session_id") and item.get("hook") != "stop"
        }
        if seen_sessions:
            loop = asyncio.get_running_loop()
            for sid in seen_sessions:
                try:
                    await loop.run_in_executor(None, storage.compute_and_upsert_centroid, sid)
                except Exception as exc:
                    logger.error("Centroid update failed for session %s: %s", sid, exc)


def start_worker() -> None:
    """Kick off the background worker (must be called from a running event loop)."""
    global _worker_task
    loop = asyncio.get_running_loop()
    _worker_task = loop.create_task(_batch_worker())
    logger.info("Observer batch worker scheduled")
