#!/usr/bin/env python3
"""臨時 debug 腳本：dump stop hook 的完整 stdin + transcript 內容到檔案。"""
import json
import sys
from datetime import datetime

OUT = "/tmp/patrick_stop_debug.json"


def main():
    stdin_raw = sys.stdin.read()
    try:
        data = json.loads(stdin_raw) if stdin_raw.strip() else {}
    except Exception:
        data = {"_parse_error": stdin_raw[:500]}

    result = {
        "timestamp": datetime.utcnow().isoformat(),
        "stdin": data,
        "transcript": None,
        "transcript_error": None,
    }

    transcript_path = data.get("transcript_path", "")
    if transcript_path:
        try:
            with open(transcript_path, encoding="utf-8") as f:
                lines = [ln.strip() for ln in f if ln.strip()]
            parsed = []
            for ln in lines:
                try:
                    parsed.append(json.loads(ln))
                except Exception:
                    parsed.append({"_raw": ln})
            result["transcript"] = parsed
        except Exception as e:
            result["transcript_error"] = str(e)
    else:
        result["transcript_error"] = "transcript_path not in stdin"

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
