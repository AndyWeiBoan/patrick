"""
Compare binary nDCG vs graded nDCG on hand-crafted cases with known ground truth.

Binary nDCG: every relevant chunk gets gain=1 regardless of grade.
Graded nDCG: G2 chunk gets gain=3 (2^2-1), G1 chunk gets gain=1 (2^1-1).

Run:
    python -m pytest tests/unit/test_ndcg.py -v
"""

from __future__ import annotations

import math

import pytest


# ── Implementations under comparison ─────────────────────────────────────────

def binary_ndcg(relevant: set[str], retrieved: list[str], k: int = 10) -> float:
    """Current eval.py implementation — binary relevance."""
    if not relevant:
        return 0.0
    dcg = sum(
        1.0 / math.log2(rank + 1)
        for rank, cid in enumerate(retrieved[:k], 1)
        if cid in relevant
    )
    ideal_k = min(len(relevant), k)
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, ideal_k + 1))
    return dcg / idcg if idcg > 0 else 0.0


def graded_ndcg(relevance_map: dict[str, int], retrieved: list[str], k: int = 10) -> float:
    """Proposed implementation — uses G1/G2 grades."""
    if not relevance_map:
        return 0.0
    dcg = sum(
        (2 ** relevance_map.get(cid, 0) - 1) / math.log2(rank + 1)
        for rank, cid in enumerate(retrieved[:k], 1)
    )
    ideal_scores = sorted(relevance_map.values(), reverse=True)[:k]
    idcg = sum(
        (2 ** g - 1) / math.log2(i + 1)
        for i, g in enumerate(ideal_scores, 1)
    )
    return dcg / idcg if idcg > 0 else 0.0


# ── Ground truth: hand-computed values ───────────────────────────────────────
#
# Notation:
#   G2 chunk → gain = 2^2-1 = 3
#   G1 chunk → gain = 2^1-1 = 1
#   discount at rank r = 1 / log2(r+1)
#
#   DCG  = sum(gain_i / log2(rank_i + 1))
#   IDCG = DCG of ideal (grades sorted desc, starting rank 1)
#   nDCG = DCG / IDCG
#
# log2(2)=1.000, log2(3)=1.585, log2(4)=2.000, log2(5)=2.322, log2(6)=2.585


CASES = [
    # ── Case 1: all same grade (G1) — both methods must agree ─────────────
    # Relevant: A=G1, B=G1, C=G1  |  Retrieved: [A, B, C]
    # Binary  IDCG = 1+0.631+0.500 = 2.131   DCG = 2.131  → 1.0
    # Graded  gains all=1, identical computation        → 1.0
    pytest.param(
        {"A": 1, "B": 1, "C": 1},   # relevance_grades
        ["A", "B", "C"],             # retrieved
        1.0,                         # expected binary
        1.0,                         # expected graded
        id="all_G1_perfect_retrieval",
    ),

    # ── Case 2: all same grade (G2) — both methods must agree ─────────────
    # Relevant: A=G2, B=G2  |  Retrieved: [A, B]
    # Binary  IDCG = 1+0.631 = 1.631   DCG = 1.631  → 1.0
    # Graded  gains all=3, ratio still 1.0            → 1.0
    pytest.param(
        {"A": 2, "B": 2},
        ["A", "B"],
        1.0,
        1.0,
        id="all_G2_perfect_retrieval",
    ),

    # ── Case 3: THE KEY CASE — ordering matters for graded, not binary ─────
    # Relevant: A=G2, B=G2, C=G1
    #
    # System A retrieved: [A, B, C]   (G2 first — ideal order)
    # System B retrieved: [C, A, B]   (G1 first — suboptimal)
    #
    # Binary  IDCG = 1+0.631+0.500 = 2.131
    #   A: DCG = 2.131 → 1.0
    #   B: DCG = 2.131 → 1.0   ← SAME, can't distinguish
    #
    # Graded  IDCG = 3/1+3/1.585+1/2 = 3.000+1.893+0.500 = 5.393
    #   A: DCG = 3/1+3/1.585+1/2 = 5.393 → 1.0
    #   B: DCG = 1/1+3/1.585+3/2 = 1.000+1.893+1.500 = 4.393 → 4.393/5.393 = 0.8146
    pytest.param(
        {"A": 2, "B": 2, "C": 1},
        ["A", "B", "C"],    # System A: G2 first
        1.0,
        1.0,
        id="ordering_matters__system_A__G2_first",
    ),
    pytest.param(
        {"A": 2, "B": 2, "C": 1},
        ["C", "A", "B"],    # System B: G1 first
        1.0,                # binary: same as System A — BLIND TO ORDER
        0.8146,             # graded: correctly penalises G1 at rank 1
        id="ordering_matters__system_B__G1_first",
    ),

    # ── Case 4: missing the G2 chunk — graded penalises harder ────────────
    # Relevant: A=G2, B=G1  |  Retrieved: [B, X, X]  (only G1 found)
    #
    # Binary  IDCG = 1+0.631 = 1.631   DCG = 1/1 = 1.000 → 0.6131
    # Graded  IDCG = 3/1+1/1.585 = 3.000+0.631 = 3.631
    #         DCG  = 1/1 = 1.000   (B is G1, gain=1)      → 1.000/3.631 = 0.2754
    pytest.param(
        {"A": 2, "B": 1},
        ["B", "X1", "X2"],
        0.6131,
        0.2754,
        id="miss_G2_found_only_G1",
    ),

    # ── Case 5: no relevant results found ─────────────────────────────────
    pytest.param(
        {"A": 2, "B": 1},
        ["X1", "X2", "X3"],
        0.0,
        0.0,
        id="no_hits",
    ),

    # ── Case 6: partial retrieval, different grade ordering ────────────────
    # Relevant: A=G2, B=G2, C=G1, D=G1  (4 relevant, D never retrieved)
    # Retrieved (System A): [A, junk, B, junk, C]   → hits at ranks 1,3,5
    # Retrieved (System B): [C, junk, A, junk, B]   → hits at ranks 1,3,5 (same positions!)
    #
    # Binary  IDCG = 1+0.631+0.500+0.431 = 2.562
    #   A: DCG = 1/1+1/2+1/2.585 = 1.000+0.500+0.387 = 1.887 → 1.887/2.562 = 0.7365
    #   B: same positions, same binary gains             → 0.7365   SAME
    #
    # Graded  IDCG = 3/1+3/1.585+1/2+1/2.322 = 3.000+1.893+0.500+0.431 = 5.824
    #   A: DCG = 3/1+3/2+1/2.585 = 3.000+1.500+0.387 = 4.887 → 4.887/5.824 = 0.8391
    #   B: DCG = 1/1+3/2+3/2.585 = 1.000+1.500+1.161 = 3.661 → 3.661/5.824 = 0.6287
    pytest.param(
        {"A": 2, "B": 2, "C": 1, "D": 1},
        ["A", "junk1", "B", "junk2", "C"],  # System A: G2 first
        0.7365,
        0.8391,
        id="partial_miss__system_A__G2_prioritised",
    ),
    pytest.param(
        {"A": 2, "B": 2, "C": 1, "D": 1},
        ["C", "junk1", "A", "junk2", "B"],  # System B: G1 first
        0.7365,  # binary: SAME as System A — can't tell the difference
        0.6286,  # graded: correctly lower than System A
        id="partial_miss__system_B__G1_prioritised",
    ),
]


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("grades,retrieved,expected_binary,expected_graded", CASES)
def test_binary_ndcg(grades, retrieved, expected_binary, expected_graded):
    relevant = set(grades.keys())
    result = binary_ndcg(relevant, retrieved, k=10)
    assert result == pytest.approx(expected_binary, abs=1e-4)


@pytest.mark.parametrize("grades,retrieved,expected_binary,expected_graded", CASES)
def test_graded_ndcg(grades, retrieved, expected_binary, expected_graded):
    result = graded_ndcg(grades, retrieved, k=10)
    assert result == pytest.approx(expected_graded, abs=1e-4)


def test_ordering_matters_summary():
    """
    Demonstrate the discrimination gap in one readable assertion.

    Same 3 chunks found at same 3 ranks. Only the grade-order differs.
    Binary sees no difference. Graded correctly prefers G2-first ranking.
    """
    grades = {"A": 2, "B": 2, "C": 1}
    system_a = ["A", "B", "C"]   # G2, G2, G1
    system_b = ["C", "A", "B"]   # G1, G2, G2

    binary_a = binary_ndcg(set(grades), system_a)
    binary_b = binary_ndcg(set(grades), system_b)
    graded_a = graded_ndcg(grades, system_a)
    graded_b = graded_ndcg(grades, system_b)

    # Binary cannot distinguish
    assert binary_a == pytest.approx(binary_b, abs=1e-6), (
        f"Binary should be equal: {binary_a:.4f} vs {binary_b:.4f}"
    )

    # Graded correctly prefers system_a
    assert graded_a > graded_b, (
        f"Graded should prefer G2-first: {graded_a:.4f} vs {graded_b:.4f}"
    )
    assert graded_a == pytest.approx(1.0, abs=1e-4)
    assert graded_b == pytest.approx(0.8146, abs=1e-4)
