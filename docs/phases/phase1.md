# Phase 1 — MVP 跑起來

> 目標：`pip install` 一個包，設定 claude config + hooks.json，記憶就能存進去、查得出來。

---

## Todo List（有順序依賴）

1. ~~**拍板向量庫**（先決條件，不能跳過）~~ ✅ **已決定：LanceDB**
2. **專案骨架**：`patrick/` 目錄結構，`pyproject.toml`，entry point (`server.py`)
3. **MCP HTTP server 骨架**：
   - **SSE transport**（非 stdio），一個 HTTP server 同時提供 MCP endpoint 和 `/observe` hook endpoint，監聽 localhost port（預設 `3141`）
   - 4 個 tool schema 定義（先空殼，確認 MCP 能啟動）
   - 共用同一個 `EmbeddingProvider` singleton 和 LanceDB 連線，不重複載入
   - 啟動時加 `SO_REUSEADDR`，port 被佔用時給明確錯誤訊息（避免上次沒正常關 / 開了兩個 instance）
4. **Embedding 層**：
   - `EmbeddingProvider` 類封裝，預設載 `multilingual-e5-small`，config 可切換 `bge-m3`
   - model name / chunk_size / overlap 從 config 常數讀取，切換模型只改一行
   - singleton 模式（只載一次）
   - Chunk splitting：長文本超過模型 token limit 時自動分段 + overlap，不丟語義
   - **Token-aware 切割**：使用 `tokenizers` library 載入 `intfloat/multilingual-e5-small` 對應的 tokenizer 計算 token 數，不用 `len(text)` 字元數近似（中文一字 = 1-3 token，混合場景下字元數不可靠）。注意：fastembed 沒有 public tokenizer API，需額外 `pip install tokenizers` 獨立載入
5. **向量庫 CRUD**：
   - `session_summaries`：使用 LanceDB `merge_insert("session_id").when_matched_update_all().when_not_matched_insert_all()` 實現 upsert（同 session 後來的 summary 覆蓋前面的）
   - `turn_chunks`：純 `table.add()` append-only（不需要 upsert，dedup 靠 SHA256 hash 檢查）
   - `query(vector, filter)` 封裝
6. **兩層索引邏輯**：
   - `session_summaries` collection（粗篩層）
   - `turn_chunks` collection（細撈層）
   - 查詢：先取 top-N session_ids → 再 WHERE session_id IN [...] 細撈
   - **Context expansion**：查詢命中某 chunk 後，用 `turn_id` 撈同組所有 chunks（`ORDER BY chunk_index`）拼回完整語義。Phase 1 必做，否則召回半句話無上下文
7. **4 個 tool 實作**：
   - `memory_save` — 接 text / session_id / role / **summary（optional）**，embed + 存入對應 collection，自動標記 `source: "manual"`
     - `session_id`：來源優先順序 (1) hook stdin JSON 的 `session_id` 欄位（snake_case，官方文件確認為所有 hook payload 的 common field）(2) fallback `uuid4()`。無 `CLAUDE_SESSION_ID` 環境變數（官方文件未列出，spike 實測確認 NOT SET；社群 issue #25642 仍 open）。`session-start` hook 取得 session_id 後：① POST 到 `/observe`；② 回傳結構化 `additionalContext` JSON（`{"hookSpecificOutput": {"additionalContext": "PATRICK_SESSION_ID=xxx"}}`），Claude Code 把它注入對話 context，使 Claude 在同一 session 後續呼叫 `memory_save` 時能帶正確的 session_id。**已驗證：spike_session_id.py PASS ✅（真實 hook 觸發，session_id UUID 值確認存在於 stdin）**
     - `turn_id`：由 server 端自動生成（`uuid4()`），Claude 不需帶入。同一次 `memory_save` 呼叫產生的所有 chunks 共用同一個 `turn_id`，供 context expansion 查詢時拼回完整語義
     - `summary`：由 Claude 呼叫時自帶（一句話描述這段記憶的主題），server 端不自行生成，符合零 token 約束
     - **更新策略：最後寫入覆蓋**（對話越到後面 summary 越精準，不用鎖定第一次的）
     - 若帶有 summary，同時透過 `merge_insert` upsert 到 `session_summaries` collection（粗篩層）
   - `memory_search` — flat 語義搜尋（跳過 Layer 1 粗篩，直接查 `turn_chunks`），返回 top-K。適合「快速查單句」場景，延遲低。tool description 寫明：「快速語義搜尋，直接從所有 turn chunks 撈，無 session 脈絡聚合」
   - `memory_deep_search` — 兩層查詢，返回帶 session 脈絡的 top-K。適合「需要完整上下文 / 跨 session 聚合」場景。tool description 寫明：「深度搜尋，先用 session summary 粗篩定位相關 session，再細撈 turn chunks，結果帶 session 脈絡」
     - **Graceful degradation**：Layer 1 粗篩結果不足（< 3 個 session 或 score 都很低）時，自動 fallback 到 Layer 2 全域搜尋，確保無 summary 的 session 也能被召回
     - 兩者共用底層查詢邏輯，維護成本低
   - `memory_sessions` — 列出所有 session，回傳 `[{session_id, created_at, summary_text}]`（summary_text 若無則回傳 null），讓使用者看得懂每個 session 是什麼
8. **存入方式：Hook 全自動 + `memory_save` 手動補充**
   - **Hook 自動存入（主要路徑）**：Claude Code `hooks.json` 宣告 4 個 hook，觸發輕量 Python 腳本 HTTP POST 到 Patrick 的 `/observe` endpoint
     - `session-start`：從 stdin JSON 的 `session_id` 欄位取得 session_id（唯一可靠來源，env var 不存在），POST `{hook: "session-start", session_id}` 建立 session
     - `prompt-submit`：捕捉用戶輸入，POST `{hook: "prompt-submit", text, session_id}`
     - `post-tool-use`：捕捉工具呼叫結果，POST `{hook: "post-tool-use", tool_name, input, output, session_id}`（截斷 >8KB 的 output）
     - `stop`：session 結束信號，可觸發 session 標記完成
   - Hook 腳本全部 Python（`urllib.request` stdlib，~30-50 行/腳本），讀 stdin JSON → fire-and-forget POST，timeout 3 秒失敗靜默
   - `/observe` endpoint 收到 POST 後先回 202，將資料丟進 `asyncio.Queue`；背景 worker 每 N 條或每 T 秒（先到先觸發）批次 `embed(List[str])` + 批次寫入 LanceDB，壓縮 IO 次數和 embedding 開銷
   - **⚠️ 實作注意（blocking embedding）**：background worker 雖然是 `async def`，但 `fastembed.embed()` 是 CPU-bound 同步操作，直接呼叫會 block 整個 event loop（包括新的 `/observe` POST 進不來）。必須用 thread pool offload：
     ```python
     from concurrent.futures import ThreadPoolExecutor
     executor = ThreadPoolExecutor(max_workers=1)  # server 啟動時建立，全域共用
     
     async def background_worker():
         loop = asyncio.get_event_loop()
         batch = await queue.get()
         # 正確：offload 到 thread pool，event loop 不被卡住
         vectors = await loop.run_in_executor(executor, embedder.embed, batch)
         await table.add(...)
     ```
     `asyncio.Queue` 解決了「HTTP handler 不等 embedding」，但 background worker 裡的 embed call 還是要 `run_in_executor`，否則 worker 跑 inference 時其他請求仍然進不來。
   - **Exact text dedup（SHA256）**：寫入前計算 `hashlib.sha256(text.encode()).hexdigest()`，查詢 `turn_chunks` 是否已存在相同 `text_hash`，已存在則跳過。防止重複 hook 觸發塞入重複記憶，省掉多餘 embedding 計算。Phase 2 的 MinHash 近似去重是進階版，Phase 1 先用 exact match 擋住最明顯的重複
   - **`memory_save` tool（補充路徑）**：保留給 Claude 主動存 session summary、重要筆記等場景
     - **Tool description 設計原則（高門檻策略）**：description 明確寫「只在以下情況呼叫：(1) 用戶明確要求記住某件事 (2) 本次對話做了重大決定或結論 (3) session 結束時提供 summary」。不寫「請隨時記住重要內容」之類的寬泛引導，避免 Claude 每個 turn 都呼叫導致 token 浪費
     - 預期頻率：每個 session 0-2 次，不是每個 turn
     - 透過 `memory_save` 存入的記憶自動標記 `source: "manual"`，hook 存入的標記 `source: "hook"`
   - **兩者互補**：hook 確保每句對話不漏存（零額外 token），`memory_save` 提供高價值的結構化 summary 供粗篩層使用（每次呼叫約 200-400 token 來回）
   - **淘汰策略依據**：`source: "manual"` 的記憶天然比 `source: "hook"` 更重要（Claude 特意存的 vs 自動掃進來的），後續淘汰 / 排序時優先保留 manual 記憶
9. **`patrick init` CLI 指令**：安裝後執行 `patrick init`，預下載 embedding 模型（直接用 fastembed 預設輸出，不額外 wrap 進度條）+ 跑一次 dummy embedding sanity check，失敗給明確錯誤訊息
10. **Hook 腳本**（全部 Python，零額外依賴，使用 `urllib.request` stdlib）：
    - `hooks/session_start.py`：從 stdin JSON 的 `session_id` 欄位讀取 session_id → POST `/observe`，建立 session（~20 行）
    - `hooks/prompt_submit.py`：讀 stdin JSON（含 session_id）→ POST `/observe`（~30 行）
    - `hooks/post_tool_use.py`：讀 stdin JSON（含 session_id）→ **tool-specific formatter** 轉成自然語言 → POST `/observe`，截斷 >8KB output（~100 行）
      - `format_tool_text()` 依 `tool_name` 分派：Bash → `執行了指令：{cmd}\n結果：{output[:500]}`；Read → `讀取了檔案：{path}`；Write → `寫入了檔案：{path}`；Edit/MultiEdit → `修改了檔案：{path}，舊：…，新：…`；Glob/Grep → pattern + path；WebFetch/WebSearch → query；其他 → `使用了工具 {name}，輸入：{input[:200]}`
      - **設計原則**：不存 raw JSON wrapper，只存語意關鍵欄位，避免格式標記（`[Tool:`、`Input:`）污染 embedding 向量
    - `hooks/stop.py`：讀 stdin JSON → 從 `transcript_path` 讀完整 JSONL transcript → 提取所有 assistant 文字回覆（去重 by `message.id`）→ 逐條 POST `{hook: "stop-text", role: "assistant"}` → 最後 POST `{hook: "stop"}` 觸發 session 完成
      - **實作注意（已 spike 驗證 ✅）**：transcript JSONL 格式為 `entry["message"]["role"]`（巢狀），非 top-level `entry["role"]`。同一 message 可能因 streaming 被拆成多行（thinking/text/tool_use 各一行），以 `message.id` 去重才能正確合並
      - stop hook stdin payload 直接含 `last_assistant_message`（最後一條文字）與 `transcript_path`（完整路徑），兩個欄位均已確認存在
    - session_id 取得方式：從 hook stdin JSON 的 `session_id` 欄位讀取（snake_case），fallback `uuid4()`（無 env var 路徑，官方文件確認不存在）
    - 所有腳本 fire-and-forget，timeout 3 秒，失敗靜默不影響 Claude 運作
11. **claude config 範本**：`claude_config_example.json`（含 MCP server 設定 + hooks.json 設定），讓使用者一分鐘接上

---

## Acceptance Criteria

| # | AC | 驗收標準 |
|---|-----|---------|
| AC-1 | 安裝 | `pip install patrick-memory` + `patrick init`（預下載模型）+ `python server.py` 啟動無報錯 |
| AC-2 | 存入 | 呼叫 `memory_save`（session summary + turns）→ 向量庫有資料 |
| AC-3 | 查詢 | `memory_search("XXX")` 返回相關 chunk，不是空陣列 |
| AC-4 | 兩層索引 | 查詢先命中 session summary 粗篩，再從該 session 撈 turn chunk |
| AC-5 | 中文 | 存中文 turn，用中文 query，能查到（multilingual-e5-small 不能掛） |
| AC-6 | 零 token | 全程不發任何外部 LLM API 請求 |
| AC-7 | 接上 Claude | 修改 claude config 後，Claude 能呼叫這四個 tool |
| AC-8 | 持久化 | 重啟 server 後，之前存入的記憶還在，查得回來 |
| AC-9 | 跨 session | 存入 session A → 新開 session B → `memory_deep_search` 能查到 session A 的內容 |
| AC-10 | 模型只載一次 | embedding model 在 server 啟動時載入，不是每次 tool call 重載 |
| AC-11 | Hook 自動存入 | Hook 腳本觸發後，用戶輸入與工具結果自動寫入 `turn_chunks`，不依賴 Claude 主動呼叫 |
| AC-12 | Hook 靜默失敗 | Hook 腳本 timeout 或 server 未啟動時，不影響 Claude 正常運作（fire-and-forget） |
| AC-13 | 模型可切換 | 改 config 一行字串（e5-small → bge-m3），重啟 server 即生效，不動業務邏輯 |
| AC-14 | Chunk splitting | agent 長回覆（>512 token）正確分段存入（token-aware 切割），查詢時 context expansion 召回完整語義 |
| AC-15 | 模型預下載 | `patrick init` 執行後模型已快取，首次 tool call 不觸發下載等待 |
| AC-16 | HTTP endpoint | Patrick server 為 HTTP MCP server（SSE transport），`curl POST localhost:3141/observe` 能成功寫入記憶 |
| AC-17 | Exact dedup | 同一段 text 透過 hook 重複送入時，只存一筆（SHA256 hash 比對），不重複 embedding |
| AC-18 | session_id 一致性 | hook 存入的 turn 與 `memory_save` 存入的 summary 使用同一個 session_id（來自 hook stdin JSON 的 `session_id` 欄位，無 env var 路徑），兩層索引能正確關聯 |
| AC-19 | Queue 非同步寫入 | `/observe` endpoint 回 202 不阻塞，背景 worker 批次 embedding + 寫入，hook 腳本不會因 embedding 延遲而 timeout |

---

## LanceDB Schema（已拍板）

```
session_summaries:
  session_id    str (primary)
  summary_text  str
  vector        float[384]
  created_at    timestamp
  updated_at    timestamp

turn_chunks:
  chunk_id      str (uuid)
  session_id    str (indexed)
  turn_id       str
  chunk_index   int
  total_chunks  int
  role          str (user/assistant)
  text          str
  vector        float[384]
  source        str ("hook" | "manual")
  text_hash     str (SHA256, indexed, exact dedup 用)
  source_file   str (nullable)
  created_at    timestamp
```

> `total_chunks` 讓查詢時知道要拉幾個相鄰 chunk；`chunk_id` 是 dedup 和 debug 的基礎。`source` 區分自動存入（hook）與手動存入（manual），供淘汰策略和搜尋排序使用。`text_hash` 用於 exact dedup：寫入前計算 `hashlib.sha256(text.encode()).hexdigest()`，若 hash 已存在則跳過，防止同一段文字重複寫入（hook 重試 / Claude 重發場景）。

---

## Phase 1 已知限制（Phase 2 處理）

| 限制 | 說明 | Phase 2 解法 |
|------|------|-------------|
| 無並發寫入保護 | 兩個 Claude 視窗同時寫同一個 LanceDB 資料夾可能出問題 | file lock 或 LanceDB server 模式 |
| 無 compaction | append-heavy workload 久了查詢變慢 | `patrick compact` CLI 或每 500 筆自動 compact |
| fastembed M-series Mac | `onnxruntime` 依賴可能需手動處理 | `patrick init` sanity check 已覆蓋基本場景 |

---

## 向量庫決策（已拍板）

**選擇：LanceDB**

| 理由 | 說明 |
|------|------|
| embedded 模式，無 server | 本地 `.lancedb/` 資料夾，`pip install lancedb` 一行搞定 |
| 無 pydantic 版本衝突 | 依賴更輕，不像 Chroma 有 pydantic v1/v2 地雷 |
| 原生 metadata filter | `where` 條件過濾，兩層查詢直觀 |
| Lance 列式儲存 | 大量資料下效能優於 Chroma，天花板更高 |

**Fallback 路線（訊息量越多越好）**

```
LanceDB（Phase 1 起步，幾千～幾萬條）
  → Qdrant local mode（幾十萬條，單一 binary，不需 Docker）
```

| Fallback | 特點 | 觸發條件 |
|----------|------|---------|
| **Qdrant local** | HNSW 業界最優、原生 filter、需多開一個 process | 訊息量到幾十萬條、效能要求提升 |

> **不在 fallback 清單**：sqlite-vec（線性掃描，訊息越多越慢）、FAISS（無原生 metadata filter）、Chroma（pydantic 依賴衝突風險）

Storage 層封裝好介面後，LanceDB → Qdrant 切換不超過半天。

---

## Embedding 模型決策（已拍板）

**預設：multilingual-e5-small（~120MB ONNX）**

| 理由 | 說明 |
|------|------|
| 體積輕量 | 120MB vs bge-m3 的 570MB，首次下載快 ~5 倍 |
| 冷啟動快 | ~0.5s vs ~3-5s，記憶體佔用 ~300MB vs ~1.2GB |
| 中英文夠用 | chat memory 短句場景召回品質足夠 |
| fastembed 原生支援 | 零額外配置 |

**已知限制與應對**：

| 限制 | 影響 | 應對策略 |
|------|------|---------|
| 512 token context limit | agent 長回覆會被截斷 | Chunk splitting：自動分段 + overlap |
| 多語言混合稍弱 | 3+ 語言混雜時精度下降 | 繁中 + 英文組合 OK，冷門語言配對待觀察 |
| 無原生 sparse vector | Phase 2 hybrid search 無法免費拿 sparse signal | 靠 jieba BM25 外掛補 |

**Fallback 路線**：

```
multilingual-e5-small（Phase 1 起手，輕量優先）
  → bge-m3（召回效果不夠時切換，改 config 一行字串）
```

> 切換方式：修改 `EmbeddingProvider` config 中的 model name，重啟 server 即生效。chunk_size 同步放大（512 → 2048+），不需動業務邏輯。

---

## Phase 1 開工前置作業（Spike）— 已完成 ✅

1. **✅ 驗證 session_id 來源**（`scripts/research/spike_session_id.py` PASS — 真實 hook 觸發驗證）：
   - 官方文件 + 真實 hook 觸發雙重確認：`stdin['session_id']`（snake_case）是唯一可靠來源
   - 真實觸發結果：`session_id = "4da2ce8c-629d-4d26-b73a-8b315f0414ad"`，來源 `stdin_json['session_id']`，`hook_event_name: "PreToolUse"`
   - `sessionId`（camelCase）：NOT FOUND（官方文件正確，camelCase 不存在）
   - `CLAUDE_SESSION_ID` 環境變數：NOT SET（官方文件確認不存在）
   - 正確優先順序：`stdin['session_id']` → `uuid4()` fallback（無 env var 路徑）

2. **✅ 驗證 LanceDB `merge_insert` 行為**（`scripts/research/spike_lancedb_merge_insert.py` 全部 PASS）：
   - `merge_insert("session_id").when_matched_update_all().when_not_matched_insert_all()` 在 embedded 模式正常運作
   - **重要實作注意**：nullable `source_file` 欄位在 pandas 回傳 `float nan`，非 `None`。所有 null check 必須用 `pd.isna(val)`，不能用 `val is None`

---

## 不在 Phase 1 範圍內

- BM25 / jieba 分詞（Phase 2）
- entity hint / regex boost（Phase 2）
- MinHash 去重（Phase 2）
- 並發寫入保護（Phase 2）
- LanceDB compaction 策略（Phase 2）
- tool call 噪音權重調整（Phase 2）：post_tool_use 記錄不是噪音，是低權重信號；Phase 2 依 tool_name / source 做差異化搜尋排序，而非直接過濾
- PyPI 發布（Phase 4）
