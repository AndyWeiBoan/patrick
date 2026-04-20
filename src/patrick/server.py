"""Patrick MCP server — entry point.

Starts a FastMCP SSE server that exposes 4 memory tools + /observe hook endpoint.
All hook events and memory operations share one embedding model + LanceDB instance.
"""

from __future__ import annotations

import logging
import socket
import sys

import uvicorn
from mcp.server.fastmcp import FastMCP

from .config import HOST, PORT
from .embedding import provider
from .observer import observe_handler, start_worker
from .storage import storage
from .tools import (
    memory_deep_search,
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
        "1. At the START of any session where the user mentions past work, an ongoing project, "
        "a previous decision, or anything that implies continuity — call memory_deep_search FIRST, "
        "before answering. Do not rely on in-context memory alone.\n"
        "2. Use memory_deep_search for questions needing full context or cross-session recall.\n"
        "3. Use memory_search for quick single-fact lookups.\n"
        "4. Use memory_save sparingly: only for explicit user requests, major decisions, or session end summaries. "
        "memory_save is ALWAYS required after memory_deep_search — see that tool's instructions."
    ),
)

# Register tools
mcp.tool()(memory_save)
mcp.tool()(memory_search)
mcp.tool()(memory_deep_search)
mcp.tool()(memory_sessions)

# Register /observe custom route
mcp.custom_route("/observe", methods=["POST"])(observe_handler)


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
        start_worker()   # task lives in uvicorn's loop — correct
        await _orig_startup(sockets)

    server.startup = _patched_startup
    server.run()


if __name__ == "__main__":
    main()
