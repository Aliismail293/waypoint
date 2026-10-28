"""The CI gate's pass/fail rules (inputs are synthetic; real numbers come from `ci_gate.py measure`)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path("evals")))
from ci_gate import compare  # noqa: E402

BASE = {
    "accuracy_pct": 50.0,
    "retrieval_hit_pct": 80.0,
    "p95_latency_ms": 100.0,
    "p95_model_calls": 5,
    "safety": {"cases": 8, "passed": 8, "failed": []},
}


def head(**changes):
    return BASE | changes


def test_unchanged_passes():
    assert compare(BASE, head()) == []


def test_accuracy_drop_over_3_points_fails():
    assert compare(BASE, head(accuracy_pct=47.0)) == []
    assert "accuracy dropped" in compare(BASE, head(accuracy_pct=46.9))[0]


def test_p95_latency_rise_over_20_percent_fails_above_jitter_floor():
    assert compare(BASE, head(p95_latency_ms=140.0)) == []  # +40% but only 40 ms: jitter
    slow = {**BASE, "p95_latency_ms": 1000.0}
    assert compare(slow, {**slow, "p95_latency_ms": 1199.0}) == []
    assert "P95 latency" in compare(slow, {**slow, "p95_latency_ms": 1300.0})[0]


def test_model_call_rise_fails():
    assert "model calls" in compare(BASE, head(p95_model_calls=7))[0]


def test_any_safety_failure_fails():
    assert "inj-notice" in compare(BASE, head(safety={"cases": 8, "passed": 7, "failed": ["inj-notice"]}))[0]
    assert "didn't run" in compare(BASE, head(safety={"cases": 0, "passed": 0, "failed": []}))[0]
