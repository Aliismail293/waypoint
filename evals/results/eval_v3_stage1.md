| Metric (eval_v3_stage1, test split, n=21, k=5) | Value |
|---|---|
| Answer accuracy (LLM judge) | 61.9% |
| Retrieval hit rate (recall@5) | 71.4% |
| Pass rate (hit AND correct) | 47.6% |
| MRR | 0.691 |
| P50 / P95 latency | 4.0 s / 6.9 s |
| TTFT P50 / P95 | 3.8 s / 6.8 s |
| Cost per query (agent, est.) | $0.0039 |
| Mean model calls per query | 2.9 |
| route `fact` (n=1): TTFT P50/P95, total P95 | 1.6 / 1.6 s, 1.6 s |
| route `fast` (n=20): TTFT P50/P95, total P95 | 3.8 / 19.3 s, 19.4 s |
