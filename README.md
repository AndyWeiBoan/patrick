<p align="center">
  <img src="https://upload.wikimedia.org/wikipedia/commons/thumb/2/28/Patrick_Star_character.png/250px-Patrick_Star_character.png" alt="Patrick Star" width="180"/>
</p>

<h1 align="center">Patrick</h1>

<p align="center">
  <a href="README.md"><b>English</b></a> &nbsp;|&nbsp; <a href="docs/zh/README_ZH.md">中文</a>
</p>

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
- **[fastembed](https://github.com/qdrant/fastembed)** + **ONNX Runtime** — runs `paraphrase-multilingual-MiniLM-L12-v2` (384-dimensional) fully locally via ONNX. No GPU required. Cold-start in ~1s on Apple Silicon.
- Supports mixed-language conversations (Chinese + English) out of the box.
- Heavy I/O-bound embedding work is offloaded to a thread pool via `asyncio.run_in_executor` to avoid blocking the MCP event loop.

### Retrieval Algorithm
1. **Layer 1 (session coarse filter)** — embed the query and cosine-search `session_summaries`. Returns the top sessions above a similarity threshold.
2. **Layer 2 (chunk fine search)** — cosine-search `turn_chunks` filtered to sessions from Layer 1.
3. **Graceful degradation** — if fewer than 3 sessions pass the threshold, fall back to a global chunk search across all sessions.
4. **Context expansion** — for each matched chunk, fetch all sibling chunks from the same `turn_id` to restore full conversational context.

### Session Summary (Two-Stage Pipeline)

**Why no LLM summarization?** Patrick's core constraint is zero token cost. Calling an LLM to summarize each session would add latency, API costs, and break the "fully local" promise. Instead, we use a two-stage pipeline that combines pure math with local embeddings — no external API calls.

**Stage 1 — Centroid (immediate, at session end):**
1. Loads all chunk vectors for that session.
2. Computes the **mean centroid** of all chunk vectors (pure numpy, zero LLM cost).
3. Finds the top-3 chunks closest to the centroid by cosine similarity.
4. Concatenates their text as a provisional `summary_text`.
5. Upserts the session row with the centroid vector as a **temporary** Layer 1 search anchor.

**Stage 2 — Structured summary (background backfill):**
A background scheduler picks up sessions marked `pending` and generates a richer summary:
- **Regular sessions:** `opening` = first user prompt (≤200 chars), `body` = top-5 assistant responses (filtered by cosine ≥ 0.8 diversity threshold to avoid repetitive content).
- **Multi-agent sessions:** `opening` = discussion topic, `body` = owner's broadcast messages.
- The combined `opening + body` text is **embedded** using the same fastembed model, and this new vector **replaces** the centroid vector as the Layer 1 search anchor.

**How search uses summaries:**
- At query time, Layer 1 embeds your query and does **cosine similarity search against session vectors** in `session_summaries` — this quickly identifies which sessions are semantically relevant.
- For fully processed sessions, the search vector is the embedding of the structured summary (opening + body), which is more semantically meaningful than a raw centroid.
- Only the top-K matching sessions proceed to Layer 2, where individual chunks are searched for precise answers.
- This two-layer design avoids searching millions of chunks directly — the session vector acts as a cheap "session-level index" that narrows the search space by 10–100×.

### Deduplication (Two-Layer)

**Layer 1 — Exact dedup (on write):**
- **SHA-256** hash of each text chunk is stored in `turn_chunks.text_hash`.
- Before any write, `hash_exists()` checks for a collision — identical chunks are silently dropped.

**Layer 2 — Semantic dedup (on session end):**
- When a session ends (stop hook), Patrick scans all chunks in that session.
- Greedy algorithm: iterate chunks newest-first; keep a chunk only if its cosine similarity to **all** already-kept chunks is below 0.95.
- Chunks exceeding the threshold are **deleted from the database** — this removes paraphrased duplicates that SHA-256 cannot catch.

### MCP Server
- **[FastMCP](https://github.com/jlowin/fastmcp)** SSE server on `http://127.0.0.1:3141/sse`
- Exposes 3 tools: `memory_search`, `memory_sessions`, `memory_save`
- Custom `/observe` POST endpoint for hook ingestion

---

## Installation

**Prerequisites:** Python 3.10+, [Claude Code](https://docs.anthropic.com/claude-code), Git

```bash
git clone https://github.com/AndyWeiBoan/patrick.git
cd patrick
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
patrick init          # download embedding model (~220 MB)
patrick setup --auto  # configure Claude Code hooks + MCP server
patrick start         # start the memory server
```

Then **restart Claude Code** for the hooks to take effect.

> **[Full installation guide](docs/INSTALL.md)** — step-by-step walkthrough, background service setup, troubleshooting, and uninstall instructions.

---

## CLI Reference

| Command | Description |
|---|---|
| `patrick init` | Pre-download embedding model and run sanity check |
| `patrick setup` | Configure Claude Code hooks + MCP in `~/.claude/settings.json` |
| `patrick start` | Start the memory server |
| `patrick doctor` | Health check — server, hooks, settings, model cache |
| `patrick hooks-path` | Print the absolute path to installed hook scripts |

---

## Data Location

All memory data is stored locally at:

```
~/.patrick/data/
```

Nothing leaves your machine.

---

## MCP Tools Reference

| Tool | When to use |
|---|---|
| `memory_search` | Semantic search across all chunks. Modes: `vector` (default), `hybrid` (BM25+vector+rerank), `recency` (hybrid+time-decay). Supports `hook_type` filter (`assistant_text`, `user_prompt`, `tool_use`). |
| `memory_sessions` | Browse sessions with summaries. Supports `include_body`, `session_type` filter, `after` date filter, pagination (`limit`/`offset`). |
| `memory_save` | Currently disabled — hooks handle all storage automatically. |

---

## Project Status

- **Phase 1** ✅: Automatic hook capture, two-layer vector search, centroid session summaries, SHA-256 dedup, MCP server, `patrick setup` / `init` / `doctor` CLI.
- **Phase 2** ✅: BM25 hybrid search, cross-encoder reranking, cosine semantic dedup, eval harness + CI quality gate.
- **Phase 3** ✅: Time-decay recency weighting, hook_type classification, multi-value filter.
- **Phase 4** (in progress): Session summary UI improvements (opening/body fields), summary backfill scheduler.
