"""Small end-to-end eval for the demo. Expected answers are computed from the data at run time, not typed in.

PARKS_LLM=vertex uv run python -m parks_demo.eval
"""

import asyncio
import json
import re
from pathlib import Path

from parks_demo.adapter import AttendanceData
from parks_demo.agent import Assistant
from parks_demo.config import get_settings
from parks_demo.mcp_server import make_source


def number_patterns(n: int) -> list[str]:
    return [f"{n:,}", str(n), f"{n:,}".replace(",", " ")]


def cases(data: AttendanceData) -> list[dict]:
    top = data.top_places("2025-26", "National Park", 1)[0]
    jasper = next(r for r in data.attendance("jasper national park") if r.fiscal_year == "2024-25")
    total = data.total("2024-25")["visitors"]
    unreported = next(r for r in data.records() if r.fiscal_year == "2024-25" and r.status == "unable_to_report")
    rim = {r.fiscal_year: r.visitors for r in data.attendance("pacific rim national park")}
    return [
        {
            "q": "Which national park had the most visitors in 2025-26, and how many?",
            "any": number_patterns(top.visitors or 0),
            "all": [top.place.split()[0]],
        },
        {"q": "How many people visited Jasper National Park in 2024-25?", "any": number_patterns(jasper.visitors or 0)},
        {"q": "What was total attendance across all Parks Canada places in 2024-25?", "any": number_patterns(total)},
        {
            "q": f"How many visitors did {unreported.place} have in 2024-25?",
            "any": [
                r"unable to report",
                r"didn't report",
                r"did not report",
                r"not report",
                r"no (attendance )?(figure|number|data)",
            ],
        },
        {
            "q": "Did visits to Pacific Rim National Park Reserve go up or down from 2023-24 to 2024-25?",
            "any": ["up", "increase", "rose", "grew"]
            if (rim["2024-25"] or 0) > (rim["2023-24"] or 0)
            else ["down", "decrease", "fell", "declin"],
        },
        {
            "q": "What's the weather forecast in Banff this weekend?",
            "any": [r"only (answer|help)", r"can't", r"cannot", r"not able", r"attendance"],
            "max_tool_calls": 0,
        },
    ]


async def main() -> None:
    settings = get_settings()
    data = AttendanceData(make_source(settings))
    assistant = Assistant(settings)
    rows = []
    for case in cases(data):
        reply = await assistant.ask("eval", case["q"])
        text = reply.text.lower()
        ok = any(re.search(p.lower(), text) for p in case["any"]) and all(
            s.lower() in text for s in case.get("all", [])
        )
        if "max_tool_calls" in case:
            ok = ok and reply.metrics["tool_calls"] <= case["max_tool_calls"]
        rows.append({"q": case["q"], "correct": ok, "answer": reply.text, **reply.metrics})
        print(
            json.dumps(
                {
                    "correct": ok,
                    "q": case["q"][:60],
                    "latency_ms": reply.metrics["latency_ms"],
                    "cost_usd": reply.metrics["cost_usd"],
                }
            )
        )
    latencies = sorted(r["latency_ms"] for r in rows)
    summary = {
        "questions": len(rows),
        "correct": sum(r["correct"] for r in rows),
        "p95_latency_s": round(latencies[min(len(latencies) - 1, round(0.95 * len(latencies) + 0.5) - 1)] / 1000, 1),
        "mean_cost_usd": round(sum(r["cost_usd"] for r in rows) / len(rows), 5),
        "source": settings.source,
    }
    out = Path(__file__).parent.parent / "eval_results.json"
    out.write_text(json.dumps({"summary": summary, "rows": rows}, indent=2))
    print(json.dumps(summary))


if __name__ == "__main__":
    asyncio.run(main())
