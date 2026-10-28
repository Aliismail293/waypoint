"""Put an old, flaky, oddly-shaped data source behind a clean interface, and serve it to agents over MCP.

`LegacyAdapter` does the three things every legacy integration needs:
- retry transient failures with exponential backoff,
- cache results for a while so a slow source isn't hit on every agent question,
- normalise each raw record into your own model.

`build_mcp_server` turns plain functions (usually thin wrappers around an adapter) into MCP tools.

    adapter = LegacyAdapter(
        fetch=old_api.get_rows,                       # (course, session) -> list[dict]
        normalise=row_to_section,                     # dict -> Section
        canonical_args=lambda c, s: (c.upper(), s),   # "mat224" and "MAT224" share a cache entry
        retry_on=(TimeoutError, ConnectionError),
    )

    def search_sections(course: str, session: str) -> dict:
        '''List the sections of a course in a session.'''
        return {"sections": [s.model_dump() for s in adapter.get(course, session)]}

    build_mcp_server("timetable", "Course sections.", [search_sections]).run()  # stdio
"""

import time
from collections.abc import Callable, Hashable, Iterable
from typing import Any

from mcp.server.mcpserver import MCPServer


class TransientError(Exception):
    """Raise (or subclass) this from a fetch for failures worth retrying: timeouts, 5xx, dropped connections."""


class LegacyAdapter[Raw, Model]:
    """Fetches raw records, retrying and caching, and returns them normalised.

    Args:
        fetch: calls the legacy source; returns an iterable of raw records.
        normalise: turns one raw record into your model.
        canonical_args: maps call args to the canonical args used both as the cache key and for `fetch`, so
            equivalent requests ("mat224" / "MAT224") share one cache entry. Default: args unchanged.
        retry_on: exception type(s) that mean "try again"; anything else propagates immediately.
        max_attempts: total tries, including the first.
        base_delay_s: first backoff; doubles on each retry (0.5, 1.0, 2.0, ...).
        cache_ttl_s: how long a result stays fresh; 0 disables caching.
        clock / sleep: injectable so tests can move time and skip real waits.
    """

    def __init__(
        self,
        fetch: Callable[..., Iterable[Raw]],
        normalise: Callable[[Raw], Model],
        *,
        canonical_args: Callable[..., tuple[Hashable, ...]] | None = None,
        retry_on: type[BaseException] | tuple[type[BaseException], ...] = TransientError,
        max_attempts: int = 3,
        base_delay_s: float = 0.5,
        cache_ttl_s: float = 900,
        max_cache_entries: int = 1024,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self._fetch = fetch
        self._normalise = normalise
        self._canonical_args = canonical_args
        self.retry_on = retry_on
        self.max_attempts = max_attempts
        self.base_delay_s = base_delay_s
        self.cache_ttl_s = cache_ttl_s
        self.max_cache_entries = max_cache_entries
        self._clock = clock
        self._sleep = sleep
        self._cache: dict[tuple[Hashable, ...], tuple[float, list[Model]]] = {}
        self.stats = {"source_calls": 0, "cache_hits": 0, "retries": 0}

    def get(self, *args: Hashable) -> list[Model]:
        """Normalised records for `args`, from cache if fresh, otherwise from the source."""
        key = self._canonical_args(*args) if self._canonical_args else args
        cached = self._cache.get(key)
        if cached is not None and self._clock() - cached[0] < self.cache_ttl_s:
            self.stats["cache_hits"] += 1
            return cached[1]
        # Normalise before caching, so a cache hit skips both the source call and the parsing.
        models = [self._normalise(raw) for raw in self._fetch_with_retry(key)]
        if self.cache_ttl_s > 0:
            if len(self._cache) >= self.max_cache_entries:
                self._cache.clear()  # crude but bounded: stale entries never pile up past the limit
            self._cache[key] = (self._clock(), models)
        return models

    def clear_cache(self) -> None:
        self._cache.clear()

    def _fetch_with_retry(self, args: tuple[Hashable, ...]) -> list[Raw]:
        for attempt in range(1, self.max_attempts + 1):
            self.stats["source_calls"] += 1
            try:
                return list(self._fetch(*args))  # list() so a lazy generator fails inside this try
            except self.retry_on:
                if attempt == self.max_attempts:
                    raise
                self.stats["retries"] += 1
                self._sleep(self.base_delay_s * 2 ** (attempt - 1))
        raise AssertionError("unreachable: the loop either returns or raises")


def build_mcp_server(name: str, instructions: str, tools: list[Callable[..., Any]]) -> MCPServer:
    """An MCP server exposing each function in `tools` as a tool.

    The tool's name is the function name, its description the docstring, and its input schema comes from the
    type hints, so write those for the model that will read them. Run it with `.run()` (stdio) or
    `.run("streamable-http")`. (`MCPServer` is what MCP Python SDK 2.x calls the old `FastMCP`.)
    """
    server: MCPServer = MCPServer(name=name, instructions=instructions)
    for tool in tools:
        server.add_tool(tool)
    return server
