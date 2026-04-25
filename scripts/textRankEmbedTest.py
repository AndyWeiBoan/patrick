#!/usr/bin/env python3
"""
TextRank with embedding cosine similarity (vs TF-IDF in textRankTest.py).

Similarity matrix is built from fastembed vectors instead of TF-IDF,
giving semantic matching rather than token-overlap matching.

Usage:
    python scripts/textRankEmbedTest.py [session_id]
"""

import sys
import re
import math
from pathlib import Path

DEFAULT_SESSION_ID = "6312d3c5-896e-4c5f-ab3e-aad703bebbe2"
TOP_N = 5
EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


# ── Data ─────────────────────────────────────────────────────────────────────

def fetch_assistant_texts(session_id: str) -> list[str]:
    import lancedb
    db = lancedb.connect(str(Path.home() / ".patrick" / "data"))
    tbl = db.open_table("turn_chunks")
    df = tbl.to_pandas()
    session_df = df[
        (df["session_id"] == session_id) & (df["hook_type"] == "assistant_text")
    ].sort_values("created_at")
    return session_df["text"].tolist()


# ── Sentence splitting ────────────────────────────────────────────────────────

_NOISE_PATTERNS = re.compile(
    r'^\[\d+\]'       # starts with [number]
    r'|score=\d'      # contains score=
    r'|^`{3}'         # code block fence
)

def split_sentences(text: str) -> list[str]:
    raw = re.split(r'(?<=[。！？!?])\s*|\n{2,}', text)
    cleaned = []
    for s in raw:
        s = s.strip()
        if len(s) <= 15:
            continue
        if _NOISE_PATTERNS.search(s):
            continue
        cleaned.append(s)
    return cleaned


# ── Embedding cosine similarity ───────────────────────────────────────────────

def embed_sentences(sentences: list[str]) -> list[list[float]]:
    from fastembed import TextEmbedding
    model = TextEmbedding(model_name=EMBEDDING_MODEL)
    return [v.tolist() for v in model.embed(sentences)]


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x ** 2 for x in a))
    nb = math.sqrt(sum(x ** 2 for x in b))
    return dot / (na * nb + 1e-10)


def build_sim_matrix(vecs: list[list[float]]) -> list[list[float]]:
    n = len(vecs)
    return [[cosine(vecs[i], vecs[j]) for j in range(n)] for i in range(n)]


# ── TextRank ──────────────────────────────────────────────────────────────────

def textrank(sim: list[list[float]], damping: float = 0.85, iters: int = 50) -> list[float]:
    n = len(sim)
    scores = [1.0 / n] * n
    row_sums = [sum(sim[i]) or 1.0 for i in range(n)]
    norm = [[sim[i][j] / row_sums[i] for j in range(n)] for i in range(n)]
    for _ in range(iters):
        scores = [
            (1 - damping) / n + damping * sum(norm[j][i] * scores[j] for j in range(n))
            for i in range(n)
        ]
    return scores


# ── Main ──────────────────────────────────────────────────────────────────────

def summarize(texts: list[str], top_n: int = TOP_N) -> list[tuple[int, float, str]]:
    sentences: list[str] = []
    for text in texts:
        sentences.extend(split_sentences(text))

    if not sentences:
        return []

    print(f"Embedding {len(sentences)} sentences...", flush=True)
    vecs = embed_sentences(sentences)
    sim = build_sim_matrix(vecs)
    scores = textrank(sim)

    ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
    top = sorted(ranked[:top_n], key=lambda x: x[0])
    return [(idx, scores[idx], sentences[idx]) for idx, _ in top]


if __name__ == "__main__":
    session_id = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SESSION_ID

    print(f"Session : {session_id}")
    texts = fetch_assistant_texts(session_id)
    print(f"Chunks  : {len(texts)} assistant_text paragraphs\n")

    results = summarize(texts, top_n=TOP_N)

    print()
    print("=" * 60)
    print(f"TextRank (embedding) — top {TOP_N} sentences (original order)")
    print("=" * 60)
    for idx, score, sentence in results:
        print(f"\n[{idx:3d}] score={score:.4f}")
        print(f"      {sentence}")
