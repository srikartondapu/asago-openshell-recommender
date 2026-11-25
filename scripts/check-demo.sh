#!/usr/bin/env bash
# Click through every stage, tab, condition and one attack + benign animation of the demo in headless Chrome; report errors or empty views.
# Needs python3 and Google Chrome (or Chromium). Set CHROME=/path/to/chrome if it is not found.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${PORT:-8799}"

if [ -z "${CHROME:-}" ]; then
  for c in "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" google-chrome google-chrome-stable chromium chromium-browser; do
    if command -v "$c" >/dev/null 2>&1 || [ -x "$c" ]; then CHROME="$c"; break; fi
  done
fi
[ -n "${CHROME:-}" ] || { echo "Chrome not found; set CHROME=/path/to/chrome"; exit 2; }

python3 -m http.server "$PORT" --bind 127.0.0.1 --directory "$ROOT" >/dev/null 2>&1 &
SERVER=$!
trap 'kill $SERVER 2>/dev/null' EXIT
sleep 1

RESULT="$("$CHROME" --headless --disable-gpu --virtual-time-budget=600000 --dump-dom \
  "http://127.0.0.1:$PORT/scripts/check.html" 2>/dev/null | python3 -c '
import html, re, sys
m = re.search(r"CHECK-RESULT (.*?)</pre>", sys.stdin.read(), re.S)
print(html.unescape(m.group(1)) if m else "{\"harness_error\": \"no result (increase --virtual-time-budget?)\"}")
')"
echo "$RESULT"
echo "$RESULT" | python3 -c '
import json, sys
r = json.load(sys.stdin)
ok = not r.get("harness_error") and not r.get("js_errors") and r.get("problems") == 0 and r.get("views", 0) > 0
print("PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
'
