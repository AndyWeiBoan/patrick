"""Clustering engine: HDBSCAN + two-pass UMAP pipeline.

Phase 6 addition. Architecture (定案):
  Pass 1 (聚類用):   UMAP 384D → 20D (metric='cosine'), result → HDBSCAN (metric='euclidean')
  Pass 2 (視覺化用): UMAP 384D → 2D  (metric='cosine'), stored as umap_x / umap_y

All computation is synchronous (CPU-bound). Callers should wrap in
asyncio.get_event_loop().run_in_executor() when calling from async context.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

from .config import (
    CLUSTER_MIN_CLUSTER_SIZE,
    CLUSTER_MIN_SAMPLES,
    CLUSTER_UMAP_MIN_DIST,
    CLUSTER_UMAP_N_NEIGHBORS,
    CLUSTER_UMAP_RANDOM_STATE,
)

logger = logging.getLogger(__name__)


@dataclass
class ClusterResult:
    """Output of ClusteringEngine.compute()."""

    labels: np.ndarray          # (N,) int32; -1 = noise, >=0 = cluster id
    umap_coords: np.ndarray     # (N, 2) float32; 2D positions for visualization
    n_clusters: int             # distinct cluster count, NOT counting noise (-1)
    noise_count: int            # number of points labelled -1
    noise_ratio: float          # noise_count / N  (0.0 if N == 0)

    # Convenience empty result factory
    @classmethod
    def empty(cls) -> "ClusterResult":
        return cls(
            labels=np.array([], dtype=np.int32),
            umap_coords=np.empty((0, 2), dtype=np.float32),
            n_clusters=0,
            noise_count=0,
            noise_ratio=0.0,
        )


class ClusteringEngine:
    """Stateless engine — instantiate once and call compute() as needed."""

    def compute(
        self,
        vectors: np.ndarray,          # (N, 384) float32 or float64
        min_cluster_size: int = CLUSTER_MIN_CLUSTER_SIZE,
        min_samples: int = CLUSTER_MIN_SAMPLES,
        umap_n_neighbors: int = CLUSTER_UMAP_N_NEIGHBORS,
        umap_min_dist: float = CLUSTER_UMAP_MIN_DIST,
    ) -> ClusterResult:
        """Run two-pass UMAP + HDBSCAN pipeline.

        Args:
            vectors: (N, 384) embedding matrix.
            min_cluster_size: HDBSCAN min_cluster_size.
            min_samples: HDBSCAN min_samples.
            umap_n_neighbors: UMAP n_neighbors (clamped to N-1 if needed).
            umap_min_dist: UMAP min_dist.

        Returns:
            ClusterResult with labels, 2D coords, and summary stats.
        """
        import hdbscan  # type: ignore[import]
        import umap     # type: ignore[import]

        vectors = np.asarray(vectors, dtype=np.float32)
        N = len(vectors)

        # ── Edge cases ────────────────────────────────────────────────────────
        if N == 0:
            logger.debug("ClusteringEngine.compute: N=0, returning empty result")
            return ClusterResult.empty()

        if N == 1:
            logger.debug("ClusteringEngine.compute: N=1, labelling as noise")
            return ClusterResult(
                labels=np.array([-1], dtype=np.int32),
                umap_coords=np.zeros((1, 2), dtype=np.float32),
                n_clusters=0,
                noise_count=1,
                noise_ratio=1.0,
            )

        # Clamp n_neighbors — UMAP requires n_neighbors < N
        n_neighbors = min(umap_n_neighbors, N - 1)
        if n_neighbors != umap_n_neighbors:
            logger.debug(
                "ClusteringEngine.compute: clamped n_neighbors %d → %d (N=%d)",
                umap_n_neighbors, n_neighbors, N,
            )

        # ── Pass 1: 384D → 20D for HDBSCAN ───────────────────────────────────
        # n_components must be < N - 1 for spectral init (k = n_components + 1 < N).
        # We use init='random' to avoid the spectral constraint entirely and stay
        # deterministic via random_state=42 regardless of N.
        logger.debug("ClusteringEngine: UMAP pass 1 (384D → 20D), N=%d", N)
        n_components_cluster = min(20, N - 1)   # clamp for tiny inputs
        reducer_20 = umap.UMAP(
            n_components=n_components_cluster,
            metric="cosine",
            n_neighbors=n_neighbors,
            min_dist=umap_min_dist,
            init="random",        # avoids spectral init k >= N crash for small N
            random_state=CLUSTER_UMAP_RANDOM_STATE,
        )
        X_20 = reducer_20.fit_transform(vectors)

        # ── HDBSCAN in 20D space ──────────────────────────────────────────────
        logger.debug(
            "ClusteringEngine: HDBSCAN (min_cluster_size=%d, min_samples=%d)",
            min_cluster_size, min_samples,
        )
        clusterer = hdbscan.HDBSCAN(
            min_cluster_size=min_cluster_size,
            min_samples=min_samples,
            metric="euclidean",
        )
        labels = clusterer.fit_predict(X_20).astype(np.int32)

        # ── Pass 2: 384D → 2D for visualization ──────────────────────────────
        logger.debug("ClusteringEngine: UMAP pass 2 (384D → 2D), N=%d", N)
        reducer_2 = umap.UMAP(
            n_components=2,
            metric="cosine",
            n_neighbors=n_neighbors,
            min_dist=umap_min_dist,
            init="random",        # consistent with pass 1
            random_state=CLUSTER_UMAP_RANDOM_STATE,
        )
        umap_coords = reducer_2.fit_transform(vectors).astype(np.float32)

        # ── Stats ─────────────────────────────────────────────────────────────
        noise_count = int(np.sum(labels == -1))
        n_clusters = int(np.max(labels) + 1) if np.any(labels >= 0) else 0

        logger.info(
            "ClusteringEngine: N=%d → %d clusters, %d noise (%.1f%%)",
            N, n_clusters, noise_count, 100.0 * noise_count / N,
        )

        return ClusterResult(
            labels=labels,
            umap_coords=umap_coords,
            n_clusters=n_clusters,
            noise_count=noise_count,
            noise_ratio=noise_count / N,
        )


# Module-level singleton
engine = ClusteringEngine()
