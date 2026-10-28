import argparse

from waypoint.mcp.waypoint.server import build_server


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m waypoint.mcp.waypoint")
    parser.add_argument("--http", action="store_true", help="serve streamable HTTP on 127.0.0.1 instead of stdio")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    server = build_server()
    if args.http:
        server.run(transport="streamable-http", host="127.0.0.1", port=args.port)
    else:
        server.run(transport="stdio")


if __name__ == "__main__":
    main()
