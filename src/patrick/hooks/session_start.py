#!/usr/bin/env python3
"""Hook: SessionStart — register session with Patrick server.

Usage in hooks.json:
  "SessionStart": [{"matcher": "", "hooks": [{"type": "command",
    "command": "python3 /path/to/hooks/session_start.py"}]}]
"""
import json
import sys
import urllib.request

SERVER_URL = "http://127.0.0.1:3141/observe"
TIMEOUT = 3


def main() -> None:
    try:
        stdin_raw = sys.stdin.read()
        data = json.loads(stdin_raw) if stdin_raw.strip() else {}
    except Exception:
        data = {}

    session_id = data.get("session_id") or data.get("sessionId")
    if not session_id:
        return  # no session_id, nothing to register

    # Phase 5: capture cwd at session start for project-scoped memory.
    # Normalisation (expanduser + realpath) happens server-side in observer.py.
    # Graceful fallback to "" if cwd is not in payload (older Claude Code).
    project_path = data.get("cwd", "")

    payload = json.dumps({
        "hook": "session-start",
        "session_id": session_id,
        "project_path": project_path,
    }).encode()

    try:
        req = urllib.request.Request(
            SERVER_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=TIMEOUT)
    except Exception:
        pass  # fire-and-forget: server not running is acceptable

    # Return structured additionalContext so Claude Code injects session_id
    # into the conversation. Claude can then pass the correct session_id when
    # calling memory_save (workaround: CLAUDE_SESSION_ID env var doesn't exist,
    # see github.com/anthropics/claude-code/issues/25642).
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": f"PATRICK_SESSION_ID={session_id}",
        }
    }))


if __name__ == "__main__":
    main()
