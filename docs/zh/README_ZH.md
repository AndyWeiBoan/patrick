<p align="center">
  <img src="https://upload.wikimedia.org/wikipedia/commons/thumb/2/28/Patrick_Star_character.png/250px-Patrick_Star_character.png" alt="派大星" width="180"/>
</p>

<h1 align="center">Patrick</h1>

<p align="center">
  <a href="../../README.md">English</a> &nbsp;|&nbsp; <a href="README_ZH.md"><b>中文</b></a>
</p>

<p align="center">
  <em>零 Token、完全本地的 Claude Code 跨 session 記憶系統。</em>
</p>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.10%2B-blue"/>
  <img alt="License" src="https://img.shields.io/badge/license-MIT-green"/>
  <img alt="MCP" src="https://img.shields.io/badge/MCP-SSE-purple"/>
</p>

---

Claude Code 天生失憶——每個 session 都是全新的開始。你每天重新解釋同樣的背景、重新討論同樣的決策、重新踩過同樣的坑。

Patrick 解決這個問題。它透過 Claude Code hooks 全自動捕捉對話歷史，儲存在本地的嵌入式向量資料庫中，並透過 MCP server 提供快速語義搜尋，讓 Claude 在每個 session 開始時就能取回過去的脈絡。

無雲端。無 LLM 萃取呼叫。無 Token 額外消耗。完全自動。

---

## 為什麼選擇 Patrick

| | Patrick | CLAUDE.md | mem0 / Zep |
|---|---|---|---|
| **Token 消耗** | 零 | 零 | 高（LLM 萃取） |
| **自動化程度** | 全自動 | 手動 | 半自動 |
| **本地 / 私密** | 是 | 是 | 否（雲端） |
| **Claude Code 原生整合** | 是（MCP + hooks） | 是 | 需自行接入 |
| **跨 session 召回** | 是 | 手動 | 是 |

**同時做到「零 Token 消耗 + 全自動捕捉 + 原生 MCP 整合」——目前沒有其他工具能同時達成這三點。**

核心優勢：

- **零 Token 開銷** — hooks 逐字捕捉對話內容，寫入時不呼叫任何 LLM
- **完全自動** — `SessionStart`、`UserPromptSubmit`、`PostToolUse`、`Stop` 四個 hook 在背景靜默運行，無需任何手動操作
- **100% 本地** — 所有資料儲存在本機的嵌入式 LanceDB 資料庫中，無需 API 金鑰，無任何網路請求
- **兩層檢索** — 先用 session 摘要做粗篩，再在命中的 session 內做細粒度 chunk 搜尋，比 flat search 精準得多
- **自動 session 摘要** — 兩階段流水線：session 結束時即時計算 centroid，背景回填生成結構化摘要（opening + body）並以更精確的 embedding 取代 centroid——全程零 LLM 消耗
- **精確去重** — SHA-256 hash 防止相同內容在跨 session 間被重複儲存

---

## 技術棧與算法

### 儲存層
- **[LanceDB](https://lancedb.github.io/lancedb/)** — 基於 Apache Arrow 的嵌入式列式向量資料庫。無需獨立 server 進程，直接在 Patrick 進程內運行。兩張表：`session_summaries`（每 session 一行）和 `turn_chunks`（每個文字 chunk 一行）。
- **[PyArrow](https://arrow.apache.org/docs/python/)** — 列式 schema 定義、高效批次寫入、基於 filter 的全表掃描。

### 嵌入模型
- **[fastembed](https://github.com/qdrant/fastembed)** + **ONNX Runtime** — 在本地透過 ONNX 運行 `paraphrase-multilingual-MiniLM-L12-v2`（384 維向量）。無需 GPU，Apple Silicon 冷啟動約 1 秒。
- 原生支援中英文混合對話。
- 嵌入計算透過 `asyncio.run_in_executor` offload 到 thread pool，避免阻塞 MCP event loop。

### 檢索算法

1. **第一層（session 粗篩）** — 對查詢向量做 cosine 搜尋 `session_summaries`，找出相似度超過門檻的 session。
2. **第二層（chunk 細搜）** — 在第一層命中的 session 範圍內，對 `turn_chunks` 做 cosine 搜尋。
3. **優雅降級** — 若第一層命中 session 數少於 3 個，自動退回全局 chunk 搜尋。
4. **Context 展開** — 對每個命中 chunk，取回同一 `turn_id` 的所有兄弟 chunk，還原完整的對話上下文。

### Session 摘要（兩階段流水線）

**為什麼不用 LLM 摘要？** Patrick 的核心約束是零 Token 消耗。呼叫 LLM 來摘要每個 session 會增加延遲、API 成本，並且打破「完全本地」的承諾。因此我們採用兩階段流水線，結合純數學計算與本地 embedding——不需要任何外部 API 呼叫。

**第一階段——Centroid（即時，session 結束時）：**

1. 讀取該 session 所有 chunk 的向量。
2. 計算所有向量的**均值 centroid**（純 numpy，零 LLM 消耗）。
3. 找出餘弦相似度最高的前 3 個 chunk。
4. 將這 3 個 chunk 的文字拼接為臨時 `summary_text`。
5. 將 centroid 向量作為**臨時**的 Layer 1 搜尋錨點，upsert 到 `session_summaries`。

**第二階段——結構化摘要（背景回填）：**
背景排程器會拾取標記為 `pending` 的 session，生成更豐富的摘要：
- **一般 session：** `opening` = 第一句 user prompt（≤200 字），`body` = 前 5 個 assistant 回覆（以 cosine ≥ 0.8 多樣性門檻篩選，避免重複內容）。
- **多 agent session：** `opening` = 討論主題，`body` = 群主的 broadcast 訊息。
- 組合後的 `opening + body` 文字會用同一個 fastembed 模型做 **embedding**，新的向量會**取代** centroid 向量，成為 Layer 1 搜尋錨點。

**搜尋如何使用摘要：**
- 查詢時，第一層將你的查詢做 embedding，然後對 `session_summaries` 中的 session 向量做 **cosine 相似度搜尋**——快速定位哪些 session 與查詢語義相關。
- 對於已完成回填的 session，搜尋向量是結構化摘要（opening + body）的 embedding，比原始 centroid 語義更精準。
- 只有 top-K 命中的 session 進入第二層，在其中搜尋具體的 chunk 取得精確答案。
- 這種兩層設計避免了直接搜尋數百萬個 chunk——session 向量作為廉價的「session 級索引」，將搜尋範圍縮小 10～100 倍。

### 去重機制（兩層）

**第一層——精確去重（寫入時）：**
- 每個 chunk 的 **SHA-256** hash 儲存在 `turn_chunks.text_hash`。
- 寫入前 `hash_exists()` 檢查碰撞，完全相同的文字靜默丟棄。

**第二層——語義去重（session 結束時）：**
- Session 結束時（stop hook），Patrick 掃描該 session 內所有 chunk。
- 貪心算法：按時間倒序遍歷 chunk，只有與所有已保留 chunk 的 cosine 相似度均低於 0.95 時才保留。
- 超過門檻的 chunk 從資料庫中**實際刪除**——這能捕捉 SHA-256 無法偵測的「換句話說」式重複。

### MCP Server

- **[FastMCP](https://github.com/jlowin/fastmcp)** SSE server，監聽 `http://127.0.0.1:3141/sse`
- 暴露 3 個工具：`memory_search`、`memory_sessions`、`memory_save`
- 自訂 `/observe` POST endpoint 供 hook 推送事件

---

## 安裝

**前置需求：** Python 3.10+、[Claude Code](https://docs.anthropic.com/claude-code)、Git

```bash
git clone https://github.com/AndyWeiBoan/patrick.git
cd patrick
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
patrick init          # 下載嵌入模型（約 220 MB）
patrick setup --auto  # 設定 Claude Code hooks + MCP server
patrick start         # 啟動記憶伺服器
```

然後**重啟 Claude Code** 讓 hooks 生效。

> **[完整安裝指南](INSTALL_ZH.md)** — 逐步教學、背景服務設定、常見問題排解、解除安裝說明。

---

## CLI 指令參考

| 指令 | 說明 |
|---|---|
| `patrick init` | 預下載嵌入模型並執行健康檢查 |
| `patrick setup` | 自動設定 Claude Code hooks + MCP 至 `~/.claude/settings.json` |
| `patrick start` | 啟動 memory server |
| `patrick doctor` | 健康檢查——server、hooks、設定、模型快取 |
| `patrick hooks-path` | 印出已安裝 hook 腳本的絕對路徑 |

---

## 資料儲存位置

所有記憶資料儲存在本機：

```
~/.patrick/data/
```

資料不會離開你的電腦。

---

## MCP 工具參考

| 工具 | 適用場景 |
|---|---|
| `memory_search` | 語義搜尋所有 chunk。模式：`vector`（預設）、`hybrid`（BM25+向量+rerank）、`recency`（hybrid+時間衰減）。支援 `hook_type` 過濾（`assistant_text`、`user_prompt`、`tool_use`）。 |
| `memory_sessions` | 瀏覽 session 及摘要。支援 `include_body`、`session_type` 過濾、`after` 日期過濾、分頁（`limit`/`offset`）。 |
| `memory_save` | 目前已停用——hooks 自動處理所有儲存。 |

---

## 專案狀態

- **Phase 1** ✅：自動 hook 捕捉、兩層向量搜尋、centroid session 摘要、SHA-256 去重、MCP server、`patrick setup` / `init` / `doctor` CLI。
- **Phase 2** ✅：BM25 混合搜尋、cross-encoder reranking、cosine 語義去重、eval harness + CI 品質門檻。
- **Phase 3** ✅：時間衰減 recency 加權、hook_type 分類、多值篩選。
- **Phase 4** ✅：兩階段 session 摘要流水線（centroid → 結構化摘要回填）、opening/body 欄位、multi-agent session 偵測、背景摘要排程器。
