"""Live probe: print each stream event with its arrival time for one question (real Vertex calls, a few cents).

Set WAYPOINT_LLM=vertex, WAYPOINT_EMBEDDINGS=vertex and WAYPOINT_ANSWER_CACHE=off, then:
    uv run python scripts/probe_stream.py "question"
"""

import asyncio
import sys
import time

from waypoint.agents.runtime import WaypointRuntime


async def main(question: str) -> None:
    runtime = WaypointRuntime.from_settings()
    start = time.perf_counter()
    async for kind, data in runtime.ask_stream("probe", question):
        t = time.perf_counter() - start
        if kind == "final":
            print(f"{t:6.2f}s final route={data.record.route} cat={data.record.category}\n{data.text}")
        else:
            print(f"{t:6.2f}s {kind} {str(data)[:90]}")


asyncio.run(main(sys.argv[1]))
