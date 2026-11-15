"""Weekly live smoke test: 5 real questions through the live agents, with a hard cost cap.

Needs Google credentials (the workflow supplies them) and WAYPOINT_LLM=vertex. Exits 1 on any failure so the workflow
opens an issue. The cap is checked after every question; if it's passed the run stops and fails.
"""

import asyncio
import sys

from waypoint.agents.runtime import WaypointRuntime
from waypoint.config import check_model_ids, get_settings

CAP_USD = 0.10
QUESTIONS = [
    ("What do I need before MAT224H5?", "MAT223H5"),
    ("Which courses list MAT223H5 as a prerequisite?", "MAT224H5"),
    ("How many credits is MAT224H5?", "0.5"),
    ("What are the exclusions of MAT224H5?", "MAT240H5"),
    ("Is MAT224H5 offered at UTSC under another code?", "MATB24H3"),
]


async def main() -> int:
    settings = get_settings()
    check_model_ids(settings)
    if settings.llm == "mock":
        print("WAYPOINT_LLM is mock: a live smoke test needs WAYPOINT_LLM=vertex.")
        return 1
    runtime = WaypointRuntime.from_settings(settings)
    spent, failed = 0.0, []
    for question, expected in QUESTIONS:
        answer = await runtime.ask("smoke", question)
        spent += float(answer.metrics().get("cost_usd") or 0)
        ok = expected in answer.text
        print(f"{'ok  ' if ok else 'FAIL'} {question} ({answer.latency_ms:.0f} ms, ${spent:.4f} so far)")
        if not ok:
            failed.append(question)
        if spent > CAP_USD:
            print(f"Cost cap ${CAP_USD:.2f} passed; stopping.")
            return 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
