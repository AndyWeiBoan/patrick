# Eval Harness — Frozen Benchmark Set

## Purpose
30 representative queries frozen in `queries.jsonl`.  
These queries **must never be modified** once ground truth is annotated — changing the benchmark invalidates all historical comparisons.

## Ground Truth Annotation

Each query has a `relevant_chunk_ids` field that must be populated by a human annotator before the eval harness produces meaningful numbers.

### How to annotate

1. Start the patrick server: `patrick start`
2. For each query in `queries.jsonl`, run:
   ```bash
   python scripts/eval.py --search-only --query "your query here"
   ```
3. Review the returned chunks and identify which are genuinely relevant
4. Add the `chunk_id` values to `relevant_chunk_ids` in the JSONL line
5. Commit the annotated file — **never modify existing annotated entries**

### Annotation guidelines
- A chunk is **relevant** if it directly answers or contains key information for the query
- Include all relevant chunks you can find (completeness matters for Recall)
- For "decision" category queries, look for chunks that explain the *why*
- For "technical" category queries, look for chunks that describe the *how*
- Cross-language queries (zh/en): relevant chunks may be in either language

## Current Status

| Field | Value |
|-------|-------|
| Total queries | 30 |
| Annotated | 0 (needs human annotation) |
| Languages | en (21), zh (4), mixed (5) |
| Categories | technical (17), decision (5), project_overview (3), process (2), metrics (1), usage (1), mixed (1) |

## Metrics computed by `scripts/eval.py`

- **Recall@10**: fraction of relevant chunks found in top-10 results
- **nDCG@10**: normalized discounted cumulative gain at rank 10 (rewards top-ranked hits)  
- **MRR**: mean reciprocal rank of the first relevant result

## How to run

```bash
# Compute all metrics and compare against previous baseline
python scripts/eval.py

# Run with hybrid mode
python scripts/eval.py --mode hybrid

# Output JSON artifact for CI
python scripts/eval.py --output results/phase2_baseline.json
```
