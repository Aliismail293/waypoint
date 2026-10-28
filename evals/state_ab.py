"""Before/after for conversation-state optimisation (Phase 2 item 6).

Runs the multi-turn conversations in evals/conversations.jsonl with state optimisation off (full history)
and on (the v1 policy measured on 2026-10-28: pruned old tool outputs + summary compaction every 3 turns),
against the live stack. Reports, per variant:
input tokens per turn, cost per conversation (including the summariser's own calls), P95 turn latency,
implicit cache hit rate, and how many follow-ups still resolved (the answer mentions what the turn expects).

    WAYPOINT_LLM=vertex ... uv run python evals/state_ab.py --name state_ab
"""

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from waypoint.agents.runtime import WaypointRuntime
from waypoint.config import get_settings
from waypoint.observability.cost import cost_usd

HERE = Path(__file__).parent


def percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(pct / 100 * len(ordered) + 0.5) - 1))
    return ordered[index] if ordered else 0.0


async def run_variant(state_opt: bool, conversations: list[dict[str, Any]]) -> dict[str, Any]:
    v1 = {"prune_tool_outputs": True, "compaction_interval": 3, "compaction_overlap": 1}
    settings = get_settings().model_copy(update={"state_opt": state_opt, **v1})
    runtime = WaypointRuntime.from_settings(settings)
    turns, conversation_costs = [], []
    for conv in conversations:
        session_id = None
        cost = 0.0
        summary_calls_before = len(runtime.summarizer.usage.log) if runtime.summarizer else 0
        for i, turn in enumerate(conv["turns"]):
            answer = await runtime.ask(f"state-ab-{conv['id']}", turn["q"], session_id)
            session_id = answer.session_id
            m = answer.metrics()
            orchestrator_in = sum(c.input_tokens for c in answer.record.model_calls if c.agent == "waypoint")
            resolved = all(s.lower() in answer.text.lower() for s in turn.get("expect", []))
            turns.append(
                {
                    "conversation": conv["id"],
                    "turn": i + 1,
                    "input_tokens": m["input_tokens"],
                    "orchestrator_input_tokens": orchestrator_in,
                    "cached_tokens": m["cached_tokens"],
                    "output_tokens": m["output_tokens"],
                    "cost_usd": m["cost_usd"],
                    "latency_ms": m["latency_ms"],
                    "model_calls": m["model_calls"],
                    "resolved": resolved,
                    "answer": answer.text,
                }
            )
            cost += m["cost_usd"]
            print(json.dumps({k: v for k, v in turns[-1].items() if k != "answer"}), flush=True)
        await asyncio.sleep(5)  # let the post-answer compaction for this conversation finish
        if runtime.summarizer:
            for model, inp, out, thinking in runtime.summarizer.usage.log[summary_calls_before:]:
                cost += cost_usd(model, inp, out, 0, thinking)
        conversation_costs.append(round(cost, 6))
    n = len(turns)
    later = [t for t in turns if t["turn"] >= 3]  # where history has built up
    return {
        "summary": {
            "turns": n,
            "mean_input_tokens_per_turn": round(sum(t["input_tokens"] for t in turns) / n),
            "mean_input_tokens_per_turn_3plus": round(sum(t["input_tokens"] for t in later) / len(later)),
            "mean_orchestrator_input_tokens_3plus": round(
                sum(t["orchestrator_input_tokens"] for t in later) / len(later)
            ),
            "mean_cost_per_conversation_usd": round(sum(conversation_costs) / len(conversation_costs), 5),
            "p95_turn_latency_s": round(percentile([t["latency_ms"] for t in turns], 95) / 1000, 1),
            "cache_hit_rate": round(
                sum(t["cached_tokens"] for t in turns) / max(1, sum(t["input_tokens"] for t in turns)), 3
            ),
            "followups_resolved": f"{sum(t['resolved'] for t in turns)}/{n}",
            "summarizer_calls": runtime.summarizer.usage.calls if runtime.summarizer else 0,
        },
        "conversation_costs": conversation_costs,
        "turns": turns,
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", default="state_ab")
    args = parser.parse_args()
    conversations = [json.loads(line) for line in (HERE / "conversations.jsonl").read_text().splitlines() if line]
    result = {"before": await run_variant(False, conversations), "after": await run_variant(True, conversations)}
    out = HERE / "results" / f"{args.name}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(result, indent=2))
    print(json.dumps({k: v["summary"] for k, v in result.items()}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
