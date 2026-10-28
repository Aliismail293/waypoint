"""Ask Waypoint one question from the command line (uses whatever WAYPOINT_* settings are in the env).

uv run python -m waypoint.agents "What are the prerequisites for MAT224?"
"""

import asyncio
import sys

from waypoint.agents.runtime import WaypointRuntime


async def main(question: str) -> None:
    answer = await WaypointRuntime.from_settings().ask("cli-user", question)
    print(answer.text)
    print("\n---")
    print("citations:", answer.citations)
    print("retrieved:", answer.record.retrieved_chunk_ids)
    print("agents:", answer.record.agents)
    print("tools:", answer.record.tool_calls)
    tokens_in = sum(c.input_tokens for c in answer.record.model_calls)
    tokens_out = sum(c.output_tokens + c.thinking_tokens for c in answer.record.model_calls)
    print(f"model calls: {len(answer.record.model_calls)}, tokens in/out: {tokens_in}/{tokens_out}")
    print(f"latency: {answer.latency_ms:.0f} ms")


if __name__ == "__main__":
    asyncio.run(main(" ".join(sys.argv[1:])))
