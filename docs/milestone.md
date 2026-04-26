# Patrick — Milestone 計劃

> 零 token、本地跑、傻瓜式 Chat Memory MCP Server

---

## Phase 1 — MVP 跑起來

**目標**：`pip install` 一個包，設定 claude config + hooks.json，記憶能存、能查，不花一個 token。

**包含**：
- MCP HTTP server 骨架（Python `mcp` SDK，SSE transport）— 同一個 HTTP server 同時提供 MCP endpoint 和 `/observe` hook endpoint，一個 port 搞定
- 4 個 tool：`memory_save` / `memory_search` / `memory_sessions` / `memory_deep_search`
- Hook 驅動的全自動存入（參考 agentmemory 的 hook 模式）：
  - Claude Code `hooks.json` 宣告 → 觸發輕量腳本 → HTTP POST 到 Patrick server
  - 4 個 hook 腳本：`session-start`（建立 session_id）/ `prompt-submit`（用戶輸入）/ `post-tool-use`（工具結果）/ `stop`（session 結束）
  - Patrick server 的 `/observe` endpoint 接收 hook POST，非同步 queue + 批次 embedding 處理（不阻塞 HTTP response）
  - `memory_save` tool 保留給 Claude 主動存 summary 等場景，hook 負責每句對話自動存——兩者互補
- 預設 `multilingual-e5-small`（~120MB ONNX via fastembed），config 可切換 `bge-m3`
- Embedding 層抽象：`EmbeddingProvider` 類，model name + chunk size + overlap 從 config 讀取，切換模型改一行字串
- Chunk splitting：長回覆（超過模型 token limit）自動分段 + overlap，token-aware 切割（使用 `tokenizers` library，非字元數近似）
- Chunk 歸屬追蹤：metadata 存 `turn_id` + `chunk_index` + `total_chunks` + `text_hash`（SHA256），查詢時 context expansion 自動拼回完整語義；寫入前檢查 hash 已存在則跳過，防止重複記憶堆積
- `patrick init` CLI 指令：安裝後執行，預下載模型 + sanity check（跑一次 dummy embedding 確認環境正確）
- 向量儲存：LanceDB（embedded 模式，單一資料夾，無 server）
- 兩層索引（graceful degradation）：
  - Layer 1（粗篩）：Session Summary embedding → 快速定位相關 session
  - Layer 2（細撈）：Turn-level chunk embedding → 精確找到具體語句
  - Layer 1 結果不足時自動 fallback 到 Layer 2 全域搜尋，確保無 summary 的 session 也能被召回
- Session Summary 來源：`memory_save` tool schema 的 optional `summary` 參數，由 Claude 呼叫時自帶；server 端不自行生成，符合零 token 約束。同 session 多次存入時，後來的 summary 覆蓋前面的（透過 LanceDB `merge_insert` on `session_id`）
- `session_id` 來源優先順序：(1) hook stdin JSON 的 `session_id` 欄位（snake_case，官方文件 + spike 雙重驗證 ✅）(2) fallback `uuid4()`。無 `CLAUDE_SESSION_ID` 環境變數（官方文件未列出，實測確認）
- 支援一般聊天 + 文件讀取場景（`source_file` metadata）

**成功標準**：Hook 自動存入每句對話，`memory_save` 補充 summary，下個 session 查得到。

---

## Phase 2 — 查得更準

**目標**：語義搜尋之外加關鍵字路徑，解決 exact match 問題（如「v2.3.1 那次討論」）。

**包含**：
- BM25（jieba 分詞）+ dense embedding hybrid search
- regex entity hint（引號 / URL / 版本號 / 路徑 加分）
- MinHash 去重，防止重複記憶堆積
- 並發寫入安全性（file lock 或 LanceDB server 模式）
- LanceDB compaction 策略（`patrick compact` CLI 或自動觸發）

---

## Phase 3 — 用得更順

**目標**：使用者完全感知不到 Patrick，自動運作。

**包含**：
- 記憶過期 / 淘汰策略
- 文件場景完整支援：`source_file` 查詢 API、TextRank 可選長文件壓縮

---

## Phase 4 — 發布 & 傻瓜式體驗

**目標**：讓別人能零痛苦裝上 Patrick。

**包含**：
- PyPI 打包發布
- claude config 一鍵生成腳本
- README + 文件
- 選擇性進階功能：ColBERT late interaction、LLM fact extraction（重要對話才花 token）

---

## 技術選型（已拍板）

| 元件 | 選擇 |
|------|------|
| 協議 | MCP HTTP（Python `mcp` SDK，SSE transport） |
| Embedding 模型 | 預設 `multilingual-e5-small`（~120MB ONNX），可切換 `bge-m3`（570MB） |
| Embedding 框架 | `fastembed` |
| 向量儲存 | LanceDB（embedded 模式） |
| 分詞（Phase 2） | `jieba` |
| Token 計算 | `tokenizers`（HuggingFace，用於 chunk splitting） |
| HTTP server | FastMCP + uvicorn（MCP SSE + `/observe` endpoint 共用同一個 HTTP server，監聽 localhost:3141） |
| Hook 腳本 | Python（`urllib.request` 標準庫，零額外依賴，不依賴 Node.js） |

## 核心約束（不可破壞）

- 零 LLM token 消耗於記憶管理
- 無 Docker、無外部 API、無 API key
- `pip install fastembed lancedb tokenizers aiohttp`（或同等輕量依賴）即可啟動

---

## 已知問題 / Pending Issues

### Issue #1 — Multi-agent (OpenParty) 的 assistant_text 無法被自動存入

**狀態：** 待解決

**問題描述：**
在 OpenParty 多 agent 對話場景下，`assistant_text` chunks 從未被存入 patrick DB（`user_prompt` 和 `tool_use` chunks 正常存入）。

**根本原因：**
OpenParty bridge.py 使用 `claude_agent_sdk.query()` 以 subprocess 模式逐 turn 呼叫 Claude。在此模式下，Claude Code 的 `Stop` hook **完全不會觸發**（已實測確認：`/tmp/patrick_stop_debug.log` 不存在）。`Stop` hook 是現有設計中負責存入 assistant 回應的唯一入口。

**調查過程：**
- `UserPromptSubmit` hook：✅ 有 fire（`user_prompt` chunks 存在為證）
- `PreToolUse / PostToolUse` hook：✅ 有 fire（`tool_use` chunks 存在為證）
- `Stop` hook：❌ 不 fire（subprocess 模式下 Claude process 直接退出，無「等待下一輸入」狀態）
- `ClaudeSDKClient` programmatic hooks：底層仍是同一 subprocess 機制，效果存疑
- Stop hook payload 中的 `last_assistant_message` 欄位：理論上存在，但 Stop hook 不 fire 故無法利用

**可行解法（按優先順序）：**

1. **方案 A — bridge.py 直接 POST（主要，確定有效）**
   在 `_call_claude()` 的 `elif isinstance(message, ResultMessage):` 區塊內，直接 POST `result_text` 給 patrick `/observe`。bridge.py 已有完整的 `result_text`（由 SDK 組好）和 `self.session_id`，5 行程式碼即可解決。
   - 檔案：`/Users/andy/3rd-party/OpenParty/bridge.py`，約 line 1514

2. **方案 B — prompt_submit.py 讀取上一 turn 的 transcript（兜底）**
   `UserPromptSubmit` 有 fire，且 turn N+1 觸發時 turn N 的 transcript 已寫入。在 `prompt_submit.py` 中加入讀取上一 turn assistant text 的邏輯，作為方案 A 的備援。
   - 缺點：延遲一個 turn；最後一個 turn 無法捕捉（沒有 N+1 觸發）
   - 檔案：`/Users/andy/llm-mem/patrick/src/patrick/hooks/prompt_submit.py`

**patrick 端的 dedup 機制（hash-based）確保兩個方案同時啟用不會產生重複存入。**
