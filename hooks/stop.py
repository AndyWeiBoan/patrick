#!/usr/bin/env python3
"""Hook: Stop — capture all assistant text from transcript, then signal session end.

Fallback logic for multi-agent sessions where Claude Code may send
an empty payload (no session_id, no transcript_path):
  1. session_id: fall back to PATRICK_SESSION_ID env var (set by SessionStart hook)
  2. transcript_path: glob search ~/.claude/projects/*/{session_id}.jsonl
"""
import glob
import json
import os
import sys
import urllib.request
from pathlib import Path

SERVER_URL = "http://127.0.0.1:3141/observe"
TIMEOUT = 3
MAX_TEXT_CHARS = 8_000


def extract_all_assistant_texts(transcript_path: str) -> list[str]:
    """Read transcript JSONL, return all unique assistant text responses."""
    try:
        with open(transcript_path, encoding="utf-8") as f:
            lines = [ln.strip() for ln in f if ln.strip()]
    except Exception:
        return []

    seen_ids = set()
    texts = []

    for line in lines:
        try:
            entry = json.loads(line)
        except Exception:
            continue

        # Transcript format: entry["message"]["role"], not top-level entry["role"]
        msg = entry.get("message", {})
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != "assistant":
            continue

        msg_id = msg.get("id", "")
        if msg_id in seen_ids:
            continue

        content = msg.get("content", [])
        if not isinstance(content, list):
            continue

        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                t = block.get("text", "").strip()
                if t:
                    parts.append(t)

        if parts:
            seen_ids.add(msg_id)
            texts.append("\n\n".join(parts)[:MAX_TEXT_CHARS])

    return texts


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


def main() -> None:
    try:
        stdin_raw = sys.stdin.read()
        data = json.loads(stdin_raw) if stdin_raw.strip() else {}
    except Exception:
        data = {}

    session_id = (
        data.get("session_id")
        or data.get("sessionId", "")
        or os.environ.get("PATRICK_SESSION_ID", "")
    )
    if not session_id:
        return

    # 1. Extract and store ALL assistant text responses from transcript
    transcript_path = data.get("transcript_path", "")
    if not transcript_path:
        # Fallback: search for transcript file by session_id
        matches = glob.glob(
            str(Path.home() / ".claude" / "projects" / "*" / f"{session_id}.jsonl")
        )
        if matches:
            transcript_path = matches[0]
    if transcript_path:
        for text in extract_all_assistant_texts(transcript_path):
            post({
                "hook": "stop-text",
                "session_id": session_id,
                "text": text,
                "role": "assistant",
            })

    # 2. Signal session end — triggers final centroid update in observer
    post({
        "hook": "stop",
        "session_id": session_id,
    })


if __name__ == "__main__":
    main()
