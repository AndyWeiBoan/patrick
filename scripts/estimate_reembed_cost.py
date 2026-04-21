#!/usr/bin/env python3
"""Estimate re-embedding cost for a full database rebuild.

Both embedding and reranking run locally (no API costs).
This script benchmarks actual throughput on the current machine and
projects total rebuild time based on the chunk count in LanceDB.

Usage:
    python scripts/estimate_reembed_cost.py
    python scripts/estimate_reembed_cost.py --sample-size 200
    python scripts/estimate_reembed_cost.py --output results/reembed_cost.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))


def count_chunks() -> int:
    """Return total number of chunks in LanceDB."""
    from patrick.storage import storage

    if not storage._initialized:
        storage.initialize()

    df = storage._chunks.to_pandas()
    return len(df)


def benchmark_embedding(sample_size: int = 100) -> dict:
    """Benchmark local embedding throughput.

    Generates dummy text chunks and measures embed time per chunk.
    """
    from patrick.config import CHUNK_SIZE, EMBEDDING_MODEL_FASTEMBED
    from patrick.embedding import provider

    if not provider._initialized:
        provider.initialize()

    # Generate realistic-length dummy texts (CHUNK_SIZE tokens ~ 300-600 chars)
    dummy_texts = [
        f"This is a sample text chunk number {i} used for benchmarking the "
        f"embedding model throughput. It contains enough words to approximate "
        f"a real chunk of around {CHUNK_SIZE} tokens with mixed content."
        for i in range(sample_size)
    ]

    # Warm up (first call loads model if not loaded)
    _ = list(provider._model.embed(dummy_texts[:2]))

    # Benchmark
    t0 = time.perf_counter()
    _ = list(provider._model.embed(dummy_texts))
    elapsed = time.perf_counter() - t0

    return {
        "model": EMBEDDING_MODEL_FASTEMBED,
        "runtime": "fastembed (ONNX)",
        "sample_size": sample_size,
        "total_seconds": round(elapsed, 3),
        "per_chunk_ms": round(elapsed / sample_size * 1000, 2),
        "chunks_per_second": round(sample_size / elapsed, 1),
    }


def benchmark_rerank(sample_size: int = 50) -> dict | None:
    """Benchmark local cross-encoder rerank throughput.

    Measures time to score query-candidate pairs.
    Returns None if cross-encoder is unavailable.
    """
    from patrick.config import RERANK_ENABLED, RERANK_MODEL
    from patrick.embedding import provider

    if not RERANK_ENABLED:
        return {"skipped": True, "reason": "RERANK_ENABLED=False"}

    if not provider._initialized:
        provider.initialize()

    dummy_candidates = [
        {"chunk_id": f"c{i}", "text": f"Candidate document {i} with some content for reranking benchmark."}
        for i in range(sample_size)
    ]
    query = "benchmark query for measuring rerank throughput"

    # Warm up (loads model on first call)
    try:
        provider.rerank_sync(query=query, candidates=dummy_candidates[:2], top_k=2)
    except Exception as e:
        return {"skipped": True, "reason": f"rerank unavailable: {e}"}

    # Benchmark
    t0 = time.perf_counter()
    provider.rerank_sync(query=query, candidates=dummy_candidates, top_k=sample_size)
    elapsed = time.perf_counter() - t0

    return {
        "model": RERANK_MODEL,
        "runtime": "sentence-transformers (CrossEncoder)",
        "sample_size": sample_size,
        "total_seconds": round(elapsed, 3),
        "per_pair_ms": round(elapsed / sample_size * 1000, 2),
        "pairs_per_second": round(sample_size / elapsed, 1),
    }


def estimate_rebuild(
    chunk_count: int,
    embed_bench: dict,
    rerank_bench: dict | None,
) -> dict:
    """Project total rebuild time from benchmark results."""
    embed_time_s = chunk_count * embed_bench["per_chunk_ms"] / 1000
    result = {
        "total_chunks": chunk_count,
        "embed_estimate": {
            "total_seconds": round(embed_time_s, 1),
            "total_minutes": round(embed_time_s / 60, 2),
            "description": (
                f"{chunk_count} chunks x {embed_bench['per_chunk_ms']}ms/chunk "
                f"= {embed_time_s:.1f}s ({embed_time_s / 60:.1f}min)"
            ),
        },
        "api_cost": "$0.00 (local model, no API calls)",
    }

    if rerank_bench and not rerank_bench.get("skipped"):
        # Rerank is per-query, not per-rebuild; estimate for typical eval run
        typical_queries = 30
        typical_candidates = 50  # HYBRID_RECALL_N
        rerank_pairs = typical_queries * typical_candidates
        rerank_time_s = rerank_pairs * rerank_bench["per_pair_ms"] / 1000
        result["rerank_estimate_per_eval"] = {
            "queries": typical_queries,
            "candidates_per_query": typical_candidates,
            "total_pairs": rerank_pairs,
            "total_seconds": round(rerank_time_s, 1),
            "description": (
                f"{typical_queries} queries x {typical_candidates} candidates "
                f"x {rerank_bench['per_pair_ms']}ms/pair = {rerank_time_s:.1f}s"
            ),
        }

    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Estimate re-embedding cost for full database rebuild"
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=100,
        help="Number of sample chunks for benchmarking (default: 100)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        metavar="FILE",
        help="Save results as JSON to this path",
    )
    args = parser.parse_args()

    print("Patrick Re-embed Cost Estimator")
    print("=" * 60)

    # 1. Count chunks
    print("\n1. Counting chunks in LanceDB...")
    chunk_count = count_chunks()
    print(f"   Total chunks: {chunk_count}")

    # 2. Benchmark embedding
    print(f"\n2. Benchmarking embedding ({args.sample_size} samples)...")
    embed_bench = benchmark_embedding(sample_size=args.sample_size)
    print(f"   Model: {embed_bench['model']}")
    print(f"   Throughput: {embed_bench['chunks_per_second']} chunks/s ({embed_bench['per_chunk_ms']}ms/chunk)")

    # 3. Benchmark rerank
    print(f"\n3. Benchmarking cross-encoder rerank...")
    rerank_bench = benchmark_rerank(sample_size=min(args.sample_size, 50))
    if rerank_bench and not rerank_bench.get("skipped"):
        print(f"   Model: {rerank_bench['model']}")
        print(f"   Throughput: {rerank_bench['pairs_per_second']} pairs/s ({rerank_bench['per_pair_ms']}ms/pair)")
    else:
        reason = rerank_bench.get("reason", "unknown") if rerank_bench else "N/A"
        print(f"   Skipped: {reason}")

    # 4. Estimate rebuild
    print("\n4. Rebuild time estimate:")
    estimate = estimate_rebuild(chunk_count, embed_bench, rerank_bench)
    e = estimate["embed_estimate"]
    print(f"   Embedding: {e['description']}")
    print(f"   API cost: {estimate['api_cost']}")
    if "rerank_estimate_per_eval" in estimate:
        r = estimate["rerank_estimate_per_eval"]
        print(f"   Rerank (per eval run): {r['description']}")

    # 5. Save output
    full_result = {
        "chunk_count": chunk_count,
        "embedding_benchmark": embed_bench,
        "rerank_benchmark": rerank_bench,
        "rebuild_estimate": estimate,
    }

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(full_result, indent=2, ensure_ascii=False))
        print(f"\nResults saved to: {args.output}")

    print()


if __name__ == "__main__":
    main()
