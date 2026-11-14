"""CI gate: offline router accuracy on the labelled questions must stay at or above 90%."""

import asyncio
import importlib.util
import sys
from pathlib import Path

from waypoint.config import Settings

SPEC = importlib.util.spec_from_file_location("router_eval", Path(__file__).parent.parent / "evals" / "router_eval.py")
assert SPEC and SPEC.loader
router_eval = importlib.util.module_from_spec(SPEC)
sys.modules["router_eval"] = router_eval
SPEC.loader.exec_module(router_eval)


def test_labelled_set_covers_all_routes_and_is_big_enough():
    rows = router_eval.load()
    assert len(rows) >= 30
    assert {r["route"] for r in rows} == {"simple_lookup", "timetable", "planning", "unsure"}


def test_offline_router_accuracy_gate():
    report = asyncio.run(router_eval.evaluate(router_eval.load(), Settings(llm="mock", router="on")))
    assert report["route_accuracy"] >= router_eval.THRESHOLD, report["confusions"]
    assert report["wrong_fast_routes"] == 0
