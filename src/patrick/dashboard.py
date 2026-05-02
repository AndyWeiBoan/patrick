"""Dashboard API handlers — /dashboard/api/* endpoints (Phase 6).

Route map:
    GET  /dashboard/api/projects             → list projects with session counts
    GET  /dashboard/api/sessions             → sessions for a project
    GET  /dashboard/api/clusters             → UMAP scatter data for a project/session
    GET  /dashboard/api/cluster-config       → per-project cluster params
    PUT  /dashboard/api/cluster-config       → save per-project cluster params
    POST /dashboard/api/recluster            → trigger background re-clustering (202)
    GET  /dashboard/api/recluster-status     → running state + last_clustered_at
    GET  /dashboard/api/chunk/{chunk_id}     → full chunk text + session summary
"""

from __future__ import annotations

import asyncio
import logging
import re

from starlette.requests import Request
from starlette.responses import JSONResponse

from .config import (
    CLUSTER_MIN_CLUSTER_SIZE,
    CLUSTER_MIN_SAMPLES,
    CLUSTER_UMAP_MIN_DIST,
    CLUSTER_UMAP_N_NEIGHBORS,
)
from .storage import storage

logger = logging.getLogger(__name__)

# UUID v4 validation — safe to interpolate in LanceDB WHERE clauses
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _valid_uuid(value: str) -> bool:
    return bool(_UUID_RE.match(value))


# ── In-memory recluster state ─────────────────────────────────────────────────
# { project_path: True } while clustering is in progress; key absent or False = idle.
_recluster_running: dict[str, bool] = {}


# ── Route handlers ────────────────────────────────────────────────────────────


async def dashboard_projects(request: Request) -> JSONResponse:
    """GET /dashboard/api/projects — all projects with session counts."""
    loop = asyncio.get_running_loop()
    projects = await loop.run_in_executor(None, storage.get_project_stats)
    return JSONResponse({"projects": projects})


async def dashboard_sessions(request: Request) -> JSONResponse:
    """GET /dashboard/api/sessions?project_path=... — sessions for a project."""
    project_path = request.query_params.get("project_path", "").strip()
    if not project_path:
        return JSONResponse({"error": "project_path is required"}, status_code=400)

    loop = asyncio.get_running_loop()
    sessions = await loop.run_in_executor(
        None, storage.get_sessions_for_project, project_path
    )
    return JSONResponse({"project_path": project_path, "sessions": sessions})


async def dashboard_clusters(request: Request) -> JSONResponse:
    """GET /dashboard/api/clusters?project_path=...&session_id=... — UMAP scatter data."""
    project_path = request.query_params.get("project_path", "").strip()
    session_id = request.query_params.get("session_id", "").strip() or None

    if not project_path:
        return JSONResponse({"error": "project_path is required"}, status_code=400)
    if session_id and not _valid_uuid(session_id):
        return JSONResponse({"error": "session_id must be a UUID"}, status_code=400)

    loop = asyncio.get_running_loop()
    points = await loop.run_in_executor(
        None, storage.get_cluster_data, project_path, session_id
    )

    # Derive aggregate stats from points
    cluster_ids = {p["cluster_id"] for p in points if p.get("cluster_id") is not None and p["cluster_id"] >= 0}
    noise_count = sum(1 for p in points if p.get("cluster_id") == -1)

    return JSONResponse({
        "project_path": project_path,
        "total_chunks": len(points),
        "n_clusters": len(cluster_ids),
        "noise_count": noise_count,
        "points": points,
    })


async def dashboard_cluster_config(request: Request) -> JSONResponse:
    """GET/PUT /dashboard/api/cluster-config — get or save per-project cluster params."""
    if request.method == "GET":
        project_path = request.query_params.get("project_path", "").strip()
        if not project_path:
            return JSONResponse({"error": "project_path is required"}, status_code=400)

        loop = asyncio.get_running_loop()
        cfg = await loop.run_in_executor(None, storage.get_cluster_config, project_path)

        if cfg is None:
            # Return global defaults when no per-project config exists
            cfg = {
                "project_path": project_path,
                "min_cluster_size": CLUSTER_MIN_CLUSTER_SIZE,
                "min_samples": CLUSTER_MIN_SAMPLES,
                "umap_n_neighbors": CLUSTER_UMAP_N_NEIGHBORS,
                "umap_min_dist": CLUSTER_UMAP_MIN_DIST,
                "last_clustered_at": None,
            }
        return JSONResponse(cfg)

    elif request.method == "PUT":
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid JSON"}, status_code=400)

        project_path = (body.get("project_path") or "").strip()
        if not project_path:
            return JSONResponse({"error": "project_path is required"}, status_code=400)

        params: dict = {}
        for key in ("min_cluster_size", "min_samples", "umap_n_neighbors"):
            if key in body:
                try:
                    params[key] = int(body[key])
                except (TypeError, ValueError):
                    return JSONResponse({"error": f"{key} must be an integer"}, status_code=400)
        if "umap_min_dist" in body:
            try:
                params["umap_min_dist"] = float(body["umap_min_dist"])
            except (TypeError, ValueError):
                return JSONResponse({"error": "umap_min_dist must be a number"}, status_code=400)

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, lambda: storage.upsert_cluster_config(project_path, **params)
        )
        return JSONResponse({"status": "saved", "project_path": project_path})

    return JSONResponse({"error": "method not allowed"}, status_code=405)


async def _run_recluster(project_path: str) -> None:
    """Background coroutine: compute HDBSCAN+UMAP and write results to DB."""
    try:
        import numpy as np

        from .clustering import ClusteringEngine

        loop = asyncio.get_running_loop()

        # Load per-project config (or fall back to globals)
        cfg = await loop.run_in_executor(None, storage.get_cluster_config, project_path)
        min_cluster_size = cfg["min_cluster_size"] if cfg else CLUSTER_MIN_CLUSTER_SIZE
        min_samples = cfg["min_samples"] if cfg else CLUSTER_MIN_SAMPLES
        umap_n_neighbors = cfg["umap_n_neighbors"] if cfg else CLUSTER_UMAP_N_NEIGHBORS
        umap_min_dist = cfg["umap_min_dist"] if cfg else CLUSTER_UMAP_MIN_DIST

        # Load all project chunks (includes vectors)
        chunks = await loop.run_in_executor(None, storage.get_project_chunks, project_path)
        if not chunks:
            logger.warning("recluster: no chunks found for %s", project_path)
            return

        # Exclude tool_use chunks — JSON/bash tool calls have low semantic value
        # and create noisy clusters that pollute the visualization.
        chunks = [c for c in chunks if c.get("hook_type", "") != "tool_use"]
        if not chunks:
            logger.warning("recluster: no non-tool_use chunks found for %s", project_path)
            return

        vectors = np.array([c["vector"] for c in chunks], dtype=np.float32)
        logger.info(
            "recluster: starting for %s — %d chunks, mcs=%d ms=%d nn=%d md=%.2f",
            project_path, len(chunks), min_cluster_size, min_samples,
            umap_n_neighbors, umap_min_dist,
        )

        # CPU-bound work in executor
        engine = ClusteringEngine()
        result = await loop.run_in_executor(
            None,
            lambda: engine.compute(
                vectors,
                min_cluster_size=min_cluster_size,
                min_samples=min_samples,
                umap_n_neighbors=umap_n_neighbors,
                umap_min_dist=umap_min_dist,
            ),
        )

        # Build update list
        updates = [
            {
                "chunk_id": c["chunk_id"],
                "cluster_id": int(result.labels[i]),
                "umap_x": float(result.umap_coords[i, 0]),
                "umap_y": float(result.umap_coords[i, 1]),
            }
            for i, c in enumerate(chunks)
        ]

        await loop.run_in_executor(None, storage.update_chunk_clusters, updates)

        # Persist last_clustered_at
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()
        await loop.run_in_executor(
            None,
            lambda: storage.upsert_cluster_config(project_path, last_clustered_at=now_iso),
        )

        logger.info(
            "recluster: %s done — %d clusters, %.1f%% noise",
            project_path, result.n_clusters, result.noise_ratio * 100,
        )
    except Exception as exc:
        logger.error("recluster background task failed for %s: %s", project_path, exc)
    finally:
        _recluster_running.pop(project_path, None)


async def dashboard_recluster(request: Request) -> JSONResponse:
    """POST /dashboard/api/recluster — trigger background re-clustering."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    project_path = (body.get("project_path") or "").strip()
    if not project_path:
        return JSONResponse({"error": "project_path is required"}, status_code=400)

    if _recluster_running.get(project_path):
        return JSONResponse(
            {"error": "clustering already running for this project"},
            status_code=409,
        )

    _recluster_running[project_path] = True
    loop = asyncio.get_running_loop()
    loop.create_task(_run_recluster(project_path))

    return JSONResponse(
        {"status": "accepted", "project_path": project_path},
        status_code=202,
    )


async def dashboard_recluster_status(request: Request) -> JSONResponse:
    """GET /dashboard/api/recluster-status?project_path=... — running state."""
    project_path = request.query_params.get("project_path", "").strip()
    if not project_path:
        return JSONResponse({"error": "project_path is required"}, status_code=400)

    running = bool(_recluster_running.get(project_path))

    loop = asyncio.get_running_loop()
    cfg = await loop.run_in_executor(None, storage.get_cluster_config, project_path)
    last_clustered_at = cfg["last_clustered_at"] if cfg else None

    return JSONResponse({
        "project_path": project_path,
        "running": running,
        "last_clustered_at": last_clustered_at,
    })


async def dashboard_chunk_detail(request: Request) -> JSONResponse:
    """GET /dashboard/api/chunk/{chunk_id} — full chunk text + session summary."""
    chunk_id = request.path_params.get("chunk_id", "").strip()
    if not chunk_id or not _valid_uuid(chunk_id):
        return JSONResponse({"error": "invalid chunk_id"}, status_code=400)

    loop = asyncio.get_running_loop()
    detail = await loop.run_in_executor(None, storage.get_chunk_detail, chunk_id)

    if detail is None:
        return JSONResponse({"error": "chunk not found"}, status_code=404)
    return JSONResponse(detail)
