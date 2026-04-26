# 安裝指南

<p align="center">
  <a href="../INSTALL.md">English</a> &nbsp;|&nbsp; <a href="INSTALL_ZH.md"><b>中文</b></a>
</p>

---

## 前置需求

- **Python 3.10** 或以上
- 已安裝並設定好的 **[Claude Code](https://docs.anthropic.com/claude-code)**
- **Git**

---

## 快速開始

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

---

## 逐步安裝

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
  ✓ /path/to/patrick/src/patrick/hooks/session_start.py
  ✓ /path/to/patrick/src/patrick/hooks/prompt_submit.py
  ✓ /path/to/patrick/src/patrick/hooks/post_tool_use.py
  ✓ /path/to/patrick/src/patrick/hooks/stop.py

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

## 設定為背景服務（macOS）

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

## 常見問題排解

| 問題 | 解法 |
|------|------|
| `patrick: command not found` | 確認虛擬環境已啟動：`source .venv/bin/activate` |
| Port 3141 已被佔用 | 關閉上一個實例：`lsof -ti:3141 \| xargs kill` |
| 嵌入模型下載失敗 | 檢查網路連線後重試 `patrick init` |
| Setup 後 hooks 沒作用 | 重啟 Claude Code——hooks 只在啟動時載入 |
| `patrick doctor` 報錯 | 重跑 `patrick setup` 重新設定 |

---

## 解除安裝

```bash
# 移除 Claude Code 設定
# （手動從 ~/.claude/settings.json 中移除 patrick-memory 相關項目）

# 移除 launchd 服務（如有安裝）
launchctl unload ~/Library/LaunchAgents/com.patrick.memory.plist
rm ~/Library/LaunchAgents/com.patrick.memory.plist

# 移除 Patrick 資料
rm -rf ~/.patrick

# 移除原始碼
rm -rf /path/to/patrick
```
