"""Planning speed: time to first token and total time on the planning questions, through the real streaming path.

Runs each question in evals/planning_questions.jsonl through WaypointRuntime.ask_stream with the answer cache off and
records TTFT (first answer text or first structured plan event, whichever comes first), total time, model calls,
tokens and cost, plus plan validity judged deterministically (as in plan_validity.py). Prints P50/P95.

    WAYPOINT_LLM=vertex WAYPOINT_ANSWER_CACHE=off uv run python -m evals.plan_speed --name plan_speed_before
"""

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path
from typing import Any

from evals.plan_validity import extract_plan
from waypoint.agents.runtime import WaypointRuntime
from waypoint.config import get_settings
from waypoint.tools.plan import validate_plan_text

HERE = Path(__file__).parent


def percentile(values: list[float], p: float) -> float:
    """Nearest-rank percentile (P95 of 10 values is the 10th, i.e. the slowest)."""
    ordered = sorted(values)
    rank = max(1, -(-len(ordered) * p // 100))
    return ordered[int(rank) - 1]


async def run_one(runtime: WaypointRuntime, q: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    first: float | None = None
    plan_event: float | None = None
    answer = None
    async for kind, data in runtime.ask_stream(f"plan-speed-{q['id']}", q["question"]):
        now = time.perf_counter() - started
        if kind == "plan" and plan_event is None:
            plan_event = now
        if kind in ("token", "plan") and first is None:
            first = now
        if kind == "final":
            answer = data
    total = time.perf_counter() - started
    assert answer is not None
    plan = extract_plan(answer.text)
    check = validate_plan_text(plan, q["completed"], runtime.deps.catalog) if plan else {"valid": False}
    m = answer.metrics()
    calls = answer.record.model_calls
    return {
        "id": q["id"],
        "ttft_s": round(first if first is not None else total, 2),
        "plan_event_s": round(plan_event, 2) if plan_event is not None else None,
        "total_s": round(total, 2),
        "route": m["route"],
        "category": m["category"],
        "model_calls": m["model_calls"],
        "input_tokens": m["input_tokens"],
        "visible_output_tokens": sum(c.output_tokens for c in calls),
        "thinking_tokens": sum(c.thinking_tokens for c in calls),
        "cost_usd": m["cost_usd"],
        "limit_hit": m["limit_hit"],
        "valid": bool(check["valid"]),
        "errors": check.get("errors", ["no plan lines found"]),
        "plan": plan,
        "answer": answer.text,
    }


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ttft = [r["ttft_s"] for r in rows]
    total = [r["total_s"] for r in rows]
    return {
        "questions": len(rows),
        "ttft_p50_s": round(statistics.median(ttft), 2),
        "ttft_p95_s": percentile(ttft, 95),
        "total_p50_s": round(statistics.median(total), 2),
        "total_p95_s": percentile(total, 95),
        "plan_validity": round(sum(r["valid"] for r in rows) / len(rows), 3),
        "timeouts": sum(r["limit_hit"] for r in rows),
        "mean_model_calls": round(sum(r["model_calls"] for r in rows) / len(rows), 1),
        "total_cost_usd": round(sum(r["cost_usd"] for r in rows), 4),
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", default="plan_speed")
    parser.add_argument("--ids", help="comma-separated question ids (default: all)")
    args = parser.parse_args()
    questions = [json.loads(line) for line in (HERE / "planning_questions.jsonl").read_text().splitlines() if line]
    if args.ids:
        questions = [q for q in questions if q["id"] in args.ids.split(",")]
    runtime = WaypointRuntime.from_settings(get_settings())
    rows = []
    for q in questions:
        rows.append(await run_one(runtime, q))
        print(json.dumps({k: v for k, v in rows[-1].items() if k not in ("answer", "errors")}), flush=True)
    result = {
        "settings": {"llm": runtime.settings.llm, "plan_budget_s": runtime.settings.plan_budget_s},
        "summary": summarise(rows),
        "rows": rows,
    }
    out = HERE / "results" / f"{args.name}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(result, indent=2))
    print(json.dumps(result["summary"], indent=2))


if __name__ == "__main__":
    asyncio.run(main())
