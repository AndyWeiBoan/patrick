#!/usr/bin/env python3
"""Hook: Stop — capture assistant text from payload or transcript, then signal session end."""
import glob
import json
import os
import sys
import urllib.request
from pathlib import Path

SERVER_URL = "http://127.0.0.1:3141/observe"
TIMEOUT = 3
MAX_TEXT_CHARS = 8_000


# ── helpers ───────────────────────────────────────────────────────────────────

def _parse_stdin() -> dict:
    try:
        raw = sys.stdin.read().strip()
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


def _resolve_session_id(data: dict) -> str:
    return (
        data.get("session_id")
        or data.get("sessionId")
        or os.environ.get("PATRICK_SESSION_ID")
        or ""
    )


def _resolve_transcript_path(data: dict, session_id: str) -> str:
    path = data.get("transcript_path", "")
    if path:
        return path
    matches = glob.glob(
        str(Path.home() / ".claude" / "projects" / "**" / f"{session_id}.jsonl"),
        recursive=True,
    )
    return matches[0] if matches else ""


def _extract_last_assistant_text(transcript_path: str) -> str:
    """Read transcript JSONL, return only the last assistant text response."""
    try:
        with open(transcript_path, encoding="utf-8") as f:
            lines = [ln.strip() for ln in f if ln.strip()]
    except Exception:
        return ""

    seen_ids: set[str] = set()
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except Exception:
            continue

        msg = entry.get("message", {})
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue

        msg_id = msg.get("id", "")
        if msg_id in seen_ids:
            continue
        seen_ids.add(msg_id)

        parts = [
            block.get("text", "").strip()
            for block in msg.get("content", []) or []
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        text = "\n\n".join(filter(None, parts))
        if text:
            return text[:MAX_TEXT_CHARS]

    return ""


def _resolve_assistant_text(data: dict, session_id: str) -> str:
    # 1. Prefer payload field — headless mode provides this directly
    text = data.get("last_assistant_message", "").strip()
    if text:
        return text[:MAX_TEXT_CHARS]

    # 2. Fallback: parse transcript file
    transcript_path = _resolve_transcript_path(data, session_id)
    return _extract_last_assistant_text(transcript_path) if transcript_path else ""


def post(payload_dict: dict) -> None:
    payload = json.dumps(payload_dict, ensure_ascii=False).encode()
    try:
        req = urllib.request.Request(
            SERVER_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=TIMEOUT)
    except Exception:
        pass


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    data = _parse_stdin()

    session_id = _resolve_session_id(data)
    if not session_id:
        return

    text = _resolve_assistant_text(data, session_id)
    if text:
        post({"hook": "stop-text", "session_id": session_id, "text": text, "role": "assistant"})

    post({"hook": "stop", "session_id": session_id})


if __name__ == "__main__":
    main()
