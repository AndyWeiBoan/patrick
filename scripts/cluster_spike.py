"""Phase 6 Task 4 — HDBSCAN parameter tuning spike.

Usage:
    uv run python scripts/cluster_spike.py

Loads real Patrick chunk vectors, tests parameter combinations,
prints results for documenting in phase6.md.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def cosine_distance_stats(vectors: np.ndarray, n_sample: int = 2000) -> dict:
    """Sample pairwise cosine distance distribution in 384D."""
    rng = np.random.default_rng(42)
    N = len(vectors)
    idx = rng.choice(N, size=min(n_sample, N), replace=False)
    sample = vectors[idx]

    # Normalize to unit sphere → cosine dist = 1 - dot
    norms = np.linalg.norm(sample, axis=1, keepdims=True)
    sample_normed = sample / np.clip(norms, 1e-8, None)

    # Gram matrix → pairwise cosine similarities
    gram = sample_normed @ sample_normed.T
    np.fill_diagonal(gram, np.nan)
    distances = (1.0 - gram).ravel()
    distances = distances[~np.isnan(distances)]

    return {
        "mean": float(np.mean(distances)),
        "std": float(np.std(distances)),
        "min": float(np.min(distances)),
        "max": float(np.max(distances)),
        "p25": float(np.percentile(distances, 25)),
        "p50": float(np.percentile(distances, 50)),
        "p75": float(np.percentile(distances, 75)),
    }


def representative_texts(vectors: np.ndarray, texts: list[str], label: int,
                          labels: np.ndarray, top_k: int = 3) -> list[str]:
    """Return top_k texts closest to the centroid of a given cluster."""
    mask = labels == label
    cluster_vectors = vectors[mask]
    cluster_texts = [texts[i] for i, m in enumerate(mask) if m]
    if len(cluster_vectors) == 0:
        return []
    centroid = cluster_vectors.mean(axis=0)
    centroid /= (np.linalg.norm(centroid) + 1e-8)
    normed = cluster_vectors / (np.linalg.norm(cluster_vectors, axis=1, keepdims=True) + 1e-8)
    sims = normed @ centroid
    top_idx = np.argsort(sims)[::-1][:top_k]
    return [cluster_texts[i][:120] for i in top_idx]


def run_20d_pipeline(vectors, min_cluster_size, min_samples, label=""):
    """Two-pass UMAP pipeline: 384D → 20D → HDBSCAN + 384D → 2D."""
    import hdbscan
    import umap

    N = len(vectors)
    n_neighbors = min(15, N - 1)
    n_components_cluster = min(20, N - 1)

    t0 = time.time()
    reducer_20 = umap.UMAP(n_components=n_components_cluster, metric="cosine",
                           n_neighbors=n_neighbors, min_dist=0.1,
                           init="random", random_state=42)
    X_20 = reducer_20.fit_transform(vectors)

    clusterer = hdbscan.HDBSCAN(min_cluster_size=min_cluster_size,
                                 min_samples=min_samples, metric="euclidean")
    labels = clusterer.fit_predict(X_20).astype(np.int32)
    elapsed = time.time() - t0

    n_clusters = int(np.max(labels) + 1) if np.any(labels >= 0) else 0
    noise = int(np.sum(labels == -1))
    return labels, n_clusters, noise, elapsed


def run_384d_baseline(vectors, min_cluster_size, min_samples):
    """Direct HDBSCAN on 384D (no UMAP). Baseline for high-dim curse."""
    import hdbscan

    t0 = time.time()
    # Normalize first (cosine distance via euclidean on unit sphere)
    normed = vectors / (np.linalg.norm(vectors, axis=1, keepdims=True) + 1e-8)
    clusterer = hdbscan.HDBSCAN(min_cluster_size=min_cluster_size,
                                 min_samples=min_samples, metric="euclidean")
    labels = clusterer.fit_predict(normed).astype(np.int32)
    elapsed = time.time() - t0

    n_clusters = int(np.max(labels) + 1) if np.any(labels >= 0) else 0
    noise = int(np.sum(labels == -1))
    return labels, n_clusters, noise, elapsed


def main():
    from patrick.storage import storage
    storage.initialize()

    import pandas as pd
    print("Loading chunks from LanceDB...")
    df = storage._chunks.to_pandas()
    print(f"Total chunks: {len(df)}")

    # Stack vectors
    vectors = np.stack(df["vector"].values).astype(np.float32)
    texts = df["text"].tolist()
    N = len(vectors)
    print(f"Vector matrix shape: {vectors.shape}")

    # ── 1. Cosine distance distribution in 384D ────────────────────────────
    print("\n" + "="*60)
    print("1. 384D PAIRWISE COSINE DISTANCE DISTRIBUTION (sample n=2000)")
    print("="*60)
    stats = cosine_distance_stats(vectors)
    print(f"  mean:  {stats['mean']:.4f}")
    print(f"  std:   {stats['std']:.4f}")
    print(f"  min:   {stats['min']:.4f}")
    print(f"  p25:   {stats['p25']:.4f}")
    print(f"  p50:   {stats['p50']:.4f}")
    print(f"  p75:   {stats['p75']:.4f}")
    print(f"  max:   {stats['max']:.4f}")
    narrow = stats["std"] < 0.05
    print(f"\n  → Distribution {'NARROW (std<0.05) — high-dim curse confirmed' if narrow else 'wide (std>=0.05)'}")

    # ── 2. Grid search ─────────────────────────────────────────────────────
    print("\n" + "="*60)
    print("2. PARAMETER GRID SEARCH")
    print("="*60)

    configs = [
        dict(min_cluster_size=3,  min_samples=2,  pipeline="20D"),
        dict(min_cluster_size=5,  min_samples=3,  pipeline="20D"),
        dict(min_cluster_size=10, min_samples=3,  pipeline="20D"),
        dict(min_cluster_size=10, min_samples=5,  pipeline="20D"),
        dict(min_cluster_size=5,  min_samples=3,  pipeline="384D"),  # baseline
    ]

    results = []
    for cfg in configs:
        mcs = cfg["min_cluster_size"]
        ms  = cfg["min_samples"]
        pipeline = cfg["pipeline"]

        print(f"\n--- min_cluster_size={mcs}, min_samples={ms}, pipeline={pipeline} ---")
        if pipeline == "20D":
            labels, n_clusters, noise, elapsed = run_20d_pipeline(vectors, mcs, ms)
        else:
            labels, n_clusters, noise, elapsed = run_384d_baseline(vectors, mcs, ms)

        noise_ratio = noise / N
        print(f"  clusters: {n_clusters}, noise: {noise}/{N} ({100*noise_ratio:.1f}%), time: {elapsed:.1f}s")

        # Largest cluster representative texts
        if n_clusters > 0:
            cluster_sizes = [(c, int(np.sum(labels == c))) for c in range(n_clusters)]
            cluster_sizes.sort(key=lambda x: -x[1])
            biggest_id, biggest_size = cluster_sizes[0]
            print(f"  Largest cluster: #{biggest_id} ({biggest_size} chunks)")
            reps = representative_texts(vectors, texts, biggest_id, labels, top_k=3)
            for i, t in enumerate(reps, 1):
                print(f"    [{i}] {t!r}")

        results.append({
            "min_cluster_size": mcs,
            "min_samples": ms,
            "pipeline": pipeline,
            "n_clusters": n_clusters,
            "noise": noise,
            "noise_ratio": noise_ratio,
            "elapsed_s": round(elapsed, 1),
        })

    # ── 3. Summary table ───────────────────────────────────────────────────
    print("\n" + "="*60)
    print("3. SUMMARY TABLE")
    print("="*60)
    print(f"{'mcs':>4} {'ms':>3} {'pipeline':>6} | {'clusters':>8} {'noise':>6} {'noise%':>7} {'time':>6}")
    print("-" * 50)
    for r in results:
        print(f"{r['min_cluster_size']:>4} {r['min_samples']:>3} {r['pipeline']:>6} | "
              f"{r['n_clusters']:>8} {r['noise']:>6} {100*r['noise_ratio']:>6.1f}% {r['elapsed_s']:>5.1f}s")

    # ── 4. 20D vs 384D comparison ──────────────────────────────────────────
    best_20d = next(r for r in results if r["pipeline"] == "20D" and
                    r["min_cluster_size"] == 5 and r["min_samples"] == 3)
    baseline = next(r for r in results if r["pipeline"] == "384D")
    print("\n" + "="*60)
    print("4. 20D PIPELINE vs 384D BASELINE (min_cluster_size=5, min_samples=3)")
    print("="*60)
    print(f"  20D pipeline:  {best_20d['n_clusters']} clusters, {100*best_20d['noise_ratio']:.1f}% noise")
    print(f"  384D baseline: {baseline['n_clusters']} clusters, {100*baseline['noise_ratio']:.1f}% noise")
    improvement = baseline["noise_ratio"] - best_20d["noise_ratio"]
    print(f"  Noise reduction from UMAP: {100*improvement:+.1f}pp")

    # ── 5. Recommendation ─────────────────────────────────────────────────
    print("\n" + "="*60)
    print("5. RECOMMENDED PARAMETERS")
    print("="*60)
    # Pick the 20D config with noise_ratio < 0.5 and most clusters (best signal)
    candidates = [r for r in results if r["pipeline"] == "20D" and r["noise_ratio"] < 0.6]
    if candidates:
        best = max(candidates, key=lambda r: r["n_clusters"])
        print(f"  CLUSTER_MIN_CLUSTER_SIZE = {best['min_cluster_size']}")
        print(f"  CLUSTER_MIN_SAMPLES      = {best['min_samples']}")
        print(f"  (gives {best['n_clusters']} clusters, {100*best['noise_ratio']:.1f}% noise)")
    else:
        print("  All configs have >60% noise — consider more data or smaller min_cluster_size")

    print("\nDone.")


if __name__ == "__main__":
    main()
