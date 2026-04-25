"""Patrick MCP server — entry point.

Starts a FastMCP SSE server that exposes 4 memory tools + /observe hook endpoint.
All hook events and memory operations share one embedding model + LanceDB instance.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import sys

import uvicorn
from mcp.server.fastmcp import FastMCP

from .config import (
    COMPACT_CHECK_INTERVAL,
    COMPACT_FRAGMENT_THRESHOLD,
    HOST,
    PORT,
    SUMMARY_COOLDOWN,
    SUMMARY_SCAN_INTERVAL,
)
from .embedding import provider
from .observer import observe_handler, start_worker
from .storage import storage
from .summary import generate_summary
from .tools import (
    # memory_deep_search,  # disabled — not mature yet
    memory_save,
    memory_search,
    memory_sessions,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── MCP server setup ──────────────────────────────────────────────────────────

mcp = FastMCP(
    name="patrick-memory",
    instructions=(
        "Local memory server that stores and retrieves conversation history across sessions.\n\n"
        "TOOL SELECTION — pick by question type:\n"
        "- Browse/timeline questions ('what did we work on recently?', 'which session discussed X?')\n"
        "  → memory_sessions first. Scan the opening list, then drill down with include_body=True.\n"
        "- Precise fact lookup ('how was X implemented?', 'what was the decision on Y?')\n"
        "  → memory_search. Searches all chunk content semantically.\n"
        "- memory_save: use sparingly — only for explicit user requests or major decisions.\n\n"
        "SEARCH TIPS:\n"
        "- Default memory_search uses hook_type=None (all chunks). Add hook_type='assistant_text'\n"
        "  for higher-quality semantic results, or hook_type='user_prompt' for user questions.\n"
        "- If results are insufficient, try mode='hybrid' for BM25+vector fusion."
    ),
)

# Register tools — order matters: memory_sessions first (browse entry point)
mcp.tool()(memory_sessions)
mcp.tool()(memory_search)
mcp.tool()(memory_save)
# mcp.tool()(memory_deep_search)   # disabled — not mature yet

# Register /observe custom route
mcp.custom_route("/observe", methods=["POST"])(observe_handler)


# ── Scheduled compaction ──────────────────────────────────────────────────────

_compact_task: asyncio.Task | None = None


async def _scheduled_compact() -> None:
    """Periodically check LanceDB fragment count and compact if above threshold."""
    while True:
        await asyncio.sleep(COMPACT_CHECK_INTERVAL)
        try:
            loop = asyncio.get_running_loop()
            counts = await loop.run_in_executor(None, storage.fragment_count)
            max_frags = max(counts.values()) if counts else 0
            if max_frags >= COMPACT_FRAGMENT_THRESHOLD:
                logger.info(
                    "Fragment count exceeds threshold (%d >= %d): %s — starting compaction",
                    max_frags, COMPACT_FRAGMENT_THRESHOLD, counts,
                )
                await loop.run_in_executor(None, storage.compact)
                counts_after = await loop.run_in_executor(None, storage.fragment_count)
                logger.info("Scheduled compaction done: %s → %s", counts, counts_after)
            else:
                logger.debug("Fragment check OK (%s), below threshold %d", counts, COMPACT_FRAGMENT_THRESHOLD)
        except Exception as exc:
            logger.warning("Scheduled compaction error: %s", exc)


def start_compact_scheduler() -> None:
    """Kick off the scheduled compaction task (must be called from a running event loop)."""
    global _compact_task
    loop = asyncio.get_running_loop()
    _compact_task = loop.create_task(_scheduled_compact())
    logger.info(
        "Compaction scheduler started (interval=%ds, threshold=%d fragments)",
        COMPACT_CHECK_INTERVAL, COMPACT_FRAGMENT_THRESHOLD,
    )


# ── Phase 4: Summary backfill ───────────────────────────────────────────────

_summary_task: asyncio.Task | None = None


async def _summary_backfill() -> None:
    """Periodically scan for sessions needing Phase 4 summary and generate them.

    Discovery paths:
    1. summary_status = 'pending' — regular sessions, stop hook fired
    2. Stale sessions (last chunk > SUMMARY_COOLDOWN ago) not yet 'done' —
       multi-agent sessions or any missed session
    """
    await asyncio.sleep(10)  # let server settle on startup
    while True:
        try:
            loop = asyncio.get_running_loop()
            sessions = await loop.run_in_executor(
                None, storage.get_sessions_needing_summary, SUMMARY_COOLDOWN
            )
            if sessions:
                logger.info("Summary backfill: %d sessions to process", len(sessions))
                for sid in sessions:
                    try:
                        result = await generate_summary(sid)
                        if result:
                            storage.upsert_session_summary(
                                session_id=sid,
                                summary_text=result["summary_text"],
                                vector=result["vector"],
                                opening=result["opening"],
                                body=result["body"],
                                session_type=result["session_type"],
                                summary_status="done",
                            )
                            logger.info(
                                "Summary generated for session %s (%s)",
                                sid[:8], result["session_type"],
                            )
                        else:
                            # No usable data — mark skipped to avoid re-scanning
                            storage.update_session_status(sid, "skipped")
                            logger.info("Summary skipped for session %s (no data)", sid[:8])
                    except Exception as exc:
                        logger.warning(
                            "Summary generation failed for %s: %s", sid[:8], exc
                        )
        except Exception as exc:
            logger.warning("Summary backfill scan error: %s", exc)

        await asyncio.sleep(SUMMARY_SCAN_INTERVAL)


def start_summary_scheduler() -> None:
    """Kick off the summary backfill task (must be called from a running event loop)."""
    global _summary_task
    loop = asyncio.get_running_loop()
    _summary_task = loop.create_task(_summary_backfill())
    logger.info(
        "Summary backfill scheduler started (interval=%ds, cooldown=%ds)",
        SUMMARY_SCAN_INTERVAL, SUMMARY_COOLDOWN,
    )


# ── Startup / shutdown ────────────────────────────────────────────────────────

def _check_port(host: str, port: int) -> None:
    """Fail early with a clear message if port is in use."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
        except OSError:
            logger.error(
                "Port %d on %s is already in use. "
                "Kill the previous patrick server or change PORT in config.py.",
                port, host,
            )
            sys.exit(1)



def main() -> None:
    """Run the server (called by `python -m patrick.server` or entry point)."""
    _check_port(HOST, PORT)

    # Sync init — safe to run before uvicorn
    provider.initialize()
    logger.info("Embedding model loaded")
    storage.initialize()
    logger.info("LanceDB initialized")
    storage.compact()
    logger.info("LanceDB compaction done")

    app = mcp.sse_app()

    config = uvicorn.Config(
        app,
        host=HOST,
        port=PORT,
        log_level="info",
        access_log=True,
    )
    server = uvicorn.Server(config)

    # Patch uvicorn startup so the background worker is created INSIDE
    # uvicorn's own event loop (not a separate asyncio.run() loop that closes).
    _orig_startup = server.startup

    async def _patched_startup(sockets=None):
        start_worker()              # observer batch worker
        start_compact_scheduler()   # periodic LanceDB compaction
        start_summary_scheduler()   # Phase 4 summary backfill
        await _orig_startup(sockets)

    server.startup = _patched_startup
    server.run()


if __name__ == "__main__":
    main()
