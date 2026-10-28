"""Plan validity before/after the critic (Phase 2 item 2).

Runs each planning question in evals/planning_questions.jsonl through the full agent tree twice: with the
critic off (the Phase 1 planner) and on. The judge is deterministic: the plan lines in the final answer
("- **2026 Fall**: MAT224H5, STA256H5") are extracted and run through the same validate_plan_text the agents
use. A plan is valid if it has at least one term and zero validation errors.

    uv run python evals/plan_validity.py --name critic_ab      # live: needs WAYPOINT_LLM=vertex etc.
"""

import argparse
import asyncio
import json
import re
from pathlib import Path
from typing import Any

from waypoint.agents.runtime import WaypointRuntime
from waypoint.config import get_settings
from waypoint.ingest.calendar_parser import COURSE_CODE
from waypoint.tools.plan import validate_plan_text

HERE = Path(__file__).parent
TERM = re.compile(r"\b(20\d\d)\s+(Fall|Winter|Summer)\b", re.IGNORECASE)


def extract_plan(answer: str) -> str:
    """The plan in the answer as 'Term: CODE, CODE; ...'.

    Accepts one line per term ("- **2026 Fall**: MAT224H5, STA256H5") or a term line followed by indented
    bullets, one course each. Text in parentheses is ignored, so "(needs MAT224H5)" isn't read as planned.
    """
    terms: list[tuple[str, list[str]]] = []
    current: list[str] | None = None
    for line in answer.splitlines():
        if not line.strip():
            continue
        term = TERM.search(line)
        if term and len(line[: term.start()].strip(" -*#")) == 0:
            current = []
            terms.append((f"{term.group(1)} {term.group(2).title()}", current))
            current += COURSE_CODE.findall(line[term.end() :].split("(")[0])
        elif current is not None and re.match(r"^\s+[-*+]\s", line):
            current += COURSE_CODE.findall(line.split("(")[0])
        else:
            current = None
    return "; ".join(f"{t}: {', '.join(dict.fromkeys(codes))}" for t, codes in terms if codes)


async def run_variant(critic: bool, questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    settings = get_settings().model_copy(update={"critic": critic})
    runtime = WaypointRuntime.from_settings(settings)
    rows = []
    for q in questions:
        answer = await runtime.ask(f"plan-eval-{q['id']}", q["question"])
        plan = extract_plan(answer.text)
        check = validate_plan_text(plan, q["completed"], runtime.deps.catalog) if plan else {"valid": False}
        m = answer.metrics()
        rows.append(
            {
                "id": q["id"],
                "critic": critic,
                "valid": bool(check["valid"]),
                "errors": check.get("errors", ["no plan lines found"]),
                "plan": plan,
                "critic_rounds": m["critic_rounds"],
                "latency_ms": m["latency_ms"],
                "model_calls": m["model_calls"],
                "cost_usd": m["cost_usd"],
                "limit_hit": m["limit_hit"],
                "answer": answer.text,
            }
        )
        print(json.dumps({k: v for k, v in rows[-1].items() if k != "answer"}), flush=True)
    return rows


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    return {
        "questions": n,
        "plan_validity": round(sum(r["valid"] for r in rows) / n, 3) if n else 0.0,
        "mean_latency_s": round(sum(r["latency_ms"] for r in rows) / n / 1000, 1) if n else 0.0,
        "mean_model_calls": round(sum(r["model_calls"] for r in rows) / n, 1) if n else 0.0,
        "mean_cost_usd": round(sum(r["cost_usd"] for r in rows) / n, 4) if n else 0.0,
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", default="critic_ab")
    parser.add_argument("--only", choices=["off", "on"])
    parser.add_argument("--ids", help="comma-separated question ids (default: all)")
    args = parser.parse_args()
    questions = [json.loads(line) for line in (HERE / "planning_questions.jsonl").read_text().splitlines() if line]
    if args.ids:
        questions = [q for q in questions if q["id"] in args.ids.split(",")]
    result: dict[str, Any] = {}
    for label, critic in (("off", False), ("on", True)):
        if args.only and args.only != label:
            continue
        rows = await run_variant(critic, questions)
        result[f"critic_{label}"] = {"summary": summarise(rows), "rows": rows}
    out = HERE / "results" / f"{args.name}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(result, indent=2))
    print(json.dumps({k: v["summary"] for k, v in result.items()}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
