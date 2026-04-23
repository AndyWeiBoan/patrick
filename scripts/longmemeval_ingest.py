#!/usr/bin/env python3
"""LongMemEval ingest script — downloads and indexes LongMemEval data into Patrick's LanceDB.

Usage:
    python scripts/longmemeval_ingest.py
    python scripts/longmemeval_ingest.py --skip-download   # if data already present
    python scripts/longmemeval_ingest.py --limit 100       # ingest first N questions only

Data is stored in a SEPARATE LanceDB at ~/.patrick/longmemeval_data/ to avoid
corrupting the user's production memory at ~/.patrick/data/.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ── Path setup ────────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

DATA_DIR = REPO_ROOT / "data"
LME_JSON = DATA_DIR / "longmemeval_s_cleaned.json"
MANIFEST_FILE = DATA_DIR / "longmemeval_manifest.json"
LME_DB_PATH = Path.home() / ".patrick" / "longmemeval_data"

LME_URL = "https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/longmemeval_s_cleaned.json"

CHUNK_SIZE = 400
CHUNK_OVERLAP = 80
EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
BATCH_SIZE = 32


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def download_dataset(dest: Path) -> None:
    """Download LongMemEval JSON from HuggingFace."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {LME_URL}")
    print(f"  -> {dest}")

    def _progress(block_num: int, block_size: int, total_size: int) -> None:
        downloaded = block_num * block_size
        if total_size > 0:
            pct = min(100, downloaded * 100 // total_size)
            mb = downloaded / 1024 / 1024
            total_mb = total_size / 1024 / 1024
            print(f"\r  {pct:3d}%  {mb:.1f}/{total_mb:.1f} MB", end="", flush=True)

    urllib.request.urlretrieve(LME_URL, dest, reporthook=_progress)
    print()  # newline after progress
    print(f"  Download complete: {dest.stat().st_size / 1024 / 1024:.1f} MB")


def chunk_text_with_tokenizer(text: str, tokenizer) -> list[str]:
    """Sliding window chunking: 400 tokens, 80 overlap."""
    encoding = tokenizer.encode(text)
    token_ids = encoding.ids

    if len(token_ids) <= CHUNK_SIZE:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(token_ids):
        end = min(start + CHUNK_SIZE, len(token_ids))
        chunk_ids = token_ids[start:end]
        chunk_text = tokenizer.decode(chunk_ids)
        chunks.append(chunk_text)
        if end == len(token_ids):
            break
        start += CHUNK_SIZE - CHUNK_OVERLAP

    return chunks


def build_lancedb_tables(db_path: Path):
    """Open or create LanceDB tables at the given path."""
    import lancedb
    import pyarrow as pa

    db_path.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(db_path))

    chunk_schema = pa.schema([
        pa.field("chunk_id", pa.string()),
        pa.field("session_id", pa.string()),
        pa.field("turn_id", pa.string()),
        pa.field("chunk_index", pa.int32()),
        pa.field("total_chunks", pa.int32()),
        pa.field("role", pa.string()),
        pa.field("text", pa.string()),
        pa.field("vector", pa.list_(pa.float32(), 384)),
        pa.field("source", pa.string()),
        pa.field("text_hash", pa.string()),
        pa.field("source_file", pa.string()),
        pa.field("created_at", pa.string()),
    ])

    existing = db.table_names()
    if "turn_chunks" not in existing:
        table = db.create_table("turn_chunks", schema=chunk_schema)
    else:
        table = db.open_table("turn_chunks")

    return db, table


def get_existing_hashes(table) -> set[str]:
    """Load all existing text_hashes for dedup."""
    try:
        import pandas as pd
        df = table.to_pandas(columns=["text_hash"])
        return set(df["text_hash"].tolist())
    except Exception:
        return set()


def insert_chunks_batch(table, chunks: list[dict]) -> None:
    """Insert a batch of chunk dicts into LanceDB."""
    import pyarrow as pa

    if not chunks:
        return

    table.add(pa.table({
        "chunk_id": [c["chunk_id"] for c in chunks],
        "session_id": [c["session_id"] for c in chunks],
        "turn_id": [c["turn_id"] for c in chunks],
        "chunk_index": pa.array([c["chunk_index"] for c in chunks], pa.int32()),
        "total_chunks": pa.array([c["total_chunks"] for c in chunks], pa.int32()),
        "role": [c["role"] for c in chunks],
        "text": [c["text"] for c in chunks],
        "vector": pa.array([c["vector"] for c in chunks], type=pa.list_(pa.float32(), 384)),
        "source": [c["source"] for c in chunks],
        "text_hash": [c["text_hash"] for c in chunks],
        "source_file": [c["source_file"] for c in chunks],
        "created_at": [c["created_at"] for c in chunks],
    }))


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest LongMemEval into Patrick LanceDB")
    parser.add_argument("--skip-download", action="store_true",
                        help="Skip download if data/longmemeval_s_cleaned.json already exists")
    parser.add_argument("--limit", type=int, default=None,
                        help="Only ingest first N questions (for testing)")
    parser.add_argument("--wipe", action="store_true",
                        help="Wipe existing LanceDB before ingesting")
    args = parser.parse_args()

    # ── Step 1: Download ──────────────────────────────────────────────────────
    if not args.skip_download or not LME_JSON.exists():
        download_dataset(LME_JSON)
    else:
        print(f"Skipping download — using existing {LME_JSON}")

    # ── Step 2: Load JSON ─────────────────────────────────────────────────────
    print(f"\nLoading {LME_JSON} ...")
    with LME_JSON.open(encoding="utf-8") as f:
        data = json.load(f)

    if args.limit:
        data = data[: args.limit]
        print(f"  Limiting to {args.limit} questions")

    print(f"  Loaded {len(data)} questions")

    # ── Step 3: Initialize tokenizer and embedding model ──────────────────────
    print("\nInitializing tokenizer and embedding model ...")
    from tokenizers import Tokenizer
    tokenizer = Tokenizer.from_pretrained(EMBEDDING_MODEL)
    print("  Tokenizer ready")

    from fastembed import TextEmbedding
    embed_model = TextEmbedding(model_name=EMBEDDING_MODEL)
    print("  Embedding model ready")

    # ── Step 4: Setup LanceDB ─────────────────────────────────────────────────
    print(f"\nSetting up LanceDB at {LME_DB_PATH} ...")
    if args.wipe and LME_DB_PATH.exists():
        import shutil
        shutil.rmtree(LME_DB_PATH)
        print("  Wiped existing DB")

    db, table = build_lancedb_tables(LME_DB_PATH)
    existing_hashes = get_existing_hashes(table)
    print(f"  LanceDB ready (existing chunks: {len(existing_hashes)})")

    # ── Step 5: Collect all unique chunk metadata (no embedding yet) ─────────
    try:
        from tqdm import tqdm
        use_tqdm = True
    except ImportError:
        use_tqdm = False
        print("  (tqdm not installed — no progress bars)")

    total_sessions = 0
    total_chunks_skipped = 0
    seen_session_ids: set[str] = set()

    # Map text_hash -> meta dict (dedup by hash before embedding)
    # We preserve only one record per unique text.
    unique_chunks: dict[str, dict] = {}  # text_hash -> meta

    print("\nPhase 1: Tokenizing and collecting chunks ...")
    question_iter = tqdm(data, desc="  Tokenizing") if use_tqdm else data

    for entry in question_iter:
        # haystack_sessions is a list of sessions, each session is a list of messages.
        # haystack_session_ids is the parallel list of session IDs.
        haystack_sessions = entry.get("haystack_sessions", [])
        haystack_session_ids = entry.get("haystack_session_ids", [])

        for sess_idx, messages in enumerate(haystack_sessions):
            session_id = (
                haystack_session_ids[sess_idx]
                if sess_idx < len(haystack_session_ids)
                else f"lme_sess_{entry.get('question_id', 'unk')}_{sess_idx}"
            )

            if session_id not in seen_session_ids:
                seen_session_ids.add(session_id)
                total_sessions += 1

            created_at = _now()

            for turn_idx, message in enumerate(messages):
                role = message.get("role", "user")
                content = message.get("content", "")
                if not content.strip():
                    continue

                turn_id = f"lme_{session_id}_{turn_idx}"
                chunks = chunk_text_with_tokenizer(content, tokenizer)
                total_c = len(chunks)

                for chunk_idx, chunk_text in enumerate(chunks):
                    th = text_hash(chunk_text)
                    if th in existing_hashes:
                        total_chunks_skipped += 1
                        continue
                    if th not in unique_chunks:
                        unique_chunks[th] = {
                            "chunk_id": f"lme_{session_id}_{turn_idx}_{chunk_idx}",
                            "session_id": session_id,
                            "turn_id": turn_id,
                            "chunk_index": chunk_idx,
                            "total_chunks": total_c,
                            "role": role,
                            "text": chunk_text,
                            "source": "longmemeval",
                            "text_hash": th,
                            "source_file": "longmemeval_s_cleaned.json",
                            "created_at": created_at,
                        }

    all_metas = list(unique_chunks.values())
    print(f"  Collected {len(all_metas)} unique new chunks ({total_chunks_skipped} skipped as duplicates)")

    # ── Phase 2: Embed all chunks in one streaming pass ───────────────────────
    print(f"\nPhase 2: Embedding {len(all_metas)} chunks (batch streaming via fastembed) ...")
    all_texts = [m["text"] for m in all_metas]

    total_chunks_written = 0
    # fastembed.embed() is a generator — it streams in batches internally.
    # We collect vectors and insert in large write batches to minimize LanceDB overhead.
    WRITE_BATCH = 2000
    write_buf: list[dict] = []

    vectors_iter = embed_model.embed(all_texts)
    if use_tqdm:
        vectors_iter = tqdm(vectors_iter, total=len(all_texts), desc="  Embedding")

    for meta, vec in zip(all_metas, vectors_iter):
        write_buf.append({**meta, "vector": vec.tolist()})
        if len(write_buf) >= WRITE_BATCH:
            insert_chunks_batch(table, write_buf)
            total_chunks_written += len(write_buf)
            write_buf.clear()

    if write_buf:
        insert_chunks_batch(table, write_buf)
        total_chunks_written += len(write_buf)
        write_buf.clear()

    print(f"\nIngest complete:")
    print(f"  Questions processed : {len(data)}")
    print(f"  Unique sessions     : {total_sessions}")
    print(f"  Chunks written      : {total_chunks_written}")
    print(f"  Chunks skipped(dup) : {total_chunks_skipped}")

    # ── Step 6: Save manifest ─────────────────────────────────────────────────
    manifest = {
        "total_questions": len(data),
        "total_sessions": total_sessions,
        "total_chunks": total_chunks_written,
        "embedding_model": EMBEDDING_MODEL,
        "chunk_size": CHUNK_SIZE,
        "overlap": CHUNK_OVERLAP,
        "created_at": _now(),
        "db_path": str(LME_DB_PATH),
        "source_file": str(LME_JSON),
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST_FILE.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    print(f"\nManifest saved to {MANIFEST_FILE}")


if __name__ == "__main__":
    main()
