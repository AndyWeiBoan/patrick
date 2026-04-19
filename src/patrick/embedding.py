"""EmbeddingProvider — singleton fastembed wrapper with token-aware chunking."""

from __future__ import annotations

import asyncio
import hashlib
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from tokenizers import Tokenizer

from .config import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    EMBEDDING_MODEL_FASTEMBED,
    EMBEDDING_TOKENIZER_HF,
    VECTOR_DIM,
)

if TYPE_CHECKING:
    from fastembed import TextEmbedding

# One global thread pool for all CPU-bound embedding work.
# max_workers=1: embedding doesn't benefit from concurrency; keeps memory stable.
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="embed")


class EmbeddingProvider:
    """Singleton embedding provider backed by fastembed + ONNX runtime.

    All .embed() calls are synchronous internally — callers in async context
    MUST use embed_async() or run_in_executor themselves.
    """

    _instance: "EmbeddingProvider | None" = None

    def __new__(cls) -> "EmbeddingProvider":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
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
