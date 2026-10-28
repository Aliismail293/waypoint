"""Ingestion CLI.

    uv run python -m waypoint.ingest fetch        # polite fetch of calendar pages into data/raw/calendar
    uv run python -m waypoint.ingest parse        # cached HTML -> data/corpus/*.jsonl (committed)
    uv run python -m waypoint.ingest pgvector     # embed new/changed chunks, upsert into Cloud SQL
    uv run python -m waypoint.ingest bigquery     # load courses + programs into BigQuery

`parse` is offline and deterministic. `pgvector` and `bigquery` need GCP; set WAYPOINT_EMBEDDINGS=vertex
for real embeddings (otherwise the offline hash embedder is used, which is only for tests).
"""

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import unquote

from waypoint.config import get_settings
from waypoint.ingest.calendar_parser import parse_page
from waypoint.ingest.fetch import ALL_PAGES, DEFAULT_CACHE, CalendarFetcher

CORPUS_DIR = Path("data/corpus")


def cmd_fetch(_: argparse.Namespace) -> None:
    fetcher = CalendarFetcher()
    for path in ALL_PAGES:
        try:
            print(f"{path}: {len(fetcher.get(path))} bytes")
        except Exception as exc:  # keep going: one blocked page shouldn't stop the rest
            print(f"{path}: FAILED ({exc})", file=sys.stderr)


def cmd_parse(args: argparse.Namespace) -> None:
    chunks, courses, programs = [], [], []
    for html_file in sorted(Path(args.cache).glob("*.html")):
        path = "/" + unquote(html_file.stem)
        page = parse_page(path, html_file.read_text())
        chunks += [c.to_row() for c in page.chunks]
        courses += [c.to_row() for c in page.courses]
        programs += [p.to_row() for p in page.programs]
        print(f"{path}: {len(page.chunks)} chunks, {len(page.courses)} courses, {len(page.programs)} programs")
    ids = [c["chunk_id"] for c in chunks]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        raise SystemExit(f"duplicate chunk ids: {sorted(duplicates)}")
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    for name, rows in (("calendar_chunks", chunks), ("courses", courses), ("programs", programs)):
        with open(CORPUS_DIR / f"{name}.jsonl", "w") as out:
            for row in rows:
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"wrote {len(chunks)} chunks, {len(courses)} courses, {len(programs)} programs to {CORPUS_DIR}/")


def cmd_pgvector(args: argparse.Namespace) -> None:
    from waypoint.ingest.load import load_chunks_into_pgvector

    load_chunks_into_pgvector(CORPUS_DIR / "calendar_chunks.jsonl", get_settings(), prune=args.prune)


def cmd_bigquery(_: argparse.Namespace) -> None:
    from waypoint.ingest.bigquery_loader import load_into_bigquery

    load_into_bigquery(CORPUS_DIR, get_settings())


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m waypoint.ingest")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("fetch").set_defaults(func=cmd_fetch)
    parse = sub.add_parser("parse")
    parse.add_argument("--cache", default=str(DEFAULT_CACHE))
    parse.set_defaults(func=cmd_parse)
    pg = sub.add_parser("pgvector")
    pg.add_argument("--prune", action="store_true", help="delete chunks no longer in the corpus")
    pg.set_defaults(func=cmd_pgvector)
    sub.add_parser("bigquery").set_defaults(func=cmd_bigquery)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
