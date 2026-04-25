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
# "sentence-transformers/paraphrase-multilingual-mpnet-base-v2"  (768 dim, 1 GB, needs reindex)
# "intfloat/multilingual-e5-large"                               (1024 dim, 2.24 GB, needs reindex)
# NOTE: intfloat/multilingual-e5-small is NOT supported in fastembed

VECTOR_DIM: int = 384          # matches paraphrase-multilingual-MiniLM-L12-v2
CHUNK_SIZE: int = 400          # tokens per chunk
CHUNK_OVERLAP: int = 80        # overlap tokens between chunks (Phase 2: 50→80)

# ── Queue / batching ─────────────────────────────────────────────────────────
BATCH_SIZE: int = 16           # embed up to N items at once
BATCH_TIMEOUT: float = 2.0     # flush queue after T seconds even if < BATCH_SIZE

# ── Search ───────────────────────────────────────────────────────────────────
TOP_K_SESSIONS: int = 5        # Layer 1 coarse filter
TOP_K_CHUNKS: int = 10         # Layer 2 fine retrieval
MIN_SESSION_SCORE: float = 0.3 # below this, Layer 1 degrades to global search

# ── Phase 2: Hybrid Search ────────────────────────────────────────────────────
# BM25 parameters (tuned via grid search on frozen benchmark set)
BM25_K1: float = 1.5           # term frequency saturation; 1.2–2.0 typical range
BM25_B: float = 0.75           # length normalization; 0.0 = no norm, 1.0 = full norm
BM25_WEIGHT: float = 0.3       # weight for BM25 score in RRF fusion (0 = pure vector)
VECTOR_WEIGHT: float = 0.7     # weight for vector score in RRF fusion
RRF_K: int = 60                # RRF constant k; higher = less rank-position sensitivity
HYBRID_RECALL_N: int = 50      # top-N candidates fed to cross-encoder rerank

# ── Phase 2: Cross-encoder Rerank ─────────────────────────────────────────────
# Options (uncomment to switch):
#   "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"   lightweight multilingual (~120 MB)
#   "BAAI/bge-reranker-v2-m3"                       best multilingual quality (~1.1 GB)
RERANK_MODEL: str = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
RERANK_TOP_N: int = 50         # candidates to rerank (from hybrid recall)
RERANK_ENABLED: bool = True    # global switch; set False to skip rerank & measure latency delta

# ── Scheduled Compaction ──────────────────────────────────────────────────────
COMPACT_CHECK_INTERVAL: int = 300       # seconds between fragment-count checks (5 min)
COMPACT_FRAGMENT_THRESHOLD: int = 100   # compact when any table exceeds this many .lance files

# ── Role-based scoring boost ──────────────────────────────────────────────────
# In hybrid search, assistant responses typically have higher information density
# than user prompts (which may contain framework-injected context, raw logs, etc.).
# This multiplier boosts assistant_text chunks in RRF fusion scoring.
ASSISTANT_TEXT_BOOST: float = 1.5  # 1.0 = no boost; 1.5 = 50% score increase

# ── Phase 2: Deduplication ────────────────────────────────────────────────────
COSINE_DEDUP_THRESHOLD: float = 0.95  # session-level semantic dedup threshold

# ── Phase 3: Time-decay search ────────────────────────────────────────────────
# Controls recency weighting in search_chunks_with_recency() (use_recency=True).
# Score = hybrid_score * exp(-age_days / TIME_DECAY_HALFLIFE_DAYS)
# At halflife_days age → score halved; at 2× halflife → quartered.
TIME_DECAY_HALFLIFE_DAYS: int = 30   # half-life in days; smaller = prefer newer memories more
RECENCY_BLEND: float = 1.0           # recency influence (0=ignore time, 1=full decay applied)
