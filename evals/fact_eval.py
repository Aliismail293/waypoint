"""Deterministic fact eval: questions are generated from the structured tables, answers are checked by code.

No model is involved. Each expected answer is read straight from a table (prerequisite codes, exclusion codes,
credits, reverse prerequisites), never from the fact route's own output, so a bug in the route shows up as a miss.

    uv run python evals/fact_eval.py            # writes evals/results/v3_fact_eval.json
"""

import json
import re
import sys
import time
from pathlib import Path

from waypoint.datastore import get_store
from waypoint.facts import answer_fact, parse_fact_question
from waypoint.ingest.prereq_parser import course_leaves

OUT = Path(__file__).parent / "results" / "v3_fact_eval.json"
CODE = re.compile(r"\b[A-Z]{3}[A-D0-9]\d{2}[HY]\d\b")


def questions(store):
    for code, c in sorted(store.courses.items()):
        leaves = course_leaves(c["prereq_tree"])
        if c["prereq_parsed"] and leaves:
            yield ("prerequisites", f"What do I need before {code}?", set(leaves), code)
        elif not c["prereq_parsed"]:
            yield ("prerequisites_unparsed", f"What are the prerequisites for {code}?", {"calendar"}, code)
        required_by = store.courses_requiring(code)
        if required_by:
            yield ("unlocks", f"Which courses list {code} as a prerequisite?", set(required_by), code)
        if c["exclusion_codes"]:
            yield ("exclusions", f"What are the exclusions of {code}?", set(c["exclusion_codes"]), code)
        yield ("credits", f"How many credits is {code}?", {f"{float(c['credits']):g}"}, code)


def main() -> int:
    store = get_store()
    rows = []
    for kind, question, expected, code in questions(store):
        start = time.perf_counter()
        query = parse_fact_question(question, store)
        answer = answer_fact(query, store) if query else None
        ms = (time.perf_counter() - start) * 1000
        text = answer.text if answer else ""
        if kind == "credits":
            ok = bool(answer) and any(e in text for e in expected)
        elif kind == "prerequisites_unparsed":
            ok = bool(answer) and "calendar" in text.lower()
        else:
            ok = bool(answer) and expected <= set(CODE.findall(text))
        cited = bool(answer and answer.chunk_ids)
        rows.append({"kind": kind, "course": code, "question": question, "correct": ok, "cited": cited, "ms": ms})
    by_kind: dict[str, list[bool]] = {}
    for r in rows:
        by_kind.setdefault(r["kind"], []).append(r["correct"])
    times = sorted(r["ms"] for r in rows)
    result = {
        "questions": len(rows),
        "accuracy_pct": round(100 * sum(r["correct"] for r in rows) / len(rows), 1),
        "cited_pct": round(100 * sum(r["cited"] for r in rows) / len(rows), 1),
        "p95_ms": round(times[int(0.95 * (len(times) - 1))], 2),
        "by_kind": {
            k: {"n": len(v), "accuracy_pct": round(100 * sum(v) / len(v), 1)} for k, v in sorted(by_kind.items())
        },
        "failures": [r for r in rows if not r["correct"]][:10],
    }
    OUT.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "failures"}, indent=2))
    for f in result["failures"]:
        print("FAIL", f["kind"], f["question"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
