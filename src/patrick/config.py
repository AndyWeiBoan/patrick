"""Central config — change one constant, restart server."""

from pathlib import Path

# ── Server ──────────────────────────────────────────────────────────────────
PORT: int = 3141  # 3112 conflicts with Docker on some systems
HOST: str = "127.0.0.1"

# ── Storage ──────────────────────────────────────────────────────────────────
DATA_DIR: Path = Path.home() / ".patrick" / "data"

# ── Embedding ────────────────────────────────────────────────────────────────
# Fastest multilingual model available in fastembed 0.8.0
# 0.22 GB, 384 dim, ~50 languages including zh/en
EMBEDDING_MODEL_FASTEMBED: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
# Tokenizer ID on HuggingFace (for token-aware chunking)
EMBEDDING_TOKENIZER_HF: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

# Upgrade options (change one line, restart server):
# "sentence-transformers/paraphrase-multilingual-mpnet-base-v2"  (768 dim, 1 GB)
# "intfloat/multilingual-e5-large"                               (1024 dim, 2.24 GB)

VECTOR_DIM: int = 384          # matches paraphrase-multilingual-MiniLM-L12-v2
CHUNK_SIZE: int = 400          # tokens per chunk
CHUNK_OVERLAP: int = 50        # overlap tokens between chunks

# ── Queue / batching ─────────────────────────────────────────────────────────
BATCH_SIZE: int = 16           # embed up to N items at once
BATCH_TIMEOUT: float = 2.0     # flush queue after T seconds even if < BATCH_SIZE

# ── Search ───────────────────────────────────────────────────────────────────
TOP_K_SESSIONS: int = 5        # Layer 1 coarse filter
TOP_K_CHUNKS: int = 10         # Layer 2 fine retrieval
MIN_SESSION_SCORE: float = 0.3 # below this, Layer 1 degrades to global search
