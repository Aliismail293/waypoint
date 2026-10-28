import argparse

from waypoint.mcp.timetable.server import build_server


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m waypoint.mcp.timetable")
    parser.add_argument("--http", action="store_true", help="serve streamable HTTP instead of stdio")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = build_server()
    if args.http:
        server.run(transport="streamable-http", port=args.port)
    else:
        server.run(transport="stdio")


if __name__ == "__main__":
    main()
