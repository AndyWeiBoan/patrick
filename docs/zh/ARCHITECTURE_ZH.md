# Patrick — 架構文件

> 零 token、純本地、全自動的 Claude Code 對話記憶 MCP Server

---

## 1. 積木（由底往上）

### 第零層 — 外部依賴

```
┌────────────────────┐  ┌────────────────────┐  ┌────────────────────┐
│  fastembed           │  │  LanceDB            │  │  tokenizers          │
│  (ONNX 推理引擎)      │  │  (嵌入式向量資料庫)    │  │  (HuggingFace)       │
│                      │  │                      │  │                      │
│  模型：paraphrase-    │  │  Lance 列式儲存       │  │  Token 級文字切割      │
│  multilingual-MiniLM │  │  cosine 向量搜尋      │  │  (400 token/chunk    │
│  -L12-v2             │  │  merge_insert 原子    │  │   + 50 token 重疊)   │
│  384 維、~120MB ONNX  │  │  寫入（upsert）       │  │                      │
└────────────────────┘  └────────────────────┘  └────────────────────┘
```

### 第一層 — 核心模組

```
┌───────────────────────────────────────────────────────────┐
│                       config.py — 全域設定                  │
│                                                           │
│  伺服器：127.0.0.1:3141                                    │
│  資料目錄：~/.patrick/data                                  │
│  向量維度：384                                              │
│  分段：400 token/chunk，50 token 重疊                       │
│  批次：最多 16 筆或等 2 秒                                   │
│  搜尋：粗篩 top-5 session，細撈 top-10 chunk               │
└───────────────────────────────────────────────────────────┘
          │                    │                    │
          v                    v                    v
┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
│  embedding.py     │  │  storage.py       │  │  observer.py      │
│  嵌入層（Singleton）│  │  儲存層（Singleton）│  │  觀察者（背景工作） │
│                   │  │                   │  │                   │
│  • 模型載入        │  │  • 兩張 LanceDB   │  │  • asyncio.Queue  │
│  • 同步/非同步嵌入  │  │    表：sessions    │  │  • 批次消費者      │
│  • Token 感知切割   │  │    與 turn_chunks  │  │  • HTTP 接收端     │
│  • SHA-256 雜湊    │  │  • Centroid 計算   │  │  • Centroid 觸發   │
└──────────────────┘  └──────────────────┘  └──────────────────┘
          \                    │                    /
           \                   │                   /
            v                  v                  v
          ┌───────────────────────────────────────┐
          │           tools.py — MCP 工具層          │
          │                                        │
          │  memory_save         — 手動存入          │
          │  memory_search       — 快速語義搜尋      │
          │  memory_deep_search  — 兩層深度搜尋      │
          │  memory_sessions     — 列出所有 session  │
          └───────────────────────────────────────┘
                            │
                            v
          ┌───────────────────────────────────────┐
          │          server.py — 應用進入點           │
          │                                        │
          │  FastMCP（SSE transport）               │
          │  POST /observe（Hook 接收端）            │
          │  uvicorn 監聽 localhost:3141            │
          └───────────────────────────────────────┘
```

### 第二層 — 外部整合

```
┌───────────────────────────────────────┐
│        Claude Code Hooks               │
│                                        │
│  hooks/session_start.py  — 建立 session │
│  hooks/prompt_submit.py  — 捕捉用戶輸入 │
│  hooks/post_tool_use.py  — 捕捉工具結果 │
│  hooks/stop.py           — session 結束 │
│                                        │
│  全部 Python stdlib，發射即忘            │
└───────────────────────────────────────┘
```

---

## 2. 資料庫 Schema

### session_summaries（Session 摘要表）

```
┌──────────────┬───────────────────┬───────────────────────────────────┐
│ 欄位          │ 型別               │ 說明                               │
├──────────────┼───────────────────┼───────────────────────────────────┤
│ session_id   │ string (UUID)      │ 主鍵，merge_insert 的 ON 欄位      │
│ summary_text │ string             │ 自動生成：centroid 最近 top-3 拼接  │
│ hint         │ string (nullable)  │ Agent 手寫的主題描述（可選）         │
│ vector       │ float32[384]       │ 搜尋錨點：hint 向量 或 centroid 向量│
│ created_at   │ string (ISO 8601)  │ 首次建立時間（不更新）              │
│ updated_at   │ string (ISO 8601)  │ 每次 upsert 時更新                │
└──────────────┴───────────────────┴───────────────────────────────────┘
```

### turn_chunks（對話片段表）

```
┌──────────────┬───────────────────┬───────────────────────────────────┐
│ 欄位          │ 型別               │ 說明                               │
├──────────────┼───────────────────┼───────────────────────────────────┤
│ chunk_id     │ string (UUID)      │ 每個 chunk 的唯一識別              │
│ session_id   │ string (UUID)      │ 所屬 session                      │
│ turn_id      │ string (UUID)      │ 同一輪對話的所有 chunk 共用此 ID    │
│ chunk_index  │ int32              │ 在該輪中的順序（0-based）           │
│ total_chunks │ int32              │ 該輪總共切了幾段                   │
│ role         │ string             │ "user" 或 "assistant"             │
│ text         │ string             │ 實際文字內容                       │
│ vector       │ float32[384]       │ 嵌入向量                          │
│ source       │ string             │ "hook"（自動）或 "manual"（手動）  │
│ text_hash    │ string             │ SHA-256，用於精確去重               │
│ source_file  │ string (nullable)  │ 來源檔案路徑（如適用）              │
│ created_at   │ string (ISO 8601)  │ 寫入時間                          │
└──────────────┴───────────────────┴───────────────────────────────────┘
```

---

## 3. 兩條寫入路徑

Patrick 有兩條互補的資料寫入路徑：

```
┌──────────────────────────────────────────────────────────────────┐
│                                                                  │
│   路徑 A：Hook 自動捕捉（主要路徑）                                │
│   ═══════════════════════════════                                │
│   • 每句對話、每次工具呼叫都自動存入                                │
│   • 零 token 消耗                                                │
│   • source = "hook"                                              │
│   • session_summaries 由 centroid 自動維護                        │
│                                                                  │
│   路徑 B：memory_save 手動存入（補充路徑）                          │
│   ═══════════════════════════════════                            │
│   • Claude 主動呼叫，每 session 約 0-2 次                         │
│   • 可帶 summary（hint），語義精度高於 centroid                    │
│   • source = "manual"                                            │
│   • hint vector 優先級永遠高於 centroid vector                    │
│                                                                  │
│   ┌─────────────────────────────────────────────────┐            │
│   │          邊界規則：hint 不被 hook 覆蓋            │            │
│   │                                                  │            │
│   │  Agent 填的 hint → 永遠保留                       │            │
│   │  Centroid → 只在沒有 hint 時暫代搜尋錨點           │            │
│   │  summary_text → 兩條路徑都會更新（centroid top-3） │            │
│   └─────────────────────────────────────────────────┘            │
└──────────────────────────────────────────────────────────────────┘
```

---

## 4. 運作流程

### 4.1 Hook 寫入流程（每句對話自動觸發）

```
 Claude Code 對話中
 ══════════════════

 ┌─────────────┐    stdin JSON     ┌────────────────────┐
 │ Claude Code  │ ──────────────→ │  Hook 腳本           │
 │ (hooks.json) │                  │  (Python stdlib)    │
 └─────────────┘                  │                     │
                                   │  擷取：              │
                                   │  • session_id       │
                                   │  • 對話文字/工具資料  │
                                   │  • 角色 (user/asst)  │
                                   └────────┬───────────┘
                                            │
                                  HTTP POST │  發射即忘
                                  逾時 3 秒  │  失敗靜默
                                            v
 ┌──────────────────────────────────────────────────────────────┐
 │                  Patrick Server (:3141)                       │
 │                                                              │
 │  POST /observe                                               │
 │  ┌──────────────────┐                                        │
 │  │ observe_handler() │                                       │
 │  │  ① 驗證 JSON 格式 │                                       │
 │  │  ② 驗證 UUID 格式 │                                       │
 │  │  ③ 放入 Queue     │                                       │
 │  │  ④ 回傳 202      │                                        │
 │  └──────────────────┘                                        │
 │           │                                                  │
 │           │ asyncio.Queue                                    │
 │           v                                                  │
 │  ┌──────────────────────────────────────────────────┐        │
 │  │ _batch_worker()（背景常駐，無限迴圈）               │        │
 │  │                                                   │        │
 │  │  收集最多 16 筆 或 等待 2 秒，先到先觸發             │        │
 │  │                                                   │        │
 │  │  對每筆資料：                                      │        │
 │  │  ┌─────────────────────────────────────────────┐  │        │
 │  │  │ _process_item()                              │  │        │
 │  │  │                                              │  │        │
 │  │  │  如果是 stop hook：                           │  │        │
 │  │  │    → 跑 compute_and_upsert_centroid()        │  │        │
 │  │  │    → 直接 return（stop 沒有文字內容）          │  │        │
 │  │  │                                              │  │        │
 │  │  │  ① chunk_text(text)                          │  │        │
 │  │  │    → 切成 [chunk1, chunk2, ...]              │  │        │
 │  │  │                                              │  │        │
 │  │  │  ② embed_async(chunks)                       │  │        │
 │  │  │    → 用 run_in_executor 丟到 thread pool     │  │        │
 │  │  │    → 不阻塞 event loop                       │  │        │
 │  │  │    → 取回 [vec1, vec2, ...]                  │  │        │
 │  │  │                                              │  │        │
 │  │  │  ③ make_chunk_records()                      │  │        │
 │  │  │    → 算 SHA-256 去重                          │  │        │
 │  │  │    → 組裝 records                             │  │        │
 │  │  │                                              │  │        │
 │  │  │  ④ add_chunks(records)                       │  │        │
 │  │  │    → append 到 turn_chunks 表                │  │        │
 │  │  └─────────────────────────────────────────────┘  │        │
 │  │                                                   │        │
 │  │  批次處理完畢後：                                   │        │
 │  │  對本批涉及的每個 session_id：                      │        │
 │  │    compute_and_upsert_centroid(session_id)        │        │
 │  │      → numpy.mean(所有 chunk 向量) = centroid     │        │
 │  │      → 找最近 top-3 chunk 拼成 summary_text       │        │
 │  │      → upsert 到 session_summaries               │        │
 │  └──────────────────────────────────────────────────┘        │
 └──────────────────────────────────────────────────────────────┘
```

### 4.2 memory_save 手動寫入流程

```
 Claude 呼叫 MCP 工具
 ═════════════════════

 memory_save(text="重要結論...", session_id="xxx", summary="專案架構決策")
       │
       v
 ┌──────────────────────────────────────────────┐
 │  tools.py :: memory_save()                    │
 │                                               │
 │  ① chunk_text(text)    → 切段                 │
 │  ② embed_async(chunks) → 取得向量              │
 │  ③ make_chunk_records()→ SHA-256 去重          │
 │  ④ add_chunks(records) → 寫入 turn_chunks     │
 │                                               │
 │  如果有帶 summary（hint）：                     │
 │    ⑤ embed_async([summary]) → hint 向量        │
 │    ⑥ compute_and_upsert_centroid(             │
 │         session_id,                           │
 │         hint="專案架構決策",     ← Agent 手寫的  │
 │         hint_vector=hint_vec   ← 語義更精準    │
 │       )                                       │
 │                                               │
 │  沒有帶 summary：                               │
 │    ⑤ compute_and_upsert_centroid(session_id)  │
 │       → 純 centroid 暫代                       │
 └──────────────────────────────────────────────┘
```

### 4.3 Centroid 決策樹（session_summaries 更新邏輯）

```
 compute_and_upsert_centroid(session_id, hint?, hint_vector?)
 ═══════════════════════════════════════════════════════════

 ① 撈該 session 所有 chunk 向量
 ② centroid = numpy.mean(所有向量)
 ③ 用 cosine similarity 排序所有 chunk
 ④ summary_text = 最近 top-3 chunk 用 " | " 拼接

 接下來看三個條件分支：

 ┌───────────────────────────────────────────────────────┐
 │                                                       │
 │  有傳入 hint + hint_vector？                           │
 │  │                                                    │
 │  ├─ YES ──→ 寫入 session_summaries：                  │
 │  │            vector = hint_vector  ← 最高優先級       │
 │  │            hint = "Agent 寫的主題"                  │
 │  │            summary_text = centroid top-3            │
 │  │                                                    │
 │  └─ NO ──→ 這個 session 之前有 hint 嗎？              │
 │              │                                        │
 │              ├─ YES ──→ 只更新 summary_text            │
 │              │          hint 和 hint_vector 完全不動    │
 │              │          （Agent 的判斷不被機器覆蓋）     │
 │              │                                        │
 │              └─ NO ──→ 寫入 session_summaries：        │
 │                          vector = centroid_vector      │
 │                          summary_text = centroid top-3 │
 │                          （centroid 暫代搜尋錨點）      │
 └───────────────────────────────────────────────────────┘

 向量優先級：hint_vector > 既有 hint > centroid_vector
```

### 4.4 memory_search 快速搜尋

```
 memory_search(query="某個問題", top_k=10)
 ═════════════════════════════════════════

 ┌──────────┐    嵌入     ┌──────────────┐
 │  查詢文字  │ ────────→ │  查詢向量      │
 └──────────┘            └───────┬───────┘
                                 │
                                 v
                     ┌──────────────────────┐
                     │  turn_chunks 表       │
                     │  cosine 向量搜尋      │
                     │  取 top-10            │
                     └──────────┬───────────┘
                                │
                                v
                     ┌──────────────────────┐
                     │  格式化結果            │
                     │  附帶相似度分數        │
                     └──────────────────────┘

 特點：跳過 session 粗篩，直接查全部 chunk，延遲低
```

### 4.5 memory_deep_search 兩層深度搜尋

```
 memory_deep_search(query="某個問題", top_k=10)
 ══════════════════════════════════════════════

 ┌──────────┐    嵌入     ┌──────────────┐
 │  查詢文字  │ ────────→ │  查詢向量      │
 └──────────┘            └───────┬───────┘
                                 │
             ╔═══════════════════╧════════════════════╗
             ║     第一層 — Session 粗篩                ║
             ║                                        ║
             ║  在 session_summaries 表做 cosine 搜尋   ║
             ║  取 top-5 session                      ║
             ║  過濾：cosine_sim > 0.3 才保留           ║
             ║                                        ║
             ║  結果：[session_A, session_B, ...]       ║
             ╚═══════════════════╤════════════════════╝
                                 │
                      ┌──────────┴──────────┐
                      │                      │
                  >= 3 個 session        < 3 個 session
                   （足夠精準）            （不夠，要保底）
                      │                      │
                      v                      v
             ╔════════════════╗     ╔══════════════════╗
             ║  第二層          ║     ║  降級保護          ║
             ║  限定範圍搜尋    ║     ║  全域搜尋          ║
             ║                 ║     ║                   ║
             ║  在 turn_chunks ║     ║  在 turn_chunks   ║
             ║  WHERE          ║     ║  不加 session 過濾 ║
             ║  session_id     ║     ║                   ║
             ║  IN [A, B, ...] ║     ║  確保沒有 summary  ║
             ║                 ║     ║  的 session 也能   ║
             ║                 ║     ║  被找到             ║
             ╚════════╤════════╝     ╚═════════╤════════╝
                      │                        │
                      └───────────┬────────────┘
                                  │
                                  v
             ╔════════════════════════════════════════╗
             ║          上下文擴展                      ║
             ║                                        ║
             ║  對每個命中的 chunk：                     ║
             ║    用 turn_id 撈同一輪的所有 chunk        ║
             ║    → 按 chunk_index 排序                ║
             ║    → 拼回完整語義                        ║
             ║                                        ║
             ║  （避免只召回半句話沒上下文）               ║
             ╚═══════════════════╤════════════════════╝
                                 │
                                 v
             ┌─────────────────────────────────────┐
             │  回傳結果：                           │
             │  • text（擴展後的完整文字）            │
             │  • session_id                        │
             │  • role（user / assistant）           │
             │  • score（相似度分數）                 │
             │  • source（"hook" 或 "manual"）       │
             │  • retrieved_from_sessions（來源列表）│
             └─────────────────────────────────────┘
```

---

## 5. Session 完整生命週期

```
 ┌─ Session 開始 ──────────────────────────────── Session 結束 ─┐
 │                                                              │
 │  session_start hook 觸發                    stop hook 觸發    │
 │  → POST /observe                            → POST /observe  │
 │  → 注入 PATRICK_SESSION_ID                   → 最終 centroid  │
 │    到 Claude 的對話上下文                       更新            │
 │                                                              │
 │  ┌─── 對話迴圈（重複 N 次）──────────────────────────┐        │
 │  │                                                   │        │
 │  │  用戶打字 ──→ prompt_submit hook                   │        │
 │  │              → POST {text, role:"user"}           │        │
 │  │              → queue → 批次嵌入 → turn_chunks     │        │
 │  │                                                   │        │
 │  │  Claude 回應（可能使用工具）                        │        │
 │  │    │                                              │        │
 │  │    ├─ post_tool_use hook（每個工具觸發一次）        │        │
 │  │    │  → 格式化成自然語言                           │        │
 │  │    │  → POST {text, role:"assistant"}             │        │
 │  │    │  → queue → 批次嵌入 → turn_chunks            │        │
 │  │    │                                              │        │
 │  │    └─ Claude 可能呼叫 memory_save（罕見，0-2 次）  │        │
 │  │       → 嵌入 text + hint                          │        │
 │  │       → turn_chunks + session_summaries           │        │
 │  │                                                   │        │
 │  │  每批處理完後自動觸發 centroid 更新：               │        │
 │  │    → numpy.mean(所有向量) → top-3 → summary_text  │        │
 │  │    → upsert session_summaries                    │        │
 │  │                                                   │        │
 │  └───────────────────────────────────────────────────┘        │
 │                                                              │
 └──────────────────────────────────────────────────────────────┘

 ===== 跨 Session 記憶召回 =====

 下一個 Session 開始：
   Claude 呼叫 memory_deep_search("上次我們討論了什麼？")
     → 第一層：在 session_summaries 找最相關的 session
     → 第二層：在那些 session 的 turn_chunks 中精確搜尋
     → 上下文擴展：把碎片拼回完整段落
     → 回傳給 Claude，Claude 就「記得」上次的對話了
```

---

## 6. 模組功能一覽

### config.py — 全域常數

| 常數 | 值 | 用途 |
|------|-----|------|
| `HOST` | `127.0.0.1` | 伺服器綁定地址 |
| `PORT` | `3141` | 伺服器埠號 |
| `DATA_DIR` | `~/.patrick/data` | LanceDB 資料目錄 |
| `EMBEDDING_MODEL` | `paraphrase-multilingual-MiniLM-L12-v2` | 384 維 ONNX 嵌入模型 |
| `VECTOR_DIM` | `384` | 向量維度 |
| `CHUNK_SIZE` | `400` | 每段 token 數 |
| `CHUNK_OVERLAP` | `50` | 段間重疊 token 數 |
| `BATCH_SIZE` | `16` | 批次最大筆數 |
| `BATCH_TIMEOUT` | `2.0` | 批次最大等待秒數 |
| `TOP_K_SESSIONS` | `5` | 粗篩取 session 數量 |
| `TOP_K_CHUNKS` | `10` | 細撈取 chunk 數量 |
| `MIN_SESSION_SCORE` | `0.3` | Session 最低 cosine 相似度門檻 |

### embedding.py — 嵌入層

| 方法 | 同步/非同步 | 說明 |
|------|------------|------|
| `initialize()` | 同步 | 載入 fastembed 模型 + HuggingFace tokenizer（只跑一次） |
| `embed_sync(texts)` | 同步 | 阻塞式嵌入（在 thread pool 中執行） |
| `embed_async(texts)` | 非同步 | 透過 `run_in_executor` 包裝，不阻塞 event loop |
| `chunk_text(text)` | 同步 | Token 感知切割（400 token + 50 重疊） |
| `text_hash(text)` | 靜態 | SHA-256 雜湊，用於精確去重 |

### storage.py — 儲存層

| 方法 | 說明 |
|------|------|
| `initialize()` | 建立/開啟 LanceDB 表，執行 hint 欄位遷移 |
| `upsert_session_summary(...)` | Merge-insert 寫入 session_summaries |
| `get_session(session_id)` | 取得單一 session 記錄 |
| `get_session_chunks(session_id)` | 取得該 session 所有 chunk（全表掃描） |
| `update_summary_text_only(...)` | 只更新 summary_text，保留 hint 和向量 |
| `search_sessions(query_vector, top_k)` | Session 摘要表的 cosine 搜尋 |
| `list_sessions()` | 列出所有 session |
| `hash_exists(text_hash)` | 檢查 SHA-256 是否已存在（去重用） |
| `add_chunks(records)` | Append-only 寫入 turn_chunks |
| `search_chunks(query_vector, top_k, session_ids?)` | Chunk 表的 cosine 搜尋，可限定 session 範圍 |
| `get_turn_chunks(turn_id)` | 取得同一輪的所有 chunk（上下文擴展用） |
| `make_chunk_records(...)` | 組裝 chunk 記錄，含去重檢查 |
| `compute_and_upsert_centroid(...)` | 純 numpy centroid 計算 + upsert |

### observer.py — Hook 事件處理

| 函式 | 說明 |
|------|------|
| `observe_handler(request)` | POST /observe → 驗證 → 放入 Queue → 回傳 202 |
| `_process_item(item)` | 切段 → 嵌入 → 去重 → 寫入單筆事件 |
| `_batch_worker()` | 背景迴圈：批次收集 → 處理 → 觸發 centroid 更新 |
| `start_worker()` | 在 event loop 中建立背景任務 |

### tools.py — MCP 工具

| 工具 | 說明 |
|------|------|
| `memory_save(text, session_id?, role?, summary?, source_file?)` | 手動存入記憶，可帶 hint |
| `memory_search(query, top_k?)` | 快速語義搜尋，直接查 turn_chunks |
| `memory_deep_search(query, top_k?)` | 兩層搜尋 + 上下文擴展 |
| `memory_sessions()` | 列出所有 session 及其摘要 |

---

## 7. Hook 腳本細節

```
hooks/
├── session_start.py   — 從 stdin 取 session_id，POST 到 /observe，
│                        回傳 PATRICK_SESSION_ID 注入對話上下文
├── prompt_submit.py   — 捕捉用戶輸入，POST text + role="user"
├── post_tool_use.py   — 把工具結果格式化成自然語言，POST role="assistant"
└── stop.py            — 通知 session 結束，觸發最終 centroid 更新
```

**工具格式化規則**（`post_tool_use.py`）：

| 工具 | 格式化方式 |
|------|-----------|
| Bash | `執行了指令：{cmd}\n結果：{output[:500]}` |
| Read | `讀取了檔案：{path}` |
| Write | `寫入了檔案：{path}` |
| Edit/MultiEdit | `修改了檔案：{path}，舊：…，新：…` |
| Glob/Grep | `搜尋 {pattern} {path}` |
| WebFetch/WebSearch | `{query}` |
| 其他 | `使用了工具 {name}，輸入：{input[:200]}` |

**設計原則**：只存語義關鍵欄位，不存 raw JSON，避免格式標記污染嵌入向量。

所有腳本：Python stdlib（`urllib.request`），3 秒逾時，失敗靜默不影響 Claude。

---

## 8. 設計原則

| 原則 | 實作方式 |
|------|---------|
| **零 LLM token** | Centroid 是純 numpy 數學運算；不呼叫任何 LLM API 做摘要 |
| **Agent hint 優先於 centroid** | hint_vector 寫入後不被自動 centroid 覆蓋；人的語義判斷優於機器均值 |
| **Hook 不等嵌入** | Queue + 202 Accepted；批次 worker 非同步處理 |
| **嵌入不阻塞 event loop** | `run_in_executor` 將 CPU 密集運算丟到 thread pool |
| **精確去重** | 每次寫入前計算 SHA-256，已存在則跳過 |
| **優雅降級** | 粗篩結果不足（< 3 session）時自動 fallback 到全域搜尋 |
| **上下文擴展** | turn_id 把同一輪的 chunk 分組；查詢時自動拼回完整段落 |
| **發射即忘的 Hook** | 3 秒逾時，失敗靜默，永遠不阻塞 Claude |
| **Singleton 資源** | 模型只載一次，DB 連線只建一次，不重複初始化 |
| **兩條路徑互補** | Hook = 自動捕捉一切；memory_save = Agent 精選策展 |

---

## 9. 核心約束（不可打破）

1. **零 LLM token 消耗於記憶管理** — 所有 summary 生成靠數學，不靠 API
2. **無 Docker、無外部 API、無 API key** — `pip install` 即可啟動
3. **純本地運行** — 所有資料存在 `~/.patrick/data`，不離開本機
4. **Hook 失敗不影響 Claude** — 記憶系統掛了，Claude 照常工作
