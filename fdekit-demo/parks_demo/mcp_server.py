"""The attendance data as MCP tools, built with fde-kit's build_mcp_server.

uv run python -m parks_demo.mcp_server          # stdio (how the agent launches it)
"""

from typing import Any

from fde_kit.mcp_legacy import build_mcp_server
from mcp.server.mcpserver import MCPServer

from parks_demo.adapter import LICENCE, AttendanceData
from parks_demo.config import Settings, get_settings
from parks_demo.source import CsvSource, PostgresSource, Source

INSTRUCTIONS = (
    "Parks Canada attendance (visitors) per national park, national historic site and marine conservation "
    "area, by fiscal year (April to March, e.g. '2024-25'). Status 'unable_to_report' or 'temporarily_closed' "
    "means there is no number for that place and year. " + LICENCE
)


def make_source(settings: Settings) -> Source:
    if settings.source == "postgres":
        dsn = (
            f"host={settings.db_host} port={settings.db_port} dbname={settings.db_name} "
            f"user={settings.db_user} password={settings.db_password}"
        )
        return PostgresSource(dsn, settings.db_schema)
    return CsvSource()


def build_server(data: AttendanceData) -> MCPServer:
    def list_years() -> dict[str, Any]:
        """The fiscal years available, e.g. ["2023-24", "2024-25", "2025-26"]."""
        return {"fiscal_years": data.years(), "source": LICENCE}

    def find_places(query: str) -> dict[str, Any]:
        """Places whose name contains every word of the query, e.g. "banff" or "fort historic"."""
        places = data.find_places(query)
        return {"count": len(places), "places": places[:25]}

    def place_attendance(place: str) -> dict[str, Any]:
        """Attendance for every fiscal year at the places matching `place` (e.g. "Jasper National Park")."""
        rows = data.attendance(place)
        return {"count": len(rows), "rows": [r.model_dump() for r in rows[:30]], "source": LICENCE}

    def top_places(fiscal_year: str, kind: str = "", limit: int = 10) -> dict[str, Any]:
        """Most-visited places in a fiscal year. kind: "National Park", "National Historic Site",
        "National Marine Conservation Area", "National Urban Park" or empty for all. limit: at most 25."""
        rows = data.top_places(fiscal_year, kind, min(limit, 25))
        return {"fiscal_year": fiscal_year, "kind": kind or "all", "rows": [r.model_dump() for r in rows]}

    def total_attendance(fiscal_year: str, kind: str = "") -> dict[str, Any]:
        """Total visitors in a fiscal year (optionally one kind of place), and how many places didn't report."""
        return {"fiscal_year": fiscal_year, "kind": kind or "all", **data.total(fiscal_year, kind)}

    return build_mcp_server(
        "parks-attendance", INSTRUCTIONS, [list_years, find_places, place_attendance, top_places, total_attendance]
    )


if __name__ == "__main__":
    build_server(AttendanceData(make_source(get_settings()))).run(transport="stdio")
