#!/usr/bin/env python3
"""LongMemEval eval script — benchmarks Patrick's hybrid search on LongMemEval.

Metrics (SESSION-LEVEL):
  session_recall@10 : did any top-10 chunk come from an answer session?
  session_recall@5  : same for top-5
  session_mrr       : reciprocal rank of first chunk from an answer session

Usage:
    python scripts/longmemeval_eval.py
    python scripts/longmemeval_eval.py --mode vector          # skip hybrid
    python scripts/longmemeval_eval.py --limit 50             # quick smoke test
    python scripts/longmemeval_eval.py --top-k 20             # use top-20
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

# ── Path setup ────────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

DATA_DIR = REPO_ROOT / "data"
RESULTS_DIR = REPO_ROOT / "results"
LME_JSON = DATA_DIR / "longmemeval_s_cleaned.json"
LME_DB_PATH = Path.home() / ".patrick" / "longmemeval_data"
OUTPUT_FILE = RESULTS_DIR / "longmemeval_results.json"

EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

# Patrick's own-data Recall@10 (from prior eval run)
PATRICK_OWN_RECALL10 = 0.4711


def session_recall_at_k(answer_session_ids: set[str], chunks: list[dict], k: int) -> float:
    """Return 1.0 if any top-k chunk's session_id is in answer_session_ids, else 0.0."""
    for chunk in chunks[:k]:
        if chunk.get("session_id") in answer_session_ids:
            return 1.0
    return 0.0


def session_mrr(answer_session_ids: set[str], chunks: list[dict]) -> float:
    """Reciprocal rank of the first chunk from an answer session."""
    for rank, chunk in enumerate(chunks, start=1):
        if chunk.get("session_id") in answer_session_ids:
            return 1.0 / rank
    return 0.0


def connect_lancedb(db_path: Path):
    """Connect to LanceDB and open turn_chunks table."""
    import lancedb
    if not db_path.exists():
        raise FileNotFoundError(
            f"LongMemEval DB not found at {db_path}.\n"
            "Run longmemeval_ingest.py first."
        )
    db = lancedb.connect(str(db_path))
    if "turn_chunks" not in db.table_names():
        raise RuntimeError(f"'turn_chunks' table not found in {db_path}")
    return db.open_table("turn_chunks")


def search_vector(table, query_vector: list[float], top_k: int, recall_n: int) -> list[dict]:
    """Pure vector search over turn_chunks."""
    df = (
        table.search(query_vector)
        .metric("cosine")
        .limit(recall_n)
        .to_pandas()
    )
    return df.to_dict(orient="records")[:top_k]


def _tokenize_bm25(text: str) -> list[str]:
    """BM25 tokenizer — jieba for CJK, whitespace fallback."""
    try:
        import jieba
        return [tok for tok in jieba.cut(text.lower()) if tok.strip()]
    except ImportError:
        return text.lower().split()


def search_hybrid(table, query_vector: list[float], query_text: str,
                  top_k: int, recall_n: int,
                  bm25_index=None, bm25_chunks=None,
                  bm25_weight: float = 0.3, vector_weight: float = 0.7,
                  rrf_k: int = 60) -> list[dict]:
    """Hybrid search: RRF fusion of vector + BM25."""
    # Vector recall
    vec_df = (
        table.search(query_vector)
        .metric("cosine")
        .limit(recall_n)
        .to_pandas()
    )
    vector_results = vec_df.to_dict(orient="records")

    # BM25 recall
    if bm25_index is None or not bm25_chunks:
        return vector_results[:top_k]

    query_tokens = _tokenize_bm25(query_text)
    scores = bm25_index.get_scores(query_tokens)
    scored = sorted(zip(scores, bm25_chunks), key=lambda x: x[0], reverse=True)
    bm25_results = [chunk for _, chunk in scored[:recall_n]]

    # RRF fusion
    vec_rank: dict[str, int] = {
        c["chunk_id"]: rank for rank, c in enumerate(vector_results) if c.get("chunk_id")
    }
    bm25_rank_map: dict[str, int] = {
        c["chunk_id"]: rank for rank, c in enumerate(bm25_results) if c.get("chunk_id")
    }

    all_ids: dict[str, dict] = {}
    for c in vector_results + bm25_results:
        cid = c.get("chunk_id")
        if cid and cid not in all_ids:
            all_ids[cid] = c

    fused = []
    for cid, chunk in all_ids.items():
        vr = vec_rank.get(cid)
        br = bm25_rank_map.get(cid)
        rrf_score = 0.0
        if vr is not None:
            rrf_score += vector_weight / (rrf_k + vr + 1)
        if br is not None:
            rrf_score += bm25_weight / (rrf_k + br + 1)
        fused.append({**chunk, "_hybrid_score": rrf_score})

    fused.sort(key=lambda x: x["_hybrid_score"], reverse=True)
    return fused[:top_k]


def build_bm25_index(table):
    """Build BM25 index over all turn_chunks. Returns (bm25, chunks)."""
    try:
        from rank_bm25 import BM25Okapi
    except ImportError:
        print("  WARNING: rank-bm25 not installed, skipping BM25 component of hybrid search")
        return None, []

    print("  Building BM25 index over all chunks (this may take a moment) ...")
    t0 = time.perf_counter()
    df = table.to_pandas()
    chunks = df.to_dict(orient="records")
    tokenized = [_tokenize_bm25(str(c.get("text", ""))) for c in chunks]
    bm25 = BM25Okapi(tokenized, k1=1.5, b=0.75)
    elapsed = time.perf_counter() - t0
    print(f"  BM25 index built: {len(chunks)} chunks in {elapsed:.1f}s")
    return bm25, chunks


def embed_query(embed_model, query: str) -> list[float]:
    """Embed a single query string."""
    vectors = list(embed_model.embed([query]))
    return vectors[0].tolist()


def print_summary_table(results: dict) -> None:
    """Print a formatted summary table to stdout."""
    m = results["metrics"]
    print()
    print("=" * 60)
    print("  LongMemEval Benchmark Results")
    print("=" * 60)
    print(f"  Questions evaluated : {results['total_questions']}")
    print(f"  Search mode         : {results['mode']}")
    print(f"  Top-K               : {results['top_k']}")
    print()
    print(f"  {'Metric':<25} {'Score':>10}")
    print(f"  {'-'*35}")
    print(f"  {'Session Recall@10':<25} {m['session_recall@10']:>10.4f}")
    print(f"  {'Session Recall@5':<25} {m['session_recall@5']:>10.4f}")
    print(f"  {'Session MRR':<25} {m['session_mrr']:>10.4f}")
    print(f"  {'P95 Latency (ms)':<25} {m['p95_latency_ms']:>10.1f}")
    print()
    print(f"  Comparison (Patrick own-data Recall@10): {PATRICK_OWN_RECALL10:.4f}")
    lme_r10 = m['session_recall@10']
    delta = lme_r10 - PATRICK_OWN_RECALL10
    symbol = "↑" if delta > 0 else ("↓" if delta < 0 else "→")
    print(f"  LongMemEval vs own-data delta           : {delta:+.4f} {symbol}")
    print("=" * 60)
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate Patrick on LongMemEval")
    parser.add_argument("--mode", choices=["vector", "hybrid"], default="hybrid",
                        help="Search mode (default: hybrid)")
    parser.add_argument("--top-k", type=int, default=10,
                        help="Rank cutoff (default: 10)")
    parser.add_argument("--recall-n", type=int, default=50,
                        help="Recall candidates for hybrid (default: 50)")
    parser.add_argument("--limit", type=int, default=None,
                        help="Only evaluate first N questions (for testing)")
    parser.add_argument("--output", type=Path, default=OUTPUT_FILE,
                        help=f"Output JSON path (default: {OUTPUT_FILE})")
    args = parser.parse_args()

    # ── Load dataset ──────────────────────────────────────────────────────────
    if not LME_JSON.exists():
        print(f"ERROR: {LME_JSON} not found. Run longmemeval_ingest.py first.")
        sys.exit(1)

    print(f"Loading {LME_JSON} ...")
    with LME_JSON.open(encoding="utf-8") as f:
        data = json.load(f)

    if args.limit:
        data = data[: args.limit]
        print(f"  Limiting to {args.limit} questions")

    print(f"  {len(data)} questions loaded")

    # ── Connect to LanceDB ────────────────────────────────────────────────────
    print(f"\nConnecting to LanceDB at {LME_DB_PATH} ...")
    table = connect_lancedb(LME_DB_PATH)
    print("  Connected")

    # ── Initialize embedding model ────────────────────────────────────────────
    print("\nInitializing embedding model ...")
    from fastembed import TextEmbedding
    embed_model = TextEmbedding(model_name=EMBEDDING_MODEL)
    print("  Ready")

    # ── Build BM25 index (hybrid mode only) ───────────────────────────────────
    bm25_index, bm25_chunks = None, []
    if args.mode == "hybrid":
        bm25_index, bm25_chunks = build_bm25_index(table)

    # ── Evaluation loop ───────────────────────────────────────────────────────
    print(f"\nEvaluating {len(data)} questions (mode={args.mode}, top_k={args.top_k}) ...")

    try:
        from tqdm import tqdm
        question_iter = tqdm(data, desc="Evaluating")
    except ImportError:
        question_iter = data

    per_question = []
    latencies_ms = []

    for entry in question_iter:
        question_id = entry.get("question_id", "")
        question_text = entry.get("question", "")
        answer_session_ids = set(entry.get("answer_session_ids", []))

        if not question_text or not answer_session_ids:
            continue

        t0 = time.perf_counter()

        # Embed query
        q_vec = embed_query(embed_model, question_text)

        # Search
        if args.mode == "hybrid":
            chunks = search_hybrid(
                table=table,
                query_vector=q_vec,
                query_text=question_text,
                top_k=args.top_k,
                recall_n=args.recall_n,
                bm25_index=bm25_index,
                bm25_chunks=bm25_chunks,
            )
        else:
            chunks = search_vector(
                table=table,
                query_vector=q_vec,
                top_k=args.top_k,
                recall_n=args.recall_n,
            )

        latency_ms = (time.perf_counter() - t0) * 1000
        latencies_ms.append(latency_ms)

        # Compute metrics
        r10 = session_recall_at_k(answer_session_ids, chunks, k=10)
        r5 = session_recall_at_k(answer_session_ids, chunks, k=5)
        mrr_val = session_mrr(answer_session_ids, chunks)

        # First matched session rank (for diagnostics)
        first_match_rank = None
        for rank, chunk in enumerate(chunks, start=1):
            if chunk.get("session_id") in answer_session_ids:
                first_match_rank = rank
                break

        per_question.append({
            "question_id": question_id,
            "question_type": entry.get("question_type", ""),
            "question": question_text[:120],
            "answer_session_ids": list(answer_session_ids),
            "session_recall@10": r10,
            "session_recall@5": r5,
            "session_mrr": round(mrr_val, 4),
            "first_match_rank": first_match_rank,
            "latency_ms": round(latency_ms, 1),
        })

    # ── Aggregate metrics ─────────────────────────────────────────────────────
    n = len(per_question)
    if n == 0:
        print("ERROR: No questions evaluated.")
        sys.exit(1)

    avg_r10 = sum(q["session_recall@10"] for q in per_question) / n
    avg_r5 = sum(q["session_recall@5"] for q in per_question) / n
    avg_mrr = sum(q["session_mrr"] for q in per_question) / n
    p95_ms = float(np.percentile(latencies_ms, 95)) if latencies_ms else 0.0
    mean_ms = float(np.mean(latencies_ms)) if latencies_ms else 0.0

    metrics = {
        "session_recall@10": round(avg_r10, 4),
        "session_recall@5": round(avg_r5, 4),
        "session_mrr": round(avg_mrr, 4),
        "p95_latency_ms": round(p95_ms, 1),
        "mean_latency_ms": round(mean_ms, 1),
    }

    results = {
        "total_questions": n,
        "mode": args.mode,
        "top_k": args.top_k,
        "recall_n": args.recall_n,
        "metrics": metrics,
        "comparison_note": f"Patrick Recall@10 (own data): {PATRICK_OWN_RECALL10}",
        "per_question": per_question,
    }

    # ── Print summary ─────────────────────────────────────────────────────────
    print_summary_table(results)

    # ── Save results ──────────────────────────────────────────────────────────
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"Results saved to: {args.output}")


if __name__ == "__main__":
    main()
