# Adding a university

Waypoint reads each institution through a *source adapter*. Everything downstream (the parsers' output, the data
store, the fact route, the course map, Programs, Enrolment check) works on rows that carry an `institution_id`, so a
second institution is a new adapter plus data, not a rewrite.

## What you write

1. **An adapter** in `waypoint/ingest/adapters/<name>.py` implementing `SourceAdapter`
   (`waypoint/ingest/adapters/base.py`): `info` (an `AdapterInfo` with `institution_id`, campus code, base URL,
   course-code suffixes, cache directory), then `list_course_pages`, `parse_course`, `list_programs`,
   `parse_program`, `list_policy_pages`, `parse_policy`. Look at `uoft.py` for a working one.
2. **A prerequisite parser rule set** if the calendar writes prerequisites differently. U of T's parser
   (`waypoint/ingest/prereq_parser.py`) turns text into a tree of `all_of` / `one_of` / leaves and marks anything it
   can't read as `unparsed`. Reuse it where the wording matches; add golden cases in
   `tests/data/prereq_golden.json` for every new pattern.
3. **A registry entry**: add the adapter to the list built in `waypoint/ingest/v3.py` and run
   `uv run python -m waypoint.ingest build-v3`. Output goes to `data/corpus/v3/*.jsonl`.

## Rules (same as for U of T)

- Public pages only. Read the site's robots.txt and terms first. If either forbids crawling, stop.
- Set `WAYPOINT_CRAWLER_CONTACT` (a contact address, never committed). The fetcher refuses to run without it.
- At most one request per 2 seconds per host; raw pages are cached under `data/raw/<institution>/<campus>`.
- On a 403 or 429 the fetcher raises `SourceBlocked`, writes a `_BLOCKED.json` marker and stops that site. Do not
  work around it (no proxies, no faked browsers, no rotating agents). Log it and ask for another source.
- Never invent data. A field you can't read stays empty and the UI says "check the calendar" with a link.

## Check before you ship

- `uv run python -m waypoint.ingest build-v3` prints coverage per campus and subject and the parser's
  fully-parsed percentage. Read the unparsed list; it is the to-do list.
- Add the adapter's pages to `tests/` as small fixtures (offline) and run `uv run pytest`.
- Campus names and subjects appear in the UI from the data (`/api/config` coverage), so nothing else needs editing
  for the navigation label; set `info.institution_name` and the header shows it.

## Not done yet

The institution label in the header is still the single U of T string, and `Settings` selects one institution.
Multi-institution switching in the UI is the next step after a second adapter exists.
