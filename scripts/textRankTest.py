#!/usr/bin/env python3
"""
TextRank extractive summarization on a session's assistant_text chunks.

Each assistant_text is treated as a paragraph; sentences are extracted
across all paragraphs, ranked via TextRank, and top-N returned.

Usage:
    python scripts/textRankTest.py [session_id]

No external NLP libraries needed — only stdlib + lancedb.
"""

import sys
import re
import math
from pathlib import Path
from collections import Counter

DEFAULT_SESSION_ID = "6312d3c5-896e-4c5f-ab3e-aad703bebbe2"
TOP_N = 5


# ── Data ─────────────────────────────────────────────────────────────────────

def fetch_texts(session_id: str, limit_chunks: int = 0) -> list[str]:
    import lancedb
    db = lancedb.connect(str(Path.home() / ".patrick" / "data"))
    tbl = db.open_table("turn_chunks")
    df = tbl.to_pandas()
    session_df = df[
        (df["session_id"] == session_id) &
        (df["hook_type"].isin(["assistant_text", "user_prompt"]))
    ].sort_values("created_at")
    if limit_chunks > 0:
        session_df = session_df.head(limit_chunks * 2)  # N user + N assistant
    return session_df["text"].tolist()


# ── Sentence splitting ────────────────────────────────────────────────────────

def split_sentences(text: str) -> list[str]:
    # Split on Chinese and English sentence-ending punctuation or blank lines
    raw = re.split(r'(?<=[。！？!?])\s*|\n{2,}', text)
    return [s.strip() for s in raw if len(s.strip()) > 15]


# ── TF-IDF similarity (character-level for CJK, word-level for Latin) ────────

def tokenize(text: str) -> list[str]:
    return re.findall(r'[一-鿿]|[a-zA-Z0-9]+', text.lower())


def build_sim_matrix(sentences: list[str]) -> list[list[float]]:
    tokenized = [tokenize(s) for s in sentences]
    n = len(sentences)

    df_count: Counter = Counter()
    for tokens in tokenized:
        for t in set(tokens):
            df_count[t] += 1
    idf = {t: math.log(n / (1 + c)) for t, c in df_count.items()}

    def tfidf(tokens: list[str]) -> dict[str, float]:
        tf = Counter(tokens)
        total = len(tokens) or 1
        return {t: (cnt / total) * idf.get(t, 0.0) for t, cnt in tf.items()}

    vecs = [tfidf(t) for t in tokenized]

    def cosine(a: dict, b: dict) -> float:
        shared = set(a) & set(b)
        if not shared:
            return 0.0
        dot = sum(a[t] * b[t] for t in shared)
        na = math.sqrt(sum(v ** 2 for v in a.values()))
        nb = math.sqrt(sum(v ** 2 for v in b.values()))
        return dot / (na * nb + 1e-10)

    return [[cosine(vecs[i], vecs[j]) for j in range(n)] for i in range(n)]


# ── TextRank with position preference (Personalized PageRank) ────────────────

def textrank(
    sim: list[list[float]],
    preference: list[float] | None = None,
    damping: float = 0.85,
    iters: int = 50,
) -> list[float]:
    n = len(sim)
    scores = [1.0 / n] * n
    # preference vector: uniform if not provided
    pref = preference if preference else [1.0 / n] * n

    row_sums = [sum(sim[i]) or 1.0 for i in range(n)]
    norm = [[sim[i][j] / row_sums[i] for j in range(n)] for i in range(n)]

    for _ in range(iters):
        scores = [
            (1 - damping) * pref[i] + damping * sum(norm[j][i] * scores[j] for j in range(n))
            for i in range(n)
        ]
    return scores


def build_position_preference(sentence_counts: list[int], first_boost: float = 2.0, top_chunks: int = 5) -> list[float]:
    """First top_chunks paragraphs get first_boost weight; rest uniform."""
    total = sum(sentence_counts)
    if total == 0:
        return []
    boosted_n = sum(sentence_counts[:top_chunks])
    rest_n = total - boosted_n

    base = 1.0
    boosted = base * first_boost
    total_weight = boosted * boosted_n + base * rest_n
    pref = (
        [boosted / total_weight] * boosted_n +
        [base / total_weight] * rest_n
    )
    return pref


# ── Deduplication ────────────────────────────────────────────────────────────

def dedup_sentences(sentences: list[str], threshold: float = 0.8) -> list[str]:
    """Greedy dedup: keep a sentence only if cosine < threshold vs all kept."""
    tokenized = [tokenize(s) for s in sentences]
    n = len(sentences)

    df_count: Counter = Counter()
    for tokens in tokenized:
        for t in set(tokens):
            df_count[t] += 1
    idf = {t: math.log(n / (1 + c)) for t, c in df_count.items()}

    def tfidf(tokens: list[str]) -> dict[str, float]:
        tf = Counter(tokens)
        total = len(tokens) or 1
        return {t: (cnt / total) * idf.get(t, 0.0) for t, cnt in tf.items()}

    def cosine(a: dict, b: dict) -> float:
        shared = set(a) & set(b)
        if not shared:
            return 0.0
        dot = sum(a[t] * b[t] for t in shared)
        na = math.sqrt(sum(v ** 2 for v in a.values()))
        nb = math.sqrt(sum(v ** 2 for v in b.values()))
        return dot / (na * nb + 1e-10)

    vecs = [tfidf(t) for t in tokenized]
    kept: list[str] = []
    kept_vecs: list[dict] = []

    for i, s in enumerate(sentences):
        if any(cosine(vecs[i], kv) >= threshold for kv in kept_vecs):
            continue
        kept.append(s)
        kept_vecs.append(vecs[i])

    return kept


# ── Main ──────────────────────────────────────────────────────────────────────

def summarize(texts: list[str], top_n: int = TOP_N) -> list[tuple[int, float, str]]:
    sentences: list[str] = []
    counts: list[int] = []
    for text in texts:
        s = split_sentences(text)
        sentences.extend(s)
        counts.append(len(s))

    if not sentences:
        return []

    before = len(sentences)
    sentences = dedup_sentences(sentences, threshold=0.8)
    print(f"Dedup   : {before} → {len(sentences)} sentences")

    sim = build_sim_matrix(sentences)
    pref = build_position_preference(counts[:len(sentences)], first_boost=2.0)
    scores = textrank(sim, preference=pref)

    ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
    top = sorted(ranked[:top_n], key=lambda x: x[0])  # restore original order
    return [(idx, scores[idx], sentences[idx]) for idx, _ in top]


if __name__ == "__main__":
    session_id = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SESSION_ID

    print(f"Session : {session_id}")
    texts = fetch_texts(session_id, limit_chunks=5)
    print(f"Chunks  : {len(texts)} (first 5 user_prompt + assistant_text)")

    sentences_total = sum(len(split_sentences(t)) for t in texts)
    print(f"Sentences: {sentences_total} total\n")

    print("=" * 60)
    print(f"TextRank Summary — top {TOP_N} sentences (original order)")
    print("=" * 60)
    results = summarize(texts, top_n=TOP_N)
    for idx, score, sentence in results:
        print(f"\n[{idx:3d}] score={score:.4f}")
        print(f"      {sentence}")
