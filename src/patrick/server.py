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

from .config import COMPACT_CHECK_INTERVAL, COMPACT_FRAGMENT_THRESHOLD, HOST, PORT
from .embedding import provider
from .observer import observe_handler, start_worker
from .storage import storage
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
        "Local memory server that stores and retrieves conversation history across sessions. "
        "PROACTIVE USAGE RULES:\n"
        "1. Use memory_search for quick single-fact lookups or semantic search across all stored memories.\n"
        "2. Use memory_save sparingly: only for explicit user requests, major decisions, or session end summaries.\n"
        "SEARCH STRATEGY:\n"
        "- First search with include_tool_calls=False (default) for clean semantic results.\n"
        "- If results are insufficient or you need to debug tool usage details, "
        "search again with include_tool_calls=True to include tool call records."
    ),
)

# Register tools
mcp.tool()(memory_save)
mcp.tool()(memory_search)
# mcp.tool()(memory_deep_search)   # disabled — not mature yet
mcp.tool()(memory_sessions)

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
        await _orig_startup(sockets)

    server.startup = _patched_startup
    server.run()


if __name__ == "__main__":
    main()
