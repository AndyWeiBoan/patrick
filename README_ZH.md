<p align="right">
  <a href="README.md">English</a> | <a href="README_ZH.md">中文</a>
</p>

<p align="center">
  <img src="https://upload.wikimedia.org/wikipedia/en/3/33/Patrick_Star.svg" alt="派大星" width="180"/>
</p>

<h1 align="center">Patrick</h1>

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
- **[fastembed](https://github.com/qdrant/fastembed)** + **ONNX Runtime** — 在本地透過 ONNX 運行 `multilingual-e5-small`（384 維向量）。無需 GPU，Apple Silicon 冷啟動約 1 秒。
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
- 暴露 4 個工具：`memory_search`、`memory_deep_search`、`memory_sessions`、`memory_save`
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

這會安裝 `patrick` CLI 及所有依賴（LanceDB、fastembed、FastMCP、uvicorn 等）。

> **首次啟動說明：** fastembed 會在第一次啟動時下載 `multilingual-e5-small`（約 120 MB）並快取到本地。後續啟動不需重新下載。

### 第三步 — 啟動 Patrick server

```bash
patrick serve
```

成功啟動後你會看到：

```
INFO:     Embedding model loaded
INFO:     LanceDB initialized
INFO:     Uvicorn running on http://127.0.0.1:3141
```

保持這個終端機視窗開著（或設定為背景服務，見下方說明）。

### 第四步 — 設定 Claude Code

開啟你的 Claude Code 設定檔，有兩個位置可選：

- **全域設定**（套用到所有專案）：`~/.claude/settings.json`
- **專案設定**（僅套用到目前 repo）：`.claude/settings.json`

加入以下設定（可參考 `claude_config_example.json`）：

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

將 `/ABSOLUTE/PATH/TO/patrick` 替換為你的 repo 實際絕對路徑（例如 `/Users/you/projects/patrick`）。

### 第五步 — 驗證

開啟一個新的 Claude Code session。Patrick 會自動開始捕捉對話。若要確認 MCP 連線正常，可以問 Claude：

```
列出我最近的記憶 sessions。
```

Claude 會呼叫 `memory_sessions` 並顯示目前儲存的 session 列表。

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

Log 輸出位於 `/tmp/patrick.log`。

---

## 資料儲存位置

所有記憶資料儲存在本機：

```
~/.patrick/db/
```

資料不會離開你的電腦。

---

## MCP 工具參考

| 工具 | 適用場景 |
|---|---|
| `memory_search` | 快速查找特定事實或關鍵字 |
| `memory_deep_search` | 跨 session 的完整脈絡召回（建議在 session 開始時呼叫） |
| `memory_sessions` | 列出所有 session 及其摘要 |
| `memory_save` | 明確儲存某個決策或結論（hooks 已自動處理大部分儲存，此工具少用） |

---

## 專案狀態

- **Phase 1**（目前）：完整可用——自動 hook 捕捉、兩層向量搜尋、centroid session 摘要、去重、MCP server。
- **Phase 2**（規劃中）：BM25 混合搜尋，改善中英文混合對話的精確關鍵字召回。
- **Phase 3**（規劃中）：`patrick init` sanity check CLI，打包優化。
- **Phase 4**（規劃中）：PyPI 發布。
