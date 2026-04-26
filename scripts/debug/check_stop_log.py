#!/usr/bin/env python3
"""Check stop hook debug log — compare regular vs multi-agent sessions."""
import json
from pathlib import Path

LOG = Path("/tmp/patrick_stop_debug_log.jsonl")

if not LOG.exists():
    print("No log yet. Waiting for next session to end...")
    raise SystemExit(0)

entries = []
for line in LOG.read_text().splitlines():
    line = line.strip()
    if line:
        try:
            entries.append(json.loads(line))
        except Exception:
            pass

if not entries:
    print("Log file is empty.")
    raise SystemExit(0)

print(f"{'timestamp':<17} {'session':<10} {'path?':<7} {'at_blocks':<11} {'last_msg_preview'}")
print("-" * 100)
for d in entries:
    print(
        f"{d['timestamp'][:16]:<17} "
        f"{d['session_id'][:8]:<10} "
        f"{str(d['transcript_path_present']):<7} "
        f"{d['assistant_text_blocks_in_transcript']:<11} "
        f"{d.get('last_assistant_message_preview','')[:60]}"
    )

print()
print(f"Total entries: {len(entries)}")
path_missing = [d for d in entries if not d['transcript_path_present']]
print(f"Sessions with transcript_path MISSING: {len(path_missing)}")
path_present = [d for d in entries if d['transcript_path_present']]
print(f"Sessions with transcript_path present: {len(path_present)}")
