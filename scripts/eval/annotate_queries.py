#!/usr/bin/env python3
"""
Annotation helper for C1: ground truth annotation.

Usage:
    python scripts/annotate_queries.py          # Show top-5 candidates per query
    python scripts/annotate_queries.py --top 10 # Show top-10 candidates per query
    python scripts/annotate_queries.py --output results/annotation_candidates.md

For each query in tests/eval/queries.jsonl, this script:
1. Runs hybrid search (BM25 + vector) to surface candidates
2. Prints chunk_id + text preview for human review
3. Outputs a Markdown file ready for copy-paste annotation

After reviewing, update relevant_chunk_ids in tests/eval/queries.jsonl manually
or use: python scripts/annotate_queries.py --apply results/annotation.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import textwrap
from pathlib import Path

# ── path setup ───────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from patrick.config import DATA_DIR, TOP_K_CHUNKS  # noqa: E402

TOP_CANDIDATES = 5  # default candidates shown per query


async def run_hybrid_search(query: str, top_n: int = 15) -> list[dict]:
    """Run hybrid search and return top-N chunk records."""
    import lancedb
    import numpy as np
    from rank_bm25 import BM25Okapi  # type: ignore[import]

    from patrick.embedding import provider
    from patrick.storage import _tokenize_for_bm25

    provider.initialize()

    db = lancedb.connect(DATA_DIR)
    tbl = db.open_table("turn_chunks")
    df = tbl.to_pandas()
    if df.empty:
        return []

    # ── vector search ────────────────────────────────────────────────────────
    q_vec_list = await provider.embed_async([query])
    q_vec = q_vec_list[0]
    vec_results = (
        tbl.search(q_vec, vector_column_name="vector")
        .limit(top_n)
        .to_pandas()
    )
    vec_ids = list(vec_results["chunk_id"])

    # ── BM25 search ──────────────────────────────────────────────────────────
    corpus_tokens = [_tokenize_for_bm25(str(t)) for t in df["text"].tolist()]
    bm25 = BM25Okapi(corpus_tokens)
    q_tokens = _tokenize_for_bm25(query)
    scores = bm25.get_scores(q_tokens)
    top_bm25_idx = np.argsort(scores)[::-1][:top_n]
    bm25_ids = [df.iloc[i]["chunk_id"] for i in top_bm25_idx]

    # ── RRF fusion ───────────────────────────────────────────────────────────
    RRF_K = 60
    rrf_scores: dict[str, float] = {}
    for rank, cid in enumerate(vec_ids):
        rrf_scores[cid] = rrf_scores.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
    for rank, cid in enumerate(bm25_ids):
        rrf_scores[cid] = rrf_scores.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)

    ranked_ids = sorted(rrf_scores, key=lambda x: rrf_scores[x], reverse=True)[:top_n]

    # ── build output records ─────────────────────────────────────────────────
    id_to_row = {row["chunk_id"]: row for _, row in df.iterrows()}
    results = []
    for cid in ranked_ids:
        if cid not in id_to_row:
            continue
        row = id_to_row[cid]
        results.append(
            {
                "chunk_id": cid,
                "text_hash": str(row.get("text_hash", "")),
                "session_id": row.get("session_id", ""),
                "text": str(row.get("text", "")),
                "rrf_score": round(rrf_scores[cid], 5),
            }
        )
    return results[:top_n]


async def main(top_n: int, output_path: Path | None) -> None:
    queries_path = ROOT / "tests" / "eval" / "queries.jsonl"
    queries = [json.loads(l) for l in queries_path.read_text().splitlines() if l.strip()]

    lines = []
    lines.append("# Annotation Candidates\n\n")
    lines.append("> For each query, mark candidates as G2 (highly relevant) or G1 (marginally relevant).\n")
    lines.append("> Use `text_hash` as the stable identifier — it survives DB resets.\n")
    lines.append("> Update `tests/eval/queries.jsonl`: set `relevant_text_hashes` and `relevance_grades` (keyed by hash).\n\n")
    lines.append("---\n\n")

    annotated = 0
    for q in queries:
        qid = q["id"]
        query_text = q["query"]
        lang = q.get("lang", "")
        existing = q.get("relevant_text_hashes", [])
        if existing:
            annotated += 1

        lines.append(f"## {qid} | `{query_text}` [{lang}]\n\n")
        if existing:
            lines.append(f"✅ Already annotated ({len(existing)} hashes)\n\n")
            continue

        print(f"Searching: {qid} — {query_text!r} ...", flush=True)
        candidates = await run_hybrid_search(query_text, top_n=top_n)

        if not candidates:
            lines.append("_No results found — check storage._\n\n")
            continue

        lines.append(f"**Candidates (top {len(candidates)}, hybrid search):**\n\n")
        for i, c in enumerate(candidates):
            preview = textwrap.shorten(c["text"], width=200, placeholder="…")
            lines.append(f"### [{i+1}] score={c['rrf_score']}\n")
            lines.append(f"- **text_hash**: `{c['text_hash']}`\n")
            lines.append(f"- **chunk_id**: `{c['chunk_id']}`\n")
            lines.append(f"- **session**: `{str(c['session_id'])[:30]}…`\n\n")
            lines.append(f"> {preview}\n\n")

        lines.append("**→ Paste into queries.jsonl:**\n")
        lines.append('```json\n')
        lines.append(f'"relevant_text_hashes": ["hash1", "hash2"],\n')
        lines.append(f'"relevance_grades": {{"hash1": 2, "hash2": 1}}\n')
        lines.append('```\n\n')
        lines.append("---\n\n")

    print(f"\nAnnotation status: {annotated}/30 already done.")

    md = "".join(lines)
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(md)
        print(f"Saved to: {output_path}")
    else:
        print("\n" + "=" * 60)
        print(md)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="C1 annotation helper")
    parser.add_argument("--top", type=int, default=TOP_CANDIDATES, help="Candidates per query")
    parser.add_argument("--output", type=Path, default=None, help="Save to Markdown file")
    args = parser.parse_args()
    asyncio.run(main(top_n=args.top, output_path=args.output))
