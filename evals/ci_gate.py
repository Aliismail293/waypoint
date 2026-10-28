"""CI eval gate: fail a change that drops accuracy, slows answers down, or breaks a safety guard.

Runs fully offline (mock LLM, mock embeddings, in-memory stores, the committed golden set), so CI needs no
secrets. A PR fails if, against its base branch measured on the same runner:
- answer accuracy drops by more than 3 points, or
- P95 latency rises by more than 20%, or P95 model calls per question rise by more than 20%, or
- any safety case fails (absolute, not relative).

What the offline numbers mean: the mock LLM answers deterministically from tool results, and ragaudit's
mock judge scores deterministically, so accuracy here is a regression signal for retrieval, routing and
citation plumbing, not a quality score (real quality is eval_v0 and later live runs). Offline latency measures
Waypoint's own code path; model calls per question is the main driver of live latency (~2 s per call), so
it's gated too. Latency differences under LATENCY_FLOOR_MS are ignored as runner jitter.

    uv run python evals/ci_gate.py measure --out head.json [--repo DIR]
    uv run python evals/ci_gate.py compare --base base.json --head head.json
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

ACCURACY_DROP_POINTS = 3.0
P95_RISE = 0.20
LATENCY_FLOOR_MS = 50.0
SPLITS = ("dev", "test")
OFFLINE_ENV = {
    "WAYPOINT_LLM": "mock",
    "WAYPOINT_EMBEDDINGS": "mock",
    "WAYPOINT_STORE": "memory",
    "WAYPOINT_CATALOG": "memory",
    "WAYPOINT_SESSIONS": "memory",
    "WAYPOINT_CLOUD_TRACE": "false",
}


def percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, round(pct / 100 * len(ordered) + 0.5) - 1))  # nearest-rank
    return ordered[index]


def run(cmd: list[str], repo: Path) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("WAYPOINT_", "RAGAUDIT_"))} | OFFLINE_ENV
    return subprocess.run(cmd, cwd=repo, env=env, capture_output=True, text=True)


def measure(repo: Path) -> dict[str, Any]:
    correct = total = hits = 0
    latencies: list[float] = []
    model_calls: list[float] = []
    for split in SPLITS:
        name = f"ci_{split}"
        cmd = ["uv", "run", "--group", "eval", "python", "evals/run_eval.py", "evaluate", "--llm", "mock"]
        result = run([*cmd, "--mode", "agent", "--split", split, "--name", name], repo)
        if result.returncode != 0:
            sys.exit(f"offline eval failed on {split}:\n{result.stdout[-2000:]}\n{result.stderr[-2000:]}")
        summary = json.loads((repo / "evals" / "results" / f"{name}.json").read_text())
        n = summary["questions"]
        total += n
        correct += round(summary["answer_accuracy"] * n)
        hits += round(summary["retrieval_hit_rate"] * n)
        for line in (repo / "evals" / "results" / f"{name}.queries.jsonl").read_text().splitlines():
            row = json.loads(line)
            latencies.append(row["latency_ms"])
            model_calls.append(row["model_calls"])

    safety: dict[str, Any] = {"cases": 0, "passed": 0, "failed": []}
    if (repo / "evals" / "safety_suite.py").exists():
        run(["uv", "run", "python", "evals/safety_suite.py"], repo)
        report = json.loads((repo / "evals" / "results" / "safety_offline.json").read_text())
        safety = {
            "cases": report["summary"]["cases"],
            "passed": report["summary"]["passed"],
            "failed": [r["id"] for r in report["results"] if not r["passed"]],
        }
    return {
        "questions": total,
        "accuracy_pct": round(100 * correct / total, 2) if total else 0.0,
        "retrieval_hit_pct": round(100 * hits / total, 2) if total else 0.0,
        "p95_latency_ms": round(percentile(latencies, 95), 1),
        "p95_model_calls": percentile(model_calls, 95),
        "safety": safety,
    }


def compare(base: dict[str, Any], head: dict[str, Any]) -> list[str]:
    failures = []
    drop = base["accuracy_pct"] - head["accuracy_pct"]
    if drop > ACCURACY_DROP_POINTS:
        failures.append(f"accuracy dropped {drop:.1f} points ({base['accuracy_pct']}% -> {head['accuracy_pct']}%)")
    b, h = base["p95_latency_ms"], head["p95_latency_ms"]
    if h > b * (1 + P95_RISE) and h - b > LATENCY_FLOOR_MS:
        failures.append(f"P95 latency rose {100 * (h / b - 1):.0f}% ({b} ms -> {h} ms)")
    b, h = base["p95_model_calls"], head["p95_model_calls"]
    if b and h > b * (1 + P95_RISE):
        failures.append(f"P95 model calls per question rose {100 * (h / b - 1):.0f}% ({b} -> {h})")
    safety = head["safety"]
    if safety["cases"] == 0:
        failures.append("safety suite didn't run")
    elif safety["failed"]:
        failures.append(f"safety cases failed: {', '.join(safety['failed'])}")
    return failures


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    m = sub.add_parser("measure")
    m.add_argument("--out", type=Path, required=True)
    m.add_argument("--repo", type=Path, default=Path.cwd())
    c = sub.add_parser("compare")
    c.add_argument("--base", type=Path, required=True)
    c.add_argument("--head", type=Path, required=True)
    args = parser.parse_args()

    if args.command == "measure":
        result = measure(args.repo.resolve())
        args.out.write_text(json.dumps(result, indent=2))
        print(json.dumps(result, indent=2))
        return
    base, head = json.loads(args.base.read_text()), json.loads(args.head.read_text())
    failures = compare(base, head)
    print("| Metric | Base | Head |\n|---|---|---|")
    for key in ("accuracy_pct", "retrieval_hit_pct", "p95_latency_ms", "p95_model_calls"):
        print(f"| {key} | {base[key]} | {head[key]} |")
    print(f"| safety | | {head['safety']['passed']}/{head['safety']['cases']} |")
    for failure in failures:
        print(f"GATE FAILED: {failure}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
