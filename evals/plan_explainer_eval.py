"""Plan explanations: does a faster model hold quality? Deterministic judge, live models.

For each question in evals/planning_questions.jsonl the fast planner builds the plan in code, then each candidate model
writes the explanation exactly as the app asks for it. A reply passes when the app kept it (it finished normally and
used no course code outside the facts it was given, else the fixed explanation stands in), it is 2-5 sentences,
mentions an advisor, and makes no claim about what is left or whether the program is finished (code lists that; both
models got it wrong when they were asked to). Also records time to first token and total time.

    WAYPOINT_LLM=vertex uv run python -m evals.plan_explainer_eval
    uv run python -m evals.plan_explainer_eval --rejudge     # re-score the saved replies, no model calls
"""

import argparse
import asyncio
import json
import re
import statistics
import time
from datetime import date
from pathlib import Path
from typing import Any

from evals.plan_speed import percentile
from waypoint.agents.runtime import RequestRecord, WaypointRuntime
from waypoint.config import get_settings
from waypoint.datastore import get_store
from waypoint.fastplan import build_plan
from waypoint.tools.catalog import InMemoryCatalog

HERE = Path(__file__).parent
OUT = HERE / "results" / "plan_explainer_eval.json"
MODELS = ["gemini-3.5-flash-lite", "gemini-3.5-flash"]
FIXED = "Each course sits in the earliest term"
# a claim about what is still to do, or about finishing the program
LEFT_CLAIM = re.compile(
    r"\b(?:left|remain\w*|still need\w*)\b"
    r"|\b(?:finish|complete|fulfil+)\w* (?:the|your|all) (?:\w+ )?(?:minor|major|specialist|program|requirements)\b"
    r"|\b(?:minor|major|specialist|program) (?:is|will be) (?:complete|finished|done)\b",
    re.I,
)


def judge(row: dict[str, Any]) -> dict[str, Any]:
    text = row["text"]
    row["fell_back"] = text.startswith(FIXED)
    row["sentences"] = len(re.findall(r"[.!?](?:\s|$)", text))
    row["claims_left"] = bool(LEFT_CLAIM.search(text))
    row["pass"] = (
        not row["fell_back"] and 2 <= row["sentences"] <= 5 and "advisor" in text.lower() and not row["claims_left"]
    )
    return row


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    totals = [r["total_s"] for r in rows]
    return {
        "pass_rate": round(sum(r["pass"] for r in rows) / len(rows), 2),
        "fell_back": sum(r["fell_back"] for r in rows),
        "total_p50_s": round(statistics.median(totals), 2),
        "total_p95_s": percentile(totals, 95),
        "cost_usd": round(sum(r["cost_usd"] for r in rows), 5),
        "rows": rows,
    }


async def run_model(model: str, questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    store, catalog = get_store(), InMemoryCatalog()
    runtime = WaypointRuntime.from_settings(get_settings().model_copy(update={"plan_explainer_model": model}))
    rows = []
    for q in questions:
        plan = build_plan(q["question"], "", store, catalog, date(2026, 11, 24))
        assert plan is not None
        first: list[float] = []

        def emit(kind: str, data: Any, first: list[float] = first) -> None:
            if kind == "token":
                first.append(time.perf_counter())

        started = time.perf_counter()
        record = RequestRecord(emit=emit)
        text = await runtime._explain_plan(plan, q["question"], record)
        row = {
            "id": q["id"],
            "ttft_s": round(first[0] - started, 2) if first else None,
            "total_s": round(time.perf_counter() - started, 2),
            "cost_usd": round(record.cost_usd, 6),
            "text": text,
        }
        rows.append(judge(row))
        print(json.dumps({k: v for k, v in row.items() if k != "text"} | {"model": model}), flush=True)
    return rows


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rejudge", action="store_true")
    args = parser.parse_args()
    if args.rejudge:
        saved = json.loads(OUT.read_text())
        result = {m: summarise([judge(r) for r in saved[m]["rows"]]) for m in saved}
    else:
        questions = [json.loads(line) for line in (HERE / "planning_questions.jsonl").read_text().splitlines() if line]
        result = {m: summarise(await run_model(m, questions)) for m in MODELS}
    OUT.write_text(json.dumps(result, indent=2))
    print(json.dumps({m: {k: v for k, v in r.items() if k != "rows"} for m, r in result.items()}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
