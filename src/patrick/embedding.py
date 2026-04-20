"""EmbeddingProvider — singleton fastembed wrapper with token-aware chunking.

Phase 2 additions:
- cross_encoder_rerank(): loads cross-encoder model lazily, reranks candidates
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from tokenizers import Tokenizer

from .config import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    EMBEDDING_MODEL_FASTEMBED,
    EMBEDDING_TOKENIZER_HF,
    RERANK_MODEL,
    VECTOR_DIM,
)

if TYPE_CHECKING:
    from fastembed import TextEmbedding

logger = logging.getLogger(__name__)

# One global thread pool for all CPU-bound embedding + rerank work.
# max_workers=1: serialises CPU work; keeps memory stable.
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="embed")


class EmbeddingProvider:
    """Singleton embedding provider backed by fastembed + ONNX runtime.

    All .embed() calls are synchronous internally — callers in async context
    MUST use embed_async() or run_in_executor themselves.

    Phase 2: also exposes cross_encoder_rerank_async() for reranking hybrid
    search results without blocking the event loop.
    """

    _instance: "EmbeddingProvider | None" = None

    def __new__(cls) -> "EmbeddingProvider":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
            cls._instance._reranker = None  # lazy-loaded on first rerank call
        return cls._instance

    def initialize(self) -> None:
        """Load model + tokenizer. Called once at server start."""
        if self._initialized:
            return
        from fastembed import TextEmbedding

        self._model: TextEmbedding = TextEmbedding(
            model_name=EMBEDDING_MODEL_FASTEMBED
        )
        # Load tokenizer for token-aware chunk splitting.
        # fastembed has no public tokenizer API, so we load directly via HF.
        self._tokenizer: Tokenizer = Tokenizer.from_pretrained(
            EMBEDDING_TOKENIZER_HF
        )
        self._initialized = True

    # ── Sync embed (used inside thread pool) ─────────────────────────────────

    def embed_sync(self, texts: list[str]) -> list[list[float]]:
        """Embed a list of texts synchronously. Runs in thread pool, not event loop."""
        assert self._initialized, "Call initialize() first"
        vectors = list(self._model.embed(texts))
        return [v.tolist() for v in vectors]

    # ── Async embed (event-loop safe) ─────────────────────────────────────────

    async def embed_async(self, texts: list[str]) -> list[list[float]]:
        """Embed texts without blocking the event loop."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(_executor, self.embed_sync, texts)

    # ── Token-aware chunking ──────────────────────────────────────────────────

    def chunk_text(self, text: str) -> list[str]:
        """Split text into token-aware chunks with overlap.

        Returns list of text chunks. Single short texts return as [text].
        """
        assert self._initialized, "Call initialize() first"
        encoding = self._tokenizer.encode(text)
        token_ids = encoding.ids

        if len(token_ids) <= CHUNK_SIZE:
            return [text]

        chunks: list[str] = []
        start = 0
        while start < len(token_ids):
            end = min(start + CHUNK_SIZE, len(token_ids))
            chunk_ids = token_ids[start:end]
            chunk_text = self._tokenizer.decode(chunk_ids)
            chunks.append(chunk_text)
            if end == len(token_ids):
                break
            start += CHUNK_SIZE - CHUNK_OVERLAP

        return chunks

    # ── Phase 2: Cross-encoder rerank ─────────────────────────────────────────

    def _load_reranker(self) -> None:
        """Lazily load cross-encoder model on first rerank call.

        Loads sentence-transformers CrossEncoder.  Model is downloaded once to
        HuggingFace cache (~/.cache/huggingface) and reused on subsequent starts.
        This intentionally defers the ~120 MB download to the first hybrid search
        call — it doesn't block server startup.
        """
        if self._reranker is not None:
            return
        try:
            from sentence_transformers import CrossEncoder  # type: ignore[import]
            logger.info("Loading cross-encoder model: %s", RERANK_MODEL)
            self._reranker = CrossEncoder(RERANK_MODEL)
            logger.info("Cross-encoder loaded successfully")
        except Exception as e:
            logger.error("Failed to load cross-encoder (%s): %s", RERANK_MODEL, e)
            self._reranker = None

    def rerank_sync(
        self, query: str, candidates: list[dict], top_k: int = 10
    ) -> list[dict]:
        """Rerank candidates using cross-encoder. Returns top_k best candidates.

        Each candidate dict must have a 'text' field.
        Each returned dict is the original candidate with 'rerank_score' added.
        Falls back to original order if model unavailable.
        """
        self._load_reranker()
        if self._reranker is None or not candidates:
            return candidates[:top_k]

        pairs = [(query, c["text"]) for c in candidates]
        try:
            scores = self._reranker.predict(pairs)
        except Exception as e:
            logger.warning("Cross-encoder predict failed: %s", e)
            return candidates[:top_k]

        # Attach scores and sort descending
        scored = [
            {**c, "rerank_score": float(s)}
            for c, s in zip(candidates, scores)
        ]
        scored.sort(key=lambda x: x["rerank_score"], reverse=True)
        return scored[:top_k]

    async def rerank_async(
        self, query: str, candidates: list[dict], top_k: int = 10
    ) -> list[dict]:
        """Non-blocking wrapper around rerank_sync."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            _executor, self.rerank_sync, query, candidates, top_k
        )

    # ── Utility ───────────────────────────────────────────────────────────────

    @staticmethod
    def text_hash(text: str) -> str:
        """SHA-256 hash for exact dedup."""
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @property
    def vector_dim(self) -> int:
        return VECTOR_DIM


# Module-level singleton
provider = EmbeddingProvider()
