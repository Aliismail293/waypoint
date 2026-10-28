"""Eval v0: ragaudit golden set over the calendar corpus, then Waypoint scored on the held-out test split.

    # 1. one-off, real LLM: build the golden question set (dev/test split by chunk) into evals/golden/store.db
    uv run --group eval python evals/run_eval.py generate --llm openai
    # 2. score Waypoint on the test split; writes evals/results/<name>.json and .md
    uv run --group eval python evals/run_eval.py evaluate --llm openai --name eval_v0
    # offline plumbing check (mock LLM, mock embeddings, retrieval-only adapter):
    uv run --group eval python evals/run_eval.py evaluate --llm mock --mode retrieval --name offline

Real runs talk to Gemini through Vertex AI's OpenAI-compatible endpoint (ragaudit speaks OpenAI's API);
the API key is a short-lived `gcloud auth print-access-token`, never stored.
"""

import argparse
import json
import os
import shutil
import statistics
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

EVALS = Path(__file__).parent
CORPUS = EVALS / "corpus.jsonl"
STORE = EVALS / "golden" / "store.db"
RESULTS = EVALS / "results"
ADAPTER = EVALS / "waypoint_adapter.py"
VERTEX_OPENAI_BASE = "https://aiplatform.googleapis.com/v1/projects/{project}/locations/global/endpoints/openapi"
GENERATOR = "google/gemini-3.5-flash-lite"
VERIFIER_AND_JUDGE = "google/gemini-3.5-flash"  # ragaudit requires the verifier to differ from the generator


def export_corpus() -> int:
    """data/corpus/calendar_chunks.jsonl -> ragaudit's corpus format, keeping Waypoint's chunk ids."""
    rows = [json.loads(line) for line in (Path("data/corpus/calendar_chunks.jsonl")).read_text().splitlines()]
    with open(CORPUS, "w") as out:
        for position, row in enumerate(rows):
            record = {
                "chunk_id": row["chunk_id"],
                "text": f"{row['heading']}\n{row['text']}",
                "doc_id": row["doc_id"],
                "position": position,
            }
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
    return len(rows)


def configure_llm(llm: str) -> None:
    os.environ["RAGAUDIT_LLM"] = llm
    if llm == "openai":
        project = os.environ.get("WAYPOINT_GCP_PROJECT", "waypoint-513003")
        os.environ["RAGAUDIT_API_BASE"] = VERTEX_OPENAI_BASE.format(project=project)
        os.environ["RAGAUDIT_API_KEY"] = (
            subprocess.check_output(["gcloud", "auth", "print-access-token"]).decode().strip()
        )


def percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, round(pct / 100 * len(ordered) + 0.5) - 1))  # nearest-rank
    return ordered[index]


def cmd_generate(args: argparse.Namespace) -> None:
    from ragaudit.cli_v2 import run_generate

    configure_llm(args.llm)
    print(f"exported {export_corpus()} chunks to {CORPUS}")
    args.store.parent.mkdir(parents=True, exist_ok=True)
    run_generate(
        corpus=CORPUS,
        store_path=args.store,
        max_facts_per_chunk=args.facts,
        llm=args.llm,
        generator_model=GENERATOR if args.llm == "openai" else None,
        verifier_model=VERIFIER_AND_JUDGE if args.llm == "openai" else None,
        max_chunks=None,
        max_llm_calls=args.max_llm_calls,
        multi_hop=False,
        multi_hop_pairs=0,
        lexical_threshold=0.5,
        retry_processed=False,
    )


def cmd_evaluate(args: argparse.Namespace) -> None:
    from ragaudit.cli_v2 import run_evaluate

    configure_llm(args.llm)
    RESULTS.mkdir(parents=True, exist_ok=True)
    metrics_log = RESULTS / f"{args.name}.queries.jsonl"
    metrics_log.unlink(missing_ok=True)
    os.environ["WAYPOINT_EVAL_METRICS_LOG"] = str(metrics_log)

    config_file = Path(tempfile.mkstemp(suffix=".json")[1])
    config_file.write_text(json.dumps({"mode": args.mode, "retrieval_mode": args.retrieval_mode}))
    # Evaluate against a scratch copy so cached results from one run never leak into the committed store.
    store_copy = Path(tempfile.mkdtemp()) / "store.db"
    shutil.copy(args.store, store_copy)
    summary = run_evaluate(
        adapter_path=ADAPTER,
        corpus=CORPUS,
        config_path=config_file,
        split=args.split,
        k=args.k,
        regression_sample=50,
        seed=7,
        store_path=store_copy,
        llm=args.llm,
        judge_model=VERIFIER_AND_JUDGE if args.llm == "openai" else None,
        ablation=False,
        full=True,
    )
    rows = [json.loads(line) for line in metrics_log.read_text().splitlines()] if metrics_log.exists() else []
    latencies = [r["latency_ms"] for r in rows]
    costs = [r.get("cost_usd", 0.0) for r in rows]
    report = {
        "name": args.name,
        "created_at": datetime.now(UTC).isoformat(),
        "llm": args.llm,
        "mode": args.mode,
        "split": args.split,
        "k": args.k,
        "questions": summary.n_questions,
        "pass_rate": round(summary.score / 100, 4),
        "answer_accuracy": round(summary.answer_accuracy, 4),
        "retrieval_hit_rate": round(summary.recall_at_k, 4),
        "mrr": round(summary.mrr, 4),
        "failure_counts": summary.failure_counts,
        "verdict_counts": summary.verdict_counts,
        "adapter_errors": summary.adapter_errors,
        "judge_failures": summary.judge_failures,
        "p50_latency_ms": round(percentile(latencies, 50), 1),
        "p95_latency_ms": round(percentile(latencies, 95), 1),
        "mean_cost_per_query_usd": round(statistics.fmean(costs), 5) if costs else 0.0,
        "total_agent_cost_usd": round(sum(costs), 4),
        "mean_model_calls": round(statistics.fmean(r.get("model_calls", 0) for r in rows), 2) if rows else 0.0,
        "mean_tokens_per_second": round(statistics.fmean(r.get("tokens_per_second", 0) for r in rows), 1)
        if rows
        else 0.0,
        "limit_hits": sum(1 for r in rows if r.get("limit_hit")),
        "invalid_citations": sum(r.get("invalid_citations", 0) for r in rows),
    }
    (RESULTS / f"{args.name}.json").write_text(json.dumps(report, indent=2) + "\n")
    (RESULTS / f"{args.name}.md").write_text(markdown_table(report))
    print(markdown_table(report))


def markdown_table(r: dict) -> str:
    return (
        f"| Metric ({r['name']}, {r['split']} split, n={r['questions']}, k={r['k']}) | Value |\n|---|---|\n"
        f"| Answer accuracy (LLM judge) | {r['answer_accuracy']:.1%} |\n"
        f"| Retrieval hit rate (recall@{r['k']}) | {r['retrieval_hit_rate']:.1%} |\n"
        f"| Pass rate (hit AND correct) | {r['pass_rate']:.1%} |\n"
        f"| MRR | {r['mrr']:.3f} |\n"
        f"| P50 / P95 latency | {r['p50_latency_ms'] / 1000:.1f} s / {r['p95_latency_ms'] / 1000:.1f} s |\n"
        f"| Cost per query (agent, est.) | ${r['mean_cost_per_query_usd']:.4f} |\n"
        f"| Mean model calls per query | {r['mean_model_calls']} |\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    gen = sub.add_parser("generate")
    gen.add_argument("--llm", default="mock", choices=["mock", "openai"])
    gen.add_argument("--facts", type=int, default=2)
    gen.add_argument("--max-llm-calls", type=int, default=600)
    gen.add_argument("--store", type=Path, default=STORE)
    gen.set_defaults(func=cmd_generate)
    ev = sub.add_parser("evaluate")
    ev.add_argument("--llm", default="mock", choices=["mock", "openai"])
    ev.add_argument("--mode", default="agent", choices=["agent", "retrieval"])
    ev.add_argument("--retrieval-mode", default="hybrid", choices=["hybrid", "vector", "keyword"])
    ev.add_argument("--split", default="test", choices=["dev", "test"])
    ev.add_argument("--k", type=int, default=5)
    ev.add_argument("--name", default="eval")
    ev.add_argument("--store", type=Path, default=STORE)
    ev.set_defaults(func=cmd_evaluate)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
