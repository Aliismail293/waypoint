"""Offline checks of the V3 eval tooling: the knob adapter's pure parts and the deterministic fact eval."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path("evals")))

import fact_eval  # noqa: E402
import v3_adapter  # noqa: E402

from waypoint.datastore import get_store  # noqa: E402


def test_windows_respect_size_and_overlap() -> None:
    text = "abcdefghij" * 10
    assert v3_adapter.windows(text, 0, 0) == [text]
    assert v3_adapter.windows(text, 200, 50) == [text]
    parts = v3_adapter.windows(text, 40, 10)
    assert all(len(p) <= 40 for p in parts) and parts[0][-10:] == parts[1][:10]
    assert "".join(p[:30] for p in parts[:-1]) + parts[-1] == text


def test_tokens_and_norm() -> None:
    assert v3_adapter.tokens("What are the prerequisites for MAT224H5?") == ["prerequisites", "mat224h5"]
    assert v3_adapter.norm([1.0, 3.0, 2.0]) == [0.0, 1.0, 0.5]
    assert v3_adapter.norm([2.0, 2.0]) == [0.0, 0.0]


def test_fact_questions_expect_codes_from_the_tables_not_from_the_route() -> None:
    store = get_store()
    rows = list(fact_eval.questions(store))
    kinds = {r[0] for r in rows}
    assert {"prerequisites", "unlocks", "exclusions", "credits"} <= kinds
    prereq = next(r for r in rows if r[1] == "What do I need before MAT224H5?")
    assert prereq[2] == {"MAT102H5", "MAT223H5"} or {"MAT223H5"} <= prereq[2]
