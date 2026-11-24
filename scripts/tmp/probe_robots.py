"""Fetch robots.txt for each calendar host once, politely. Prints status and body, never the User-Agent."""

import os
import time

import httpx

contact = os.environ.get("WAYPOINT_CRAWLER_CONTACT")
if not contact:
    raise SystemExit("WAYPOINT_CRAWLER_CONTACT is unset: stop crawling")
ua = f"WaypointBot/0.3 (student project; +https://github.com/Aliismail293/waypoint; contact: {contact})"
for host in ["utm", "artsci", "utsc"]:
    url = f"https://{host}.calendar.utoronto.ca/robots.txt"
    r = httpx.get(url, headers={"User-Agent": ua}, timeout=30, follow_redirects=True)
    print("=====", host, r.status_code)
    print(r.text[:1500])
    time.sleep(2)
