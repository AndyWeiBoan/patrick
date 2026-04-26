#!/usr/bin/env python3
"""
Spike 1: 驗證 Claude Code hook 實際提供的 session_id 來源

官方文件確認（https://docs.anthropic.com/en/docs/claude-code/hooks）：
- stdin JSON 所有 hook 都有 common field: session_id（snake_case）
- 無 CLAUDE_SESSION_ID 環境變數（官方文件未列出此 env var）
- 正確優先順序：(1) stdin['session_id'] → (2) uuid4() fallback

此 spike 目的：在真實 hook 觸發環境驗證上述官方文件描述是否符合實際行為。

用法：把這個腳本設成任意 hook（如 PreToolUse），觸發一次，看 output 落在哪裡

hooks.json 設定範例（任選一個 hook event 測試）：
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "python3 /Users/andy/llm-mem/patrick/spikes/spike_session_id.py >> /tmp/patrick_spike_session.log 2>&1"
          }
        ]
      }
    ]
  }
}

執行後看：/tmp/patrick_spike_session.log
"""

import json
import os
import sys
import uuid
from datetime import datetime

LOG_FILE = "/tmp/patrick_spike_session.log"

def main():
    timestamp = datetime.now().isoformat()
    results: dict = {
        "timestamp": timestamp,
        "hook_event": sys.argv[1] if len(sys.argv) > 1 else "unknown",
        "doc_expectation": {
            "expected_field": "stdin['session_id'] (snake_case)",
            "expected_env_var": "none (CLAUDE_SESSION_ID does not exist per official docs)",
        }
    }

    # --- 1. 環境變數掃描（僅供確認，官方文件說沒有 session_id 相關 env var）---
    claude_env_vars = {k: v for k, v in os.environ.items() if "CLAUDE" in k.upper()}
    results["env_claude_vars"] = claude_env_vars
    results["env_CLAUDE_SESSION_ID"] = os.environ.get("CLAUDE_SESSION_ID", "<NOT SET — expected per docs>")
    results["env_CLAUDE_PROJECT_DIR"] = os.environ.get("CLAUDE_PROJECT_DIR", "<NOT SET>")

    # --- 2. stdin JSON 解析 ---
    stdin_raw = ""
    stdin_json = None
    try:
        stdin_raw = sys.stdin.read()
        if stdin_raw.strip():
            stdin_json = json.loads(stdin_raw)
    except Exception as e:
        results["stdin_parse_error"] = str(e)

    results["stdin_raw_length"] = len(stdin_raw)
    results["stdin_json"] = stdin_json

    # 驗證 stdin 欄位
    if isinstance(stdin_json, dict):
        # 官方文件 common fields
        results["stdin_session_id"] = stdin_json.get("session_id", "<NOT FOUND>")
        results["stdin_transcript_path"] = stdin_json.get("transcript_path", "<NOT FOUND>")
        results["stdin_hook_event_name"] = stdin_json.get("hook_event_name", "<NOT FOUND>")
        results["stdin_cwd"] = stdin_json.get("cwd", "<NOT FOUND>")
        results["stdin_permission_mode"] = stdin_json.get("permission_mode", "<NOT FOUND>")

        # camelCase 檢查（官方文件說不應該存在，驗證一下）
        results["stdin_sessionId_camel"] = stdin_json.get("sessionId", "<NOT FOUND — expected per docs>")

        # 全部 session/id 相關 key（診斷用）
        results["stdin_all_session_related_keys"] = {
            k: v for k, v in stdin_json.items()
            if any(kw in k.lower() for kw in ["session", "conversation", "id"])
        }

    # --- 3. 結論（按官方文件確認的正確優先順序）---
    # 正確順序：stdin['session_id'] → uuid4() fallback（無 env var 路徑）
    session_id_found = None
    source = None

    if isinstance(stdin_json, dict) and stdin_json.get("session_id"):
        session_id_found = stdin_json["session_id"]
        source = "stdin_json['session_id'] — PASS (matches official docs)"
    else:
        session_id_found = str(uuid.uuid4())
        source = "uuid4() fallback — WARN: stdin session_id missing, check hook config"

    results["conclusion"] = {
        "session_id": session_id_found,
        "source": source,
        "doc_validated": source.startswith("stdin_json['session_id']"),
    }

    # --- 4. 驗收判定 ---
    passed = results["conclusion"]["doc_validated"]
    results["spike_result"] = "PASS ✅" if passed else "FAIL ❌ — session_id not in stdin, official docs may be wrong or hook not configured correctly"

    # --- 輸出 ---
    output = json.dumps(results, indent=2, ensure_ascii=False)
    print(output, flush=True)

    # 同時寫入 log 方便事後看
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write("\n" + "="*60 + "\n")
        f.write(output)
        f.write("\n")

if __name__ == "__main__":
    main()
