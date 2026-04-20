#!/usr/bin/env python3
"""Eval harness for Patrick memory system — computes Recall@10, nDCG@10, MRR.

Usage:
    python scripts/eval.py                          # vector mode, print results
    python scripts/eval.py --mode hybrid            # hybrid search mode
    python scripts/eval.py --output results/p2.json # save JSON artifact
    python scripts/eval.py --baseline results/p1.json  # compare vs baseline
    python scripts/eval.py --search-only --query "text"  # inspect search results

This script connects directly to the running patrick server via MCP tool calls,
or can bypass the server and call storage/embedding layers directly (--direct).

Frozen benchmark: tests/eval/queries.jsonl
Ground truth: relevant_chunk_ids populated by human annotators (see README).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import time
from pathlib import Path
from typing import Any

# ── Path setup ───────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

QUERIES_FILE = REPO_ROOT / "tests" / "eval" / "queries.jsonl"
RESULTS_DIR = REPO_ROOT / "results"


# ── Metric functions ──────────────────────────────────────────────────────────

def recall_at_k(relevant: set[str], retrieved: list[str], k: int = 10) -> float:
    """Recall@K: fraction of relevant items found in top-K retrieved."""
    if not relevant:
        return 0.0
    top_k = set(retrieved[:k])
    return len(relevant & top_k) / len(relevant)


def ndcg_at_k(relevant: set[str], retrieved: list[str], k: int = 10) -> float:
    """nDCG@K: normalised discounted cumulative gain at rank K.

    Binary relevance: 1 if chunk_id in relevant, 0 otherwise.
    """
    if not relevant:
        return 0.0
    dcg = 0.0
    for rank, chunk_id in enumerate(retrieved[:k], start=1):
        if chunk_id in relevant:
            dcg += 1.0 / math.log2(rank + 1)
    # Ideal DCG: all relevant items at top positions
    ideal_k = min(len(relevant), k)
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, ideal_k + 1))
    return dcg / idcg if idcg > 0 else 0.0


def mrr(relevant: set[str], retrieved: list[str]) -> float:
    """MRR: reciprocal rank of the first relevant result."""
    for rank, chunk_id in enumerate(retrieved, start=1):
        if chunk_id in relevant:
            return 1.0 / rank
    return 0.0


# ── Search wrapper ────────────────────────────────────────────────────────────

async def search_direct(
    query: str,
    mode: str = "vector",
    top_k: int = 10,
) -> list[str]:
    """Call storage layer directly (no server needed).

    Returns list of chunk_ids in ranked order.
    """
    from patrick.embedding import provider
    from patrick.storage import storage

    if not provider._initialized:
        provider.initialize()
    if not storage._initialized:
        storage.initialize()

    vectors = await provider.embed_async([query])
    q_vec = vectors[0]

    if mode == "hybrid":
        from patrick.config import HYBRID_RECALL_N, RERANK_ENABLED
        recall_n = max(top_k, HYBRID_RECALL_N)
        chunks = storage.search_chunks_hybrid(
            query_vector=q_vec,
            query_text=query,
            top_k=recall_n,
            recall_n=recall_n,
        )
        if RERANK_ENABLED and chunks:
            formatted = [{"chunk_id": c.get("chunk_id", ""), "text": c.get("text", "")} for c in chunks]
            reranked = await provider.rerank_async(query=query, candidates=formatted, top_k=top_k)
            return [c["chunk_id"] for c in reranked]
        return [c.get("chunk_id", "") for c in chunks[:top_k]]
    else:
        chunks = storage.search_chunks(query_vector=q_vec, top_k=top_k)
        return [c.get("chunk_id", "") for c in chunks]


# ── Main eval loop ────────────────────────────────────────────────────────────

async def run_eval(
    queries: list[dict],
    mode: str = "vector",
    top_k: int = 10,
    verbose: bool = False,
) -> dict[str, Any]:
    """Run eval over all queries with annotated ground truth.

    Returns dict with per-query scores and aggregate metrics.
    Queries without ground truth (empty relevant_chunk_ids) are skipped
    with a warning — they don't count toward averages.
    """
    annotated = [q for q in queries if q.get("relevant_chunk_ids")]
    unannotated = [q for q in queries if not q.get("relevant_chunk_ids")]

    if unannotated:
        print(f"WARNING: {len(unannotated)} queries have no ground truth and will be skipped.")
        print("         Annotate relevant_chunk_ids in tests/eval/queries.jsonl first.")
        print()

    if not annotated:
        print("ERROR: No annotated queries found. Baseline cannot be computed.")
        print("       See tests/eval/README.md for annotation instructions.")
        return {
            "error": "no_annotated_queries",
            "total_queries": len(queries),
            "annotated": 0,
            "metrics": {},
        }

    per_query: list[dict] = []
    t_start = time.perf_counter()

    for q in annotated:
        qid = q["id"]
        query_text = q["query"]
        relevant = set(q["relevant_chunk_ids"])

        qt0 = time.perf_counter()
        retrieved = await search_direct(query=query_text, mode=mode, top_k=top_k)
        qt_ms = round((time.perf_counter() - qt0) * 1000, 1)

        r_k = recall_at_k(relevant, retrieved, k=top_k)
        n_k = ndcg_at_k(relevant, retrieved, k=top_k)
        m = mrr(relevant, retrieved)

        per_query.append({
            "id": qid,
            "query": query_text,
            "lang": q.get("lang", ""),
            "category": q.get("category", ""),
            f"recall@{top_k}": round(r_k, 4),
            f"ndcg@{top_k}": round(n_k, 4),
            "mrr": round(m, 4),
            "latency_ms": qt_ms,
        })

        if verbose:
            print(
                f"  [{qid}] R@{top_k}={r_k:.3f} nDCG@{top_k}={n_k:.3f} MRR={m:.3f}  "
                f"({qt_ms}ms) | {query_text[:60]}"
            )

    total_ms = round((time.perf_counter() - t_start) * 1000, 1)
    n = len(per_query)

    avg_recall = sum(r[f"recall@{top_k}"] for r in per_query) / n
    avg_ndcg = sum(r[f"ndcg@{top_k}"] for r in per_query) / n
    avg_mrr = sum(r["mrr"] for r in per_query) / n
    latencies = sorted(r["latency_ms"] for r in per_query)
    p95_ms = latencies[int(0.95 * len(latencies)) - 1] if latencies else 0.0

    metrics = {
        f"recall@{top_k}": round(avg_recall, 4),
        f"ndcg@{top_k}": round(avg_ndcg, 4),
        "mrr": round(avg_mrr, 4),
        "p95_latency_ms": p95_ms,
        "total_latency_ms": total_ms,
    }

    return {
        "mode": mode,
        "top_k": top_k,
        "total_queries": len(queries),
        "annotated": n,
        "skipped": len(unannotated),
        "metrics": metrics,
        "per_query": per_query,
    }


def compare_vs_baseline(results: dict, baseline_path: Path) -> None:
    """Print diff table comparing current results against a baseline JSON."""
    try:
        baseline = json.loads(baseline_path.read_text())
    except Exception as e:
        print(f"Could not load baseline from {baseline_path}: {e}")
        return

    curr_m = results.get("metrics", {})
    base_m = baseline.get("metrics", {})
    top_k = results.get("top_k", 10)

    keys = [f"recall@{top_k}", f"ndcg@{top_k}", "mrr", "p95_latency_ms"]
    print(f"\n{'Metric':<20} {'Baseline':>12} {'Current':>12} {'Delta':>12} {'Status':>10}")
    print("-" * 70)
    for key in keys:
        b = base_m.get(key, 0.0)
        c = curr_m.get(key, 0.0)
        delta = c - b
        # For latency, lower is better; for others, higher is better
        if key == "p95_latency_ms":
            status = "OK" if delta <= 0 else ("WARN" if delta < 50 else "FAIL")
            symbol = "↓" if delta < 0 else ("→" if delta == 0 else "↑")
        else:
            pct_change = (delta / b * 100) if b > 0 else 0.0
            if key.startswith("recall") and pct_change >= 20:
                status = "KPI MET"
            elif key.startswith("ndcg") and pct_change >= 15:
                status = "KPI MET"
            else:
                status = f"{pct_change:+.1f}%"
            symbol = "↑" if delta > 0 else ("→" if delta == 0 else "↓")
        print(f"  {key:<18} {b:>12.4f} {c:>12.4f} {delta:>+11.4f}{symbol} {status:>10}")
    print()


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Patrick eval harness — compute Recall@10, nDCG@10, MRR"
    )
    parser.add_argument(
        "--mode",
        choices=["vector", "hybrid"],
        default="vector",
        help="Search mode (default: vector)",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=10,
        metavar="K",
        help="Rank cutoff (default: 10)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        metavar="FILE",
        help="Save results as JSON to this path",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        metavar="FILE",
        help="Compare against this baseline JSON file",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print per-query scores",
    )
    parser.add_argument(
        "--search-only",
        action="store_true",
        help="Run a single search and print results (use with --query)",
    )
    parser.add_argument(
        "--query",
        type=str,
        metavar="TEXT",
        help="Query text for --search-only mode",
    )
    args = parser.parse_args()

    # ── Search-only mode ──────────────────────────────────────────────────────
    if args.search_only:
        if not args.query:
            print("ERROR: --query TEXT required with --search-only")
            sys.exit(1)

        async def _search() -> None:
            from patrick.embedding import provider
            from patrick.storage import storage

            if not provider._initialized:
                provider.initialize()
            if not storage._initialized:
                storage.initialize()

            retrieved = await search_direct(
                query=args.query, mode=args.mode, top_k=args.top_k
            )
            print(f"\nTop-{args.top_k} results for: {args.query!r} (mode={args.mode})")
            for rank, chunk_id in enumerate(retrieved, start=1):
                print(f"  {rank:2}. {chunk_id}")

        asyncio.run(_search())
        return

    # ── Full eval mode ────────────────────────────────────────────────────────
    if not QUERIES_FILE.exists():
        print(f"ERROR: Queries file not found: {QUERIES_FILE}")
        sys.exit(1)

    queries = []
    with QUERIES_FILE.open() as f:
        for line in f:
            line = line.strip()
            if line:
                queries.append(json.loads(line))

    print(f"Patrick Eval Harness — {len(queries)} queries, mode={args.mode}, top_k={args.top_k}")
    print("=" * 70)

    results = asyncio.run(
        run_eval(queries=queries, mode=args.mode, top_k=args.top_k, verbose=args.verbose)
    )

    if "error" in results:
        sys.exit(1)

    # Print summary
    m = results["metrics"]
    k = args.top_k
    print(f"\nResults ({results['annotated']} annotated queries, {results['skipped']} skipped):")
    print(f"  Recall@{k}   : {m[f'recall@{k}']:.4f}")
    print(f"  nDCG@{k}     : {m[f'ndcg@{k}']:.4f}")
    print(f"  MRR         : {m['mrr']:.4f}")
    print(f"  P95 latency : {m['p95_latency_ms']:.1f} ms")

    # Compare vs baseline if provided
    if args.baseline:
        compare_vs_baseline(results, args.baseline)

    # Save output if requested
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(results, indent=2, ensure_ascii=False))
        print(f"\nResults saved to: {args.output}")


if __name__ == "__main__":
    main()
