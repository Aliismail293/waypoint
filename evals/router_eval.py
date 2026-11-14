"""Router eval: does the fast-path router send each labelled question to the right route?

    uv run python evals/router_eval.py                 # offline: the keyword heuristic (CI gate, no secrets)
    uv run python evals/router_eval.py --live          # real router model on Vertex AI (a few cents)

Labels live in evals/router_questions.jsonl (route, and for simple_lookup the specialist). The dev split of the
golden set only holds single-course / single-rule questions, so timetable, planning and unsure examples are
written by hand or taken from evals/planning_questions.jsonl (the `source` field says which). Exits non-zero
when route accuracy is below 90%.
"""

import argparse
import asyncio
import json
import os
import sys
from collections import Counter
from pathlib import Path

from waypoint.agents.router import classify
from waypoint.config import Settings

EVALS = Path(__file__).parent
QUESTIONS = EVALS / "router_questions.jsonl"
RESULTS = EVALS / "results"
THRESHOLD = 0.90


def load() -> list[dict]:
    return [json.loads(line) for line in QUESTIONS.read_text().splitlines() if line.strip()]


async def evaluate(rows: list[dict], settings: Settings) -> dict:
    results = []
    for row in rows:
        decision = await classify(row["question"], settings)
        results.append(
            {
                "id": row["id"],
                "expected": row["route"],
                "got": decision.category,
                "specialist_expected": row["specialist"],
                "specialist_got": decision.specialist.removesuffix("_agent").removesuffix("_data"),
                "ms": round(decision.ms, 1),
                "model_calls": decision.model_calls,
            }
        )
    correct = sum(r["expected"] == r["got"] for r in results)
    lookups = [r for r in results if r["expected"] == "simple_lookup" and r["got"] == "simple_lookup"]
    specialist_ok = sum(r["specialist_expected"] == r["specialist_got"] for r in lookups)
    # The costly mistake is a *fast* route that is wrong (the full path is always safe, only slower).
    wrong_fast = [r for r in results if r["got"] != "unsure" and r["got"] != r["expected"]]
    return {
        "mode": "model" if settings.llm == "vertex" else "heuristic",
        "router_model": settings.router_model if settings.llm == "vertex" else None,
        "questions": len(results),
        "route_accuracy": round(correct / len(results), 4),
        "per_route": {
            route: f"{sum(r['expected'] == r['got'] for r in results if r['expected'] == route)}/"
            f"{sum(r['expected'] == route for r in results)}"
            for route in ("simple_lookup", "timetable", "planning", "unsure")
        },
        "specialist_accuracy_on_correct_lookups": round(specialist_ok / len(lookups), 4) if lookups else None,
        "wrong_fast_routes": len(wrong_fast),
        "confusions": dict(Counter(f"{r['expected']}->{r['got']}" for r in results if r["expected"] != r["got"])),
        "median_ms": sorted(r["ms"] for r in results)[len(results) // 2],
        "router_model_calls": sum(r["model_calls"] for r in results),
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="use the real router model (Vertex AI)")
    parser.add_argument("--name", default=None)
    args = parser.parse_args()
    for key in [k for k in os.environ if k.startswith("WAYPOINT_")]:
        del os.environ[key]
    if not args.live:
        os.environ["WAYPOINT_LLM"] = "mock"
    else:
        os.environ["WAYPOINT_LLM"] = "vertex"
    settings = Settings()
    report = asyncio.run(evaluate(load(), settings))
    RESULTS.mkdir(exist_ok=True)
    name = args.name or ("router_eval_live" if args.live else "router_eval_offline")
    (RESULTS / f"{name}.json").write_text(json.dumps(report, indent=2) + "\n")
    summary = {k: v for k, v in report.items() if k != "results"}
    print(json.dumps(summary, indent=2))
    if report["route_accuracy"] < THRESHOLD:
        print(f"ROUTER GATE FAILED: accuracy {report['route_accuracy']:.1%} < {THRESHOLD:.0%}")
        sys.exit(1)


if __name__ == "__main__":
    main()
