# Installation Guide

<p align="center">
  <a href="INSTALL.md"><b>English</b></a> &nbsp;|&nbsp; <a href="zh/INSTALL_ZH.md">中文</a>
</p>

---

## Prerequisites

- **Python 3.10** or later
- **[Claude Code](https://docs.anthropic.com/claude-code)** installed and configured
- **Git**

---

## Quick Start

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

---

## Step-by-Step Guide

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

### Step 3 — Pre-download the embedding model

```bash
patrick init
```

This downloads `paraphrase-multilingual-MiniLM-L12-v2` (~220 MB) and caches it locally, then runs a quick sanity check to confirm the model and LanceDB are working correctly. Subsequent starts are instant.

```
Patrick init — downloading embedding model...
  Model: sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
  Embedding model: ✓ downloaded / cached
  Tokenizer: ✓ loaded
  Sanity check: ✓ embedded 1 text → 384-dim vector
  LanceDB: ✓ connected at ~/.patrick/data

✓ Patrick init complete. Run: patrick start
```

### Step 4 — Configure Claude Code

Run the one-command setup. It automatically writes the MCP server entry and all four hooks to `~/.claude/settings.json`:

```bash
patrick setup
```

You'll see a preview of changes and be asked to confirm:

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

> To skip the confirmation prompt, use `patrick setup --auto`.
> To preview changes without writing anything, use `patrick setup --dry-run`.

### Step 5 — Start the Patrick server

```bash
patrick start
```

You should see:

```
INFO:     Embedding model loaded
INFO:     LanceDB initialized
INFO:     Uvicorn running on http://127.0.0.1:3141
```

Keep this terminal running (or set it up as a background service — see below).

### Step 6 — Restart Claude Code and verify

Restart Claude Code for the hooks to take effect. Then run a health check:

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

Patrick is now running. Every conversation you have with Claude Code is automatically captured.

---

## Running as a Background Service (macOS)

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

Logs are available at `/tmp/patrick.log`.

---

## Troubleshooting

| Problem | Solution |
|---|---|
| `patrick: command not found` | Make sure the virtual environment is activated: `source .venv/bin/activate` |
| Port 3141 already in use | Kill the previous instance: `lsof -ti:3141 \| xargs kill` |
| Embedding model download fails | Check internet connection and retry `patrick init` |
| Hooks not working after setup | Restart Claude Code — hooks only load on startup |
| `patrick doctor` reports failures | Run `patrick setup` again to reconfigure |

---

## Uninstall

```bash
# Remove Claude Code configuration
# (manually remove the patrick-memory entries from ~/.claude/settings.json)

# Remove the launchd service (if installed)
launchctl unload ~/Library/LaunchAgents/com.patrick.memory.plist
rm ~/Library/LaunchAgents/com.patrick.memory.plist

# Remove Patrick data
rm -rf ~/.patrick

# Remove the source code
rm -rf /path/to/patrick
```
