#!/usr/bin/env bash
# Checks that Server-Sent Events arrive incrementally through a URL (Firebase Hosting, the tagged revision, or Cloud Run).
# Usage: scripts/check_stream.sh https://SITE.web.app [ID_TOKEN]
# Without a token the server needs WAYPOINT_DEV_SKIP_AUTH=true (local runs); a deployed service needs a Google ID token.
# Passes when the first event arrives well before the stream ends (buffering would deliver everything at the end).
set -euo pipefail
URL="${1:?usage: check_stream.sh URL [ID_TOKEN]}"
TOKEN="${2:-}"
AUTH=()
if [ -n "$TOKEN" ]; then AUTH=(-H "Authorization: Bearer $TOKEN"); fi
OUT="$(mktemp)"
curl -sS -N --max-time 60 -X POST "$URL/api/chat/stream" -H 'Content-Type: application/json' "${AUTH[@]}" \
  -d '{"message":"What do I need before MAT224H5?"}' -w '\nTIME_TOTAL=%{time_total}\nTIME_FIRST_BYTE=%{time_starttransfer}\n' > "$OUT"
grep -c '^event:' "$OUT" | sed 's/^/events: /'
grep -E 'TIME_' "$OUT"
FIRST="$(grep TIME_FIRST_BYTE "$OUT" | cut -d= -f2)"
TOTAL="$(grep TIME_TOTAL "$OUT" | cut -d= -f2)"
python3 -c "import sys; f,t=float(sys.argv[1]),float(sys.argv[2]); print('streaming OK' if t-f>0.3 or t<1 else 'WARNING: first byte at the end, the path may be buffering'); sys.exit(0 if (t-f>0.3 or t<1) else 1)" "$FIRST" "$TOTAL"
