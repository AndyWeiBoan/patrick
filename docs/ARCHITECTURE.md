# Patrick — Architecture Document

> Zero-token, local-only Chat Memory MCP Server for Claude Code

---

## 1. Building Blocks (Bottom → Top)

### Layer 0 — External Dependencies

```
+-------------------+  +------------------+  +------------------+
|   fastembed        |  |   LanceDB        |  |   tokenizers     |
|   (ONNX runtime)   |  |   (embedded DB)   |  |   (HuggingFace)  |
|                    |  |                   |  |                  |
|  sentence-trans-   |  |  Lance columnar   |  |  Token-aware     |
|  formers/para-     |  |  storage, cosine  |  |  text splitting  |
|  phrase-MiniLM-    |  |  vector search,   |  |  (400 tok chunks |
|  L12-v2 (384-dim)  |  |  merge_insert     |  |   + 50 overlap)  |
+-------------------+  +------------------+  +------------------+
```

### Layer 1 — Core Modules

```
+---------------------------------------------------------+
|                     config.py                            |
|  HOST=127.0.0.1  PORT=3141  VECTOR_DIM=384              |
|  CHUNK_SIZE=400  CHUNK_OVERLAP=50                        |
|  BATCH_SIZE=16   BATCH_TIMEOUT=2.0s                      |
|  DATA_DIR=~/.patrick/data                                |
+---------------------------------------------------------+
          |                    |                   |
          v                    v                   v
+------------------+  +------------------+  +------------------+
|  embedding.py    |  |  storage.py      |  |  observer.py     |
|  (Singleton)     |  |  (Singleton)     |  |  (Async Worker)  |
|                  |  |                  |  |                  |
|  embed_sync()    |  |  2 LanceDB      |  |  asyncio.Queue   |
|  embed_async()   |  |  tables:        |  |  _batch_worker   |
|  chunk_text()    |  |  - sessions     |  |  _process_item   |
|  text_hash()     |  |  - turn_chunks  |  |  observe_handler |
+------------------+  +------------------+  +------------------+
          \                    |                   /
           \                   |                  /
            v                  v                 v
         +--------------------------------------+
         |             tools.py                  |
         |                                       |
         |  memory_save()                        |
         |  memory_search()      (MCP tools)     |
         |  memory_deep_search()                 |
         |  memory_sessions()                    |
         +--------------------------------------+
                          |
                          v
         +--------------------------------------+
         |             server.py                 |
         |                                       |
         |  FastMCP (SSE transport)              |
         |  POST /observe  (hook endpoint)       |
         |  uvicorn on localhost:3141            |
         +--------------------------------------+
```

### Layer 2 — External Integration

```
+--------------------------------------+
|         Claude Code Hooks            |
|                                      |
|  hooks/session_start.py              |
|  hooks/prompt_submit.py              |
|  hooks/post_tool_use.py              |
|  hooks/stop.py                       |
|                                      |
|  (Python stdlib, fire-and-forget)    |
+--------------------------------------+
```

---

## 2. LanceDB Schema

### session_summaries

```
+----------------+-------------------+-----------------------------------+
| Field          | Type              | Description                       |
+----------------+-------------------+-----------------------------------+
| session_id     | string (UUID)     | Primary key, merge_insert target  |
| summary_text   | string            | Top-3 centroid chunks, auto-gen   |
| hint           | string (nullable) | Agent LLM synthesis, manual only  |
| vector         | float32[384]      | Centroid OR hint_vector           |
| created_at     | string (ISO 8601) | Immutable after first insert      |
| updated_at     | string (ISO 8601) | Refreshed on each upsert         |
+----------------+-------------------+-----------------------------------+
```

### turn_chunks

```
+----------------+-------------------+-----------------------------------+
| Field          | Type              | Description                       |
+----------------+-------------------+-----------------------------------+
| chunk_id       | string (UUID)     | Unique per chunk                  |
| session_id     | string (UUID)     | FK to session_summaries           |
| turn_id        | string (UUID)     | Groups chunks from one text input |
| chunk_index    | int32             | 0-based within turn               |
| total_chunks   | int32             | How many chunks this turn made    |
| role           | string            | "user" | "assistant"              |
| text           | string            | Actual chunk content              |
| vector         | float32[384]      | Embedding vector                  |
| source         | string            | "hook" | "manual"                 |
| text_hash      | string            | SHA-256, exact dedup              |
| source_file    | string (nullable) | File path if applicable           |
| created_at     | string (ISO 8601) | Timestamp                         |
+----------------+-------------------+-----------------------------------+
```

---

## 3. Data Flow Diagrams

### 3.1 Hook Ingestion (Write Path)

```
 Claude Code Session
 ═══════════════════

 ┌─────────────┐    stdin JSON     ┌────────────────────┐
 │ Claude Code  │ ──────────────> │  Hook Script        │
 │ (hooks.json) │                  │  (Python stdlib)    │
 └─────────────┘                  │                     │
                                   │  Extract:           │
                                   │  - session_id       │
                                   │  - text / tool data │
                                   │  - role             │
                                   └────────┬───────────┘
                                            │
                                  HTTP POST │  fire-and-forget
                                  timeout 3s│  (失敗靜默)
                                            │
                                            v
 ┌──────────────────────────────────────────────────────────────┐
 │                    Patrick Server (:3141)                     │
 │                                                              │
 │  POST /observe                                               │
 │  ┌──────────────────┐                                        │
 │  │ observe_handler() │──── validate JSON + UUID              │
 │  │                   │──── enqueue to asyncio.Queue          │
 │  │                   │──── return 202 Accepted               │
 │  └──────────────────┘                                        │
 │           │                                                  │
 │           │ asyncio.Queue                                    │
 │           v                                                  │
 │  ┌──────────────────────────────────────────────────┐        │
 │  │ _batch_worker()  (background, infinite loop)      │        │
 │  │                                                   │        │
 │  │  Collect up to 16 items or wait 2.0s              │        │
 │  │                                                   │        │
 │  │  for each item:                                   │        │
 │  │  ┌─────────────────────────────────────────────┐  │        │
 │  │  │ _process_item()                              │  │        │
 │  │  │                                              │  │        │
 │  │  │  if hook="stop":                             │  │        │
 │  │  │    → compute_and_upsert_centroid()           │  │        │
 │  │  │    → return (no text to embed)               │  │        │
 │  │  │                                              │  │        │
 │  │  │  chunk_text(text)                            │  │        │
 │  │  │    → [chunk1, chunk2, ...]                   │  │        │
 │  │  │                                              │  │        │
 │  │  │  embed_async(chunks)    ← run_in_executor   │  │        │
 │  │  │    → [vec1, vec2, ...]    (thread pool)     │  │        │
 │  │  │                                              │  │        │
 │  │  │  make_chunk_records()                        │  │        │
 │  │  │    → SHA-256 dedup check                     │  │        │
 │  │  │    → build records                           │  │        │
 │  │  │                                              │  │        │
 │  │  │  add_chunks(records)                         │  │        │
 │  │  │    → append to turn_chunks table             │  │        │
 │  │  └─────────────────────────────────────────────┘  │        │
 │  │                                                   │        │
 │  │  After batch:                                     │        │
 │  │  for each unique session_id:                      │        │
 │  │    compute_and_upsert_centroid(session_id)        │        │
 │  │      → numpy mean of all session vectors          │        │
 │  │      → top-3 similar chunks → summary_text        │        │
 │  │      → upsert to session_summaries                │        │
 │  └──────────────────────────────────────────────────┘        │
 └──────────────────────────────────────────────────────────────┘
```

### 3.2 memory_save (Manual Write Path)

```
 Claude (MCP tool call)
 ══════════════════════

 memory_save(text, session_id, summary="topic hint")
       │
       v
 ┌─────────────────────────────────────────┐
 │  tools.py :: memory_save()               │
 │                                          │
 │  1. chunk_text(text)                     │
 │  2. embed_async(chunks) → vectors        │
 │  3. make_chunk_records() (dedup)         │
 │  4. add_chunks(records)                  │
 │                                          │
 │  if summary provided:                    │
 │    5. embed_async([summary]) → hint_vec  │
 │    6. compute_and_upsert_centroid(       │
 │         session_id,                      │
 │         hint=summary,       ← Agent 寫的 │
 │         hint_vector=hint_vec             │
 │       )                                  │
 │  else:                                   │
 │    5. compute_and_upsert_centroid(       │
 │         session_id                       │
 │       )                                  │
 └─────────────────────────────────────────┘
```

### 3.3 Centroid Logic (session_summaries 更新策略)

```
 compute_and_upsert_centroid(session_id, hint?, hint_vector?)
 ═══════════════════════════════════════════════════════════

 1. Fetch all chunks for session
 2. Compute centroid = numpy.mean(all_vectors)
 3. Rank chunks by cosine_sim(chunk, centroid)
 4. summary_text = top-3 chunks joined with " | "

 Decision tree:
 ┌─────────────────────────────────────────────────┐
 │  hint + hint_vector provided?                    │
 │                                                  │
 │  YES ──→ upsert(                                │
 │            vector = hint_vector,  ← 語義更精準    │
 │            hint = summary,                       │
 │            summary_text = centroid top-3          │
 │          )                                       │
 │                                                  │
 │  NO ──→ Session already has hint?                │
 │          │                                       │
 │          │ YES ──→ update_summary_text_only()     │
 │          │         (保留 hint + hint_vector,       │
 │          │          只更新 summary_text)            │
 │          │                                       │
 │          │ NO ──→ upsert(                        │
 │          │          vector = centroid_vector,     │
 │          │          summary_text = centroid top-3 │
 │          │        )  ← centroid 暫代               │
 └─────────────────────────────────────────────────┘

 Priority: hint_vector > existing hint > centroid_vector
```

### 3.4 Search: memory_search (Fast Path)

```
 memory_search(query="some question", top_k=10)
 ═══════════════════════════════════════════════

 ┌──────────┐     embed      ┌──────────────┐
 │  query    │ ──────────>  │  query_vector  │
 └──────────┘               └───────┬────────┘
                                     │
                                     v
                          ┌─────────────────────┐
                          │  turn_chunks table   │
                          │  cosine search       │
                          │  top_k = 10          │
                          └──────────┬──────────┘
                                     │
                                     v
                          ┌─────────────────────┐
                          │  Format results      │
                          │  score = similarity  │
                          └─────────────────────┘
```

### 3.5 Search: memory_deep_search (Two-Layer Path)

```
 memory_deep_search(query="some question", top_k=10)
 ════════════════════════════════════════════════════

 ┌──────────┐     embed      ┌──────────────┐
 │  query    │ ──────────>  │  query_vector  │
 └──────────┘               └───────┬────────┘
                                     │
              ╔══════════════════════╧═══════════════╗
              ║        LAYER 1 — Session Coarse      ║
              ║                                      ║
              ║  search session_summaries             ║
              ║  top_k = 5                            ║
              ║  filter: cosine_sim > 0.3             ║
              ║                                      ║
              ║  Result: [session_A, session_B, ...]  ║
              ╚══════════════════╤═══════════════════╝
                                 │
                      ┌──────────┴──────────┐
                      │                      │
                  >= 3 sessions          < 3 sessions
                      │                      │
                      v                      v
              ╔══════════════╗      ╔══════════════════╗
              ║  LAYER 2     ║      ║  FALLBACK        ║
              ║  Scoped      ║      ║  Global search   ║
              ║              ║      ║                  ║
              ║  search      ║      ║  search          ║
              ║  turn_chunks ║      ║  turn_chunks     ║
              ║  WHERE       ║      ║  (no filter)     ║
              ║  session_id  ║      ║                  ║
              ║  IN [...]    ║      ║  Ensures no-     ║
              ║              ║      ║  summary sessions║
              ║              ║      ║  still found     ║
              ╚══════╤═══════╝      ╚════════╤═════════╝
                     │                        │
                     └───────────┬────────────┘
                                 │
                                 v
              ╔══════════════════════════════════╗
              ║     CONTEXT EXPANSION             ║
              ║                                   ║
              ║  For each hit chunk:              ║
              ║    get_turn_chunks(turn_id)       ║
              ║    → fetch ALL sibling chunks     ║
              ║    → sort by chunk_index          ║
              ║    → reconstruct full turn        ║
              ╚═══════════════╤══════════════════╝
                              │
                              v
              ┌───────────────────────────────┐
              │  Format results:               │
              │  - text (expanded)             │
              │  - session_id                  │
              │  - role                        │
              │  - score                       │
              │  - source ("hook" | "manual")  │
              │  - retrieved_from_sessions     │
              └───────────────────────────────┘
```

---

## 4. Module Reference

### config.py — Constants

| Constant | Value | Purpose |
|----------|-------|---------|
| `HOST` | `127.0.0.1` | Server bind address |
| `PORT` | `3141` | Server port |
| `DATA_DIR` | `~/.patrick/data` | LanceDB storage path |
| `EMBEDDING_MODEL_FASTEMBED` | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | 384-dim ONNX model |
| `VECTOR_DIM` | `384` | Embedding dimension |
| `CHUNK_SIZE` | `400` | Tokens per chunk |
| `CHUNK_OVERLAP` | `50` | Overlap tokens between chunks |
| `BATCH_SIZE` | `16` | Max items per batch |
| `BATCH_TIMEOUT` | `2.0` | Seconds before flushing partial batch |
| `TOP_K_SESSIONS` | `5` | Layer 1 coarse filter count |
| `TOP_K_CHUNKS` | `10` | Layer 2 fine retrieval count |
| `MIN_SESSION_SCORE` | `0.3` | Minimum cosine sim for session filter |

### embedding.py — EmbeddingProvider (Singleton)

| Method | Sync/Async | Description |
|--------|------------|-------------|
| `initialize()` | sync | Load fastembed model + HF tokenizer (once) |
| `embed_sync(texts)` | sync | Blocking embedding (called in thread pool) |
| `embed_async(texts)` | async | Non-blocking wrapper via `run_in_executor` |
| `chunk_text(text)` | sync | Token-aware splitting (400 tok + 50 overlap) |
| `text_hash(text)` | static | SHA-256 hex digest for dedup |

### storage.py — Storage (Singleton)

| Method | Description |
|--------|-------------|
| `initialize()` | Create/open LanceDB tables, run hint migration |
| `upsert_session_summary(...)` | Merge-insert to session_summaries |
| `get_session(session_id)` | Fetch one session record |
| `get_session_chunks(session_id)` | All chunks for a session (full scan) |
| `update_summary_text_only(...)` | Refresh summary_text, preserve hint/vector |
| `search_sessions(query_vector, top_k)` | Cosine search over summaries |
| `list_sessions()` | Return all sessions |
| `hash_exists(text_hash)` | Check SHA-256 dedup |
| `add_chunks(records)` | Append-only insert to turn_chunks |
| `search_chunks(query_vector, top_k, session_ids?)` | Cosine search, optional session filter |
| `get_turn_chunks(turn_id)` | Fetch sibling chunks (context expansion) |
| `make_chunk_records(...)` | Build chunk dicts with dedup check |
| `compute_and_upsert_centroid(session_id, hint?, hint_vector?)` | Pure numpy centroid + upsert |

### observer.py — Hook Event Processor

| Function | Description |
|----------|-------------|
| `observe_handler(request)` | POST /observe → validate → enqueue → 202 |
| `_process_item(item)` | Chunk + embed + dedup + write one event |
| `_batch_worker()` | Background loop: batch collect → process → centroid |
| `start_worker()` | Create background task in event loop |

### tools.py — MCP Tool Implementations

| Tool | Description |
|------|-------------|
| `memory_save(text, session_id?, role?, summary?, source_file?)` | Manual save with optional hint |
| `memory_search(query, top_k?)` | Fast flat search over turn_chunks |
| `memory_deep_search(query, top_k?)` | Two-layer search with context expansion |
| `memory_sessions()` | List all sessions with metadata |

### server.py — Application Entry Point

| Function | Description |
|----------|-------------|
| `main()` | Port check → init provider + storage → start FastMCP + uvicorn |

---

## 5. Hook Scripts

```
hooks/
├── session_start.py   — Extract session_id, POST to /observe, inject PATRICK_SESSION_ID
├── prompt_submit.py   — Capture user input, POST text + role="user"
├── post_tool_use.py   — Format tool results to natural language, POST role="assistant"
└── stop.py            — Signal session end, trigger final centroid update
```

**Tool formatting** (`post_tool_use.py`):

| Tool | Format |
|------|--------|
| Bash | `執行了指令：{cmd}\n結果：{output[:500]}` |
| Read | `讀取了檔案：{path}` |
| Write | `寫入了檔案：{path}` |
| Edit/MultiEdit | `修改了檔案：{path}，舊：…，新：…` |
| Glob/Grep | `搜尋 pattern + path` |
| WebFetch/WebSearch | `query` |
| Other | `使用了工具 {name}，輸入：{input[:200]}` |

All scripts: Python stdlib (`urllib.request`), 3s timeout, fail-silent.

---

## 6. End-to-End Lifecycle

```
 Session 開始                                    Session 結束
 ════════                                        ════════
    │                                               │
    │  session_start hook                           │  stop hook
    │  → POST /observe                              │  → POST /observe
    │  → inject PATRICK_SESSION_ID                  │  → final centroid
    │                                               │
    ├── User types prompt ──────────────────────┐   │
    │   prompt_submit hook                      │   │
    │   → POST {text, role:"user"}              │   │
    │   → queue → batch → embed → turn_chunks   │   │
    │                                           │   │
    │   ┌── Claude responds ────────────────┐   │   │
    │   │   (may use tools)                 │   │   │
    │   │                                   │   │   │
    │   │   post_tool_use hook (per tool)   │   │   │
    │   │   → format to natural language    │   │   │
    │   │   → POST {text, role:"assistant"} │   │   │
    │   │   → queue → batch → embed         │   │   │
    │   │       → turn_chunks               │   │   │
    │   │                                   │   │   │
    │   │   Claude may call memory_save     │   │   │
    │   │   (rare: 0-2 per session)         │   │   │
    │   │   → embed text + hint             │   │   │
    │   │   → turn_chunks + session_summary │   │   │
    │   └───────────────────────────────────┘   │   │
    │                                           │   │
    │   Each batch triggers centroid update:    │   │
    │   → numpy mean → top-3 → summary_text    │   │
    │   → upsert session_summaries             │   │
    │                                           │   │
    ├── Next turn... (repeat) ──────────────────┘   │
    │                                               │
    └───────────────────────────────────────────────┘

 下個 Session:
 ═══════════
    Claude calls memory_deep_search("上次討論了什麼？")
      → Layer 1: session_summaries cosine search
      → Layer 2: turn_chunks scoped search
      → Context expansion: sibling chunks
      → 完整上下文返回給 Claude
```

---

## 7. Design Principles

| Principle | Implementation |
|-----------|---------------|
| **Zero LLM tokens** | Centroid = pure numpy; no summarization API calls |
| **Agent hint > centroid** | hint_vector 不被 centroid 覆蓋；hint 語義更精準 |
| **Hook 不等 embedding** | Queue + 202 Accepted; batch worker async |
| **Embedding 不 block event loop** | `run_in_executor` offload to thread pool |
| **Exact dedup** | SHA-256 hash check before every write |
| **Graceful degradation** | < 3 good sessions → fallback to global search |
| **Context expansion** | turn_id groups chunks; sibling fetch on query |
| **Fire-and-forget hooks** | 3s timeout, fail-silent, never block Claude |
| **Singleton resources** | One model, one DB connection, no reload |
| **Two-path complementarity** | Hook = auto capture all; memory_save = agent curated |
