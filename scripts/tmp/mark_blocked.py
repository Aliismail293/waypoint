"""Record the 403s seen by scripts/tmp/probe_robots.py as _BLOCKED.json markers, so the fetcher stops asking."""

import json
from pathlib import Path

for campus, host in [("utm", "utm"), ("utsg", "artsci"), ("utsc", "utsc")]:
    folder = Path("data/raw/uoft") / campus
    folder.mkdir(parents=True, exist_ok=True)
    note = {
        "url": f"https://{host}.calendar.utoronto.ca/robots.txt",
        "status": 403,
        "at": "2026-11-14",
        "action": "stopped crawling",
    }
    (folder / "_BLOCKED.json").write_text(json.dumps(note, indent=2))
