#!/usr/bin/env bash
# Checks that answers stream to the browser the way the page actually asks for them.
# Usage: scripts/check_stream.sh https://SITE.web.app [ID_TOKEN]
# Without a token the server needs WAYPOINT_DEV_SKIP_AUTH=true (local runs); a deployed service needs a Google ID token.
#
# The browser path: the page reads /api/config from the site it was served from. If the site's origin is one of
# stream_origins (Firebase Hosting, which buffers Server-Sent Events), it POSTs /api/chat/stream to stream_url (Cloud
# Run) cross-origin, after a CORS preflight; otherwise to its own origin. This script does the same, with the site's
# Origin header, and fails unless:
#   - the preflight allows the origin and the Authorization header (cross-origin only),
#   - the stream answers 200 with Access-Control-Allow-Origin for the site (cross-origin only),
#   - the answer arrives as several events spread over time, not all at once (buffering would deliver them together).
# The question carries a timestamp so it is never served from the answer cache (a cached reply would pass even
# through a buffering proxy).
set -euo pipefail
SITE="${1:?usage: check_stream.sh URL [ID_TOKEN]}"
SITE="${SITE%/}"
TOKEN="${2:-}"
AUTH=()
if [ -n "$TOKEN" ]; then AUTH=(-H "Authorization: Bearer $TOKEN"); fi
ORIGIN="$(python3 -c "import sys, urllib.parse as u; p=u.urlsplit(sys.argv[1]); print(f'{p.scheme}://{p.netloc}')" "$SITE")"

CONFIG="$(curl -sS --max-time 20 "$SITE/api/config")"
TARGET="$(python3 -c "
import json, sys
c = json.loads(sys.argv[1])
print(c.get('stream_url') if c.get('stream_url') and sys.argv[2] in (c.get('stream_origins') or []) else sys.argv[3])
" "$CONFIG" "$ORIGIN" "$SITE")"
echo "page origin: $ORIGIN"
echo "stream from: $TARGET"

if [ "$TARGET" != "$SITE" ]; then
  PRE="$(curl -sS --max-time 20 -o /dev/null -D - -X OPTIONS "$TARGET/api/chat/stream" -H "Origin: $ORIGIN" \
    -H 'Access-Control-Request-Method: POST' -H 'Access-Control-Request-Headers: authorization,content-type')"
  echo "$PRE" | grep -i '^access-control-allow-origin' || true
  echo "$PRE" | grep -qi "^access-control-allow-origin: $ORIGIN" || { echo "FAIL: preflight does not allow $ORIGIN"; exit 1; }
  echo "$PRE" | grep -i '^access-control-allow-headers' | grep -qi authorization \
    || { echo "FAIL: preflight does not allow the Authorization header"; exit 1; }
  echo "preflight OK"
fi

OUT="$(mktemp)"
HEADERS="$(mktemp)"
QUESTION="Can a course taken on exchange count toward the Mathematics minor? (check $(date +%s))"
# Each line is stamped with the seconds since the request started, as it arrives.
curl -sS -N --max-time 90 -D "$HEADERS" -X POST "$TARGET/api/chat/stream" -H 'Content-Type: application/json' \
  -H "Origin: $ORIGIN" ${AUTH[@]+"${AUTH[@]}"} -d "{\"message\":\"$QUESTION\"}" \
  | python3 -u -c "
import sys, time
t0 = time.time()
for line in sys.stdin:
    print(f'{time.time() - t0:.3f} {line}', end='')
" > "$OUT"
STATUS="$(head -1 "$HEADERS" | awk '{print $2}')"
echo "status: $STATUS"
[ "$STATUS" = "200" ] || { echo "FAIL: stream returned $STATUS"; cat "$OUT" | head -5; exit 1; }
if [ "$TARGET" != "$SITE" ]; then
  grep -qi "^access-control-allow-origin: $ORIGIN" "$HEADERS" \
    || { echo "FAIL: the stream response has no CORS header for $ORIGIN (the browser would block it)"; exit 1; }
fi
python3 - "$OUT" <<'PY'
import sys
events = [(float(line.split(" ", 1)[0]), line.split(" ", 1)[1].strip()) for line in open(sys.argv[1]) if " event: " in line]
names = [name.removeprefix("event: ") for _, name in events]
print(f"events: {len(events)} ({names.count('token')} tokens)")
if "final" not in names:
    print("FAIL: the stream ended without a final answer")
    sys.exit(1)
first, last = events[0][0], events[-1][0]
token_times = [t for t, name in events if name == "event: token"]
spread = (token_times[-1] - token_times[0]) if len(token_times) > 1 else 0.0
print(f"first event {first:.2f} s, last event {last:.2f} s, tokens spread over {spread:.2f} s")
ok = last - first > 0.3 and len(token_times) > 1 and spread > 0.2
print("streaming OK" if ok else "FAIL: events arrived all at once; the path is buffering")
sys.exit(0 if ok else 1)
PY
