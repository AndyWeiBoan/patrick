<p align="center">
  <img src="https://upload.wikimedia.org/wikipedia/en/3/33/Patrick_Star.svg" alt="Patrick Star" width="180"/>
</p>

<h1 align="center">Patrick</h1>

<p align="center">
  <em>Zero-token, fully-local cross-session memory for Claude Code.</em>
</p>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.10%2B-blue"/>
  <img alt="License" src="https://img.shields.io/badge/license-MIT-green"/>
  <img alt="MCP" src="https://img.shields.io/badge/MCP-SSE-purple"/>
</p>

---

Claude Code is amnesiac by design — every session starts cold. You re-explain the same context, retrace the same decisions, and re-discover the same pitfalls, every single day.

Patrick fixes this. It captures your conversation history automatically via Claude Code hooks, stores everything locally in an embedded vector database, and exposes a fast semantic search MCP server that Claude queries at the start of each session.

No cloud. No LLM extraction calls. No token overhead. Fully automatic.

---

## Why Patrick

| | Patrick | CLAUDE.md | mem0 / Zep |
|---|---|---|---|
| **Token cost** | Zero | Zero | High (LLM extraction) |
| **Automation** | Fully automatic | Manual | Semi-automatic |
| **Local / private** | Yes | Yes | No (cloud) |
| **Claude Code native** | Yes (MCP + hooks) | Yes | Needs custom integration |
| **Cross-session recall** | Yes | Manual | Yes |

**Three properties no other tool delivers simultaneously: zero token cost, fully automatic capture, native MCP integration.**

Specific advantages:

- **Zero token overhead** — hooks capture conversation verbatim; no LLM summarization during write
- **Fully automatic** — `SessionStart`, `UserPromptSubmit`, `PostToolUse`, and `Stop` hooks run silently in the background without any user action
- **100% local** — all data stays on your machine in an embedded LanceDB database; no API keys, no network calls
- **Two-layer retrieval** — coarse session-level filter first, then fine-grained chunk search within matching sessions; much more precise than flat search
- **Auto session summary** — centroid of all chunk vectors is computed at session end using pure numpy; a representative summary is generated with zero LLM calls
- **Exact dedup** — SHA-256 hashing prevents storing the same content twice, even across sessions

---

## Tech Stack & Algorithms

### Storage
- **[LanceDB](https://lancedb.github.io/lancedb/)** — embedded columnar vector database backed by Apache Arrow. No server process needed; runs inside the Patrick process. Two tables: `session_summaries` (one row per session) and `turn_chunks` (one row per text chunk).
- **[PyArrow](https://arrow.apache.org/docs/python/)** — columnar schema, efficient batch inserts, and filter-based table scans.

### Embeddings
- **[fastembed](https://github.com/qdrant/fastembed)** + **ONNX Runtime** — runs `multilingual-e5-small` (384-dimensional) fully locally via ONNX. No GPU required. Cold-start in ~1s on Apple Silicon.
- Supports mixed-language conversations (Chinese + English) out of the box.
- Heavy I/O-bound embedding work is offloaded to a thread pool via `asyncio.run_in_executor` to avoid blocking the MCP event loop.

### Retrieval Algorithm
1. **Layer 1 (session coarse filter)** — embed the query and cosine-search `session_summaries`. Returns the top sessions above a similarity threshold.
2. **Layer 2 (chunk fine search)** — cosine-search `turn_chunks` filtered to sessions from Layer 1.
3. **Graceful degradation** — if fewer than 3 sessions pass the threshold, fall back to a global chunk search across all sessions.
4. **Context expansion** — for each matched chunk, fetch all sibling chunks from the same `turn_id` to restore full conversational context.

### Session Summary (Centroid Algorithm)
At the end of each session, Patrick:
1. Loads all chunk vectors for that session.
2. Computes the **mean centroid** of all chunk vectors (pure numpy, zero LLM cost).
3. Finds the top-3 chunks closest to the centroid by cosine similarity.
4. Concatenates their text as the session's `summary_text`.
5. Upserts the session row in `session_summaries` with the centroid vector as the Layer 1 search anchor.

### Deduplication
- **SHA-256** hash of each text chunk is stored in `turn_chunks.text_hash`.
- Before any write, `hash_exists()` checks for a collision — duplicate chunks are silently dropped.

### MCP Server
- **[FastMCP](https://github.com/jlowin/fastmcp)** SSE server on `http://127.0.0.1:3141/sse`
- Exposes 4 tools: `memory_search`, `memory_deep_search`, `memory_sessions`, `memory_save`
- Custom `/observe` POST endpoint for hook ingestion

---

## Installation

### Prerequisites

- Python 3.10 or later
- [Claude Code](https://docs.anthropic.com/claude-code) installed and configured
- Git

### Step 1 — Clone the repository

```bash
git clone https://github.com/AndyWeiBoan/patrick.git
cd patrick
```

### Step 2 — Create a virtual environment and install

```bash
python3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate

pip install -e .
```

This installs the `patrick` CLI and all dependencies (LanceDB, fastembed, FastMCP, uvicorn, etc.).

> **First run note:** fastembed will download `multilingual-e5-small` (~120 MB) on first startup and cache it locally. Subsequent starts are instant.

### Step 3 — Start the Patrick server

```bash
patrick serve
```

You should see:

```
INFO:     Embedding model loaded
INFO:     LanceDB initialized
INFO:     Uvicorn running on http://127.0.0.1:3141
```

Keep this terminal running (or set it up as a background service — see below).

### Step 4 — Configure Claude Code

Open your Claude Code settings file. There are two locations:

- **Global** (applies to all projects): `~/.claude/settings.json`
- **Project-level** (applies to this repo only): `.claude/settings.json`

Add the following configuration (copy from `claude_config_example.json` as a reference):

```json
{
  "mcpServers": {
    "patrick-memory": {
      "type": "sse",
      "url": "http://127.0.0.1:3141/sse"
    }
  },
  "hooks": {
    "SessionStart": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "python3 /ABSOLUTE/PATH/TO/patrick/hooks/session_start.py",
            "async": true
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "python3 /ABSOLUTE/PATH/TO/patrick/hooks/prompt_submit.py",
            "async": true
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "python3 /ABSOLUTE/PATH/TO/patrick/hooks/post_tool_use.py",
            "async": true
          }
        ]
      }
    ],
    "Stop": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "python3 /ABSOLUTE/PATH/TO/patrick/hooks/stop.py",
            "async": true
          }
        ]
      }
    ]
  }
}
```

Replace `/ABSOLUTE/PATH/TO/patrick` with the actual absolute path to your cloned repository (e.g. `/Users/you/projects/patrick`).

### Step 5 — Verify

Start a new Claude Code session. Patrick will automatically begin capturing your conversation. To confirm the MCP connection is live, ask Claude:

```
List my recent memory sessions.
```

Claude will call `memory_sessions` and show you the sessions stored so far.

---

### Running Patrick as a background service (macOS)

To have Patrick start automatically on login, create a launchd plist:

```bash
cat > ~/Library/LaunchAgents/com.patrick.memory.plist << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.patrick.memory</string>
  <key>ProgramArguments</key>
  <array>
    <string>/ABSOLUTE/PATH/TO/patrick/.venv/bin/patrick</string>
    <string>serve</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>/tmp/patrick.log</string>
  <key>StandardErrorPath</key>
  <string>/tmp/patrick.err</string>
</dict>
</plist>
EOF

launchctl load ~/Library/LaunchAgents/com.patrick.memory.plist
```

Logs are available at `/tmp/patrick.log`.

---

## Data Location

All memory data is stored locally at:

```
~/.patrick/db/
```

Nothing leaves your machine.

---

## MCP Tools Reference

| Tool | When to use |
|---|---|
| `memory_search` | Quick lookup of a specific fact or phrase |
| `memory_deep_search` | Cross-session context recall (use at session start) |
| `memory_sessions` | List all stored sessions with summaries |
| `memory_save` | Explicitly save a decision or conclusion (rarely needed — hooks handle most storage) |

---

## Project Status

- **Phase 1** (current): Fully working — automatic hook capture, two-layer vector search, centroid session summaries, dedup, MCP server.
- **Phase 2** (planned): BM25 hybrid search for improved exact-keyword recall in mixed-language conversations.
- **Phase 3** (planned): `patrick init` sanity check CLI, packaging polish.
- **Phase 4** (planned): PyPI release.
