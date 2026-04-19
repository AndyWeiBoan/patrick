#!/usr/bin/env python3
"""Hook: UserPromptSubmit — capture user input and send to Patrick server."""
import json
import sys
import urllib.request

SERVER_URL = "http://127.0.0.1:3141/observe"
TIMEOUT = 3
MAX_TEXT_BYTES = 32_000  # ~8K tokens, generous limit


def main() -> None:
    try:
        stdin_raw = sys.stdin.read()
        data = json.loads(stdin_raw) if stdin_raw.strip() else {}
    except Exception:
        return

    session_id = data.get("session_id") or data.get("sessionId", "")
    prompt = data.get("prompt", "")

    if not session_id or not prompt:
        return

    # Truncate very long prompts
    text = prompt[:MAX_TEXT_BYTES] if len(prompt) > MAX_TEXT_BYTES else prompt

    payload = json.dumps({
        "hook": "prompt-submit",
        "session_id": session_id,
        "text": text,
        "role": "user",
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
        pass  # fire-and-forget


if __name__ == "__main__":
    main()
