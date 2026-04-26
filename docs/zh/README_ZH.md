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
- **自動 session 摘要** — session 結束時用純 numpy 計算所有 chunk 向量的 centroid，自動生成代表性摘要，零 LLM 消耗
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

### Session 摘要（Centroid 算法）

每個 session 結束時，Patrick 執行以下步驟：

1. 讀取該 session 所有 chunk 的向量。
2. 計算所有向量的**均值 centroid**（純 numpy，零 LLM 消耗）。
3. 找出餘弦相似度最高的前 3 個 chunk。
4. 將這 3 個 chunk 的文字拼接為 `summary_text`。
5. 將 centroid 向量作為 Layer 1 搜尋錨點，upsert 到 `session_summaries`。

### 去重機制

- 每個 chunk 的 **SHA-256** hash 儲存在 `turn_chunks.text_hash`。
- 寫入前先呼叫 `hash_exists()` 檢查碰撞，重複內容靜默丟棄。

### MCP Server

- **[FastMCP](https://github.com/jlowin/fastmcp)** SSE server，監聽 `http://127.0.0.1:3141/sse`
- 暴露 3 個工具：`memory_search`、`memory_sessions`、`memory_save`
- 自訂 `/observe` POST endpoint 供 hook 推送事件

---

## 安裝

### 前置需求

- Python 3.10 或以上
- 已安裝並設定好的 [Claude Code](https://docs.anthropic.com/claude-code)
- Git

### 第一步 — Clone 儲存庫

```bash
git clone https://github.com/AndyWeiBoan/patrick.git
cd patrick
```

### 第二步 — 建立虛擬環境並安裝

```bash
python3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate

pip install -e .
```

### 第三步 — 預下載嵌入模型

```bash
patrick init
```

這會下載 `paraphrase-multilingual-MiniLM-L12-v2`（約 220 MB）並快取到本地，同時執行快速健康檢查確認模型和 LanceDB 正常運作。後續啟動不需重新下載。

```
Patrick init — downloading embedding model...
  Model: sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
  Embedding model: ✓ downloaded / cached
  Tokenizer: ✓ loaded
  Sanity check: ✓ embedded 1 text → 384-dim vector
  LanceDB: ✓ connected at ~/.patrick/data

✓ Patrick init complete. Run: patrick start
```

### 第四步 — 設定 Claude Code

執行一行指令完成設定。它會自動將 MCP server 和四個 hook 寫入 `~/.claude/settings.json`：

```bash
patrick setup
```

你會看到變更預覽並確認後套用：

```
Patrick setup
==================================================

[1/3] Hook scripts
  ✓ /path/to/patrick/hooks/session_start.py
  ✓ /path/to/patrick/hooks/prompt_submit.py
  ✓ /path/to/patrick/hooks/post_tool_use.py
  ✓ /path/to/patrick/hooks/stop.py

[2/3] Settings file: ~/.claude/settings.json
  ~ Will add mcpServers.patrick-memory
  ~ Will add hooks.SessionStart
  ~ Will add hooks.UserPromptSubmit
  ~ Will add hooks.PostToolUse
  ~ Will add hooks.Stop

[3/3] Apply changes
  Apply now? [y/N]: y
  ✓ Written to ~/.claude/settings.json

Next steps:
  patrick start    # run the memory server
  Restart Claude Code for hooks to take effect.
```

> 跳過確認提示：`patrick setup --auto`
> 只預覽不寫入：`patrick setup --dry-run`

### 第五步 — 啟動 Patrick server

```bash
patrick start
```

成功啟動後你會看到：

```
INFO:     Embedding model loaded
INFO:     LanceDB initialized
INFO:     Uvicorn running on http://127.0.0.1:3141
```

保持這個終端機視窗開著（或設定為背景服務，見下方說明）。

### 第六步 — 重啟 Claude Code 並驗證

重啟 Claude Code 讓 hooks 生效，然後執行健康檢查：

```bash
patrick doctor
```

```
Patrick doctor
==================================================

[Server]
  ✓ Server is running at http://127.0.0.1:3141

[Hook scripts]
  ✓ session_start.py
  ✓ prompt_submit.py
  ✓ post_tool_use.py
  ✓ stop.py

[settings.json]
  ✓ MCP server configured
  ✓ hooks.SessionStart
  ✓ hooks.UserPromptSubmit
  ✓ hooks.PostToolUse
  ✓ hooks.Stop

[Embedding model]
  ✓ Model cached: sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2

All checks passed.
```

Patrick 開始運行後，你與 Claude Code 的每一段對話都會被自動捕捉。

---

### 設定為背景服務（macOS）

若希望 Patrick 在登入時自動啟動，可以建立 launchd plist：

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
    <string>start</string>
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

Log 輸出位於 `/tmp/patrick.log`。

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
| `memory_search` | 快速查找特定事實或關鍵字（支援 `mode="hybrid"` BM25+向量融合搜尋） |
| `memory_sessions` | 列出所有 session 及其摘要，支援分頁 |
| `memory_save` | 明確儲存某個決策或結論（hooks 已自動處理大部分儲存，此工具少用） |

---

## 專案狀態

- **Phase 1** ✅：自動 hook 捕捉、兩層向量搜尋、centroid session 摘要、SHA-256 去重、MCP server、`patrick setup` / `init` / `doctor` CLI。
- **Phase 2** ✅：BM25 混合搜尋、cross-encoder reranking、cosine 語義去重、eval harness + CI 品質門檻。
- **Phase 3** ✅：時間衰減 recency 加權、hook_type 分類、多值篩選。
- **Phase 4**（進行中）：Session summary UI 改進（opening/body 欄位）、summary 回填排程器。
