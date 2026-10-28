"""MCP server exposing the legacy timetable through two tools: search_sections and get_section.

Run it over stdio (how Waypoint's Timetable agent launches it):
    uv run python -m waypoint.mcp.timetable
or over streamable HTTP for other MCP clients:
    uv run python -m waypoint.mcp.timetable --http --port 8765
"""

from mcp.server.mcpserver import MCPServer

from waypoint.mcp.timetable.adapter import SampleFileSource, TimetableAdapter

INSTRUCTIONS = (
    "Course sections (lecture/tutorial times, rooms, enrolment) for UTM courses. Terms look like '2026 Fall' "
    "or '2027 Winter'. Every result has a data_source field: if it says 'sample', the data is synthetic and "
    "must be presented to users as sample data, not real offerings."
)


def build_server(adapter: TimetableAdapter | None = None) -> MCPServer:
    adapter = adapter or TimetableAdapter(SampleFileSource())
    server = MCPServer(name="waypoint-timetable", instructions=INSTRUCTIONS)

    @server.tool()
    def search_sections(course: str, term: str) -> dict:
        """List all sections of a course in a term. course: code like "MAT224" or "MAT224H5".
        term: e.g. "2026 Fall" or "2027 Winter"."""
        try:
            sections = adapter.search_sections(course, term)
        except ValueError as exc:
            return {"error": str(exc)}
        return {
            "course": course.upper(),
            "term": term,
            "count": len(sections),
            "data_source": adapter.source.label,
            "sections": [s.model_dump() for s in sections],
        }

    @server.tool()
    def find_sections(
        term: str, department: str = "", days: list[str] | None = None, after: str = "", before: str = ""
    ) -> dict:
        """Find sections in a term whose every meeting fits a schedule. department: course prefix like "MAT"
        or "STA" (empty = all). days: e.g. ["Tuesday", "Thursday"]. after/before: 24h times like "12:00"
        and "18:00" (an afternoon is 12:00-18:00)."""
        try:
            sections = adapter.find_sections(term, department, days, after, before)
        except ValueError as exc:
            return {"error": str(exc)}
        return {
            "term": term,
            "count": len(sections),
            "data_source": adapter.source.label,
            "sections": [s.model_dump() for s in sections],
        }

    @server.tool()
    def get_section(section_id: str) -> dict:
        """Get one section by id, e.g. "MAT224H5-20271-LEC0101" (course-session-section)."""
        try:
            section = adapter.get_section(section_id)
        except ValueError:
            section = None
        if section is None:
            return {"found": False, "section_id": section_id}
        return {"found": True, **section.model_dump()}

    return server
