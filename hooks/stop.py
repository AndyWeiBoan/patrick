#!/usr/bin/env python3
"""Hook: Stop — signal session end to Patrick server."""
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

    session_id = data.get("session_id") or data.get("sessionId", "")
    if not session_id:
        return

    payload = json.dumps({
        "hook": "stop",
        "session_id": session_id,
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
