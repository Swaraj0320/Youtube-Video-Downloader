#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ] || [ ! -d frontend/node_modules ] || [ ! -x tools/bin/ffmpeg ] || [ ! -x tools/bin/ffprobe ]; then
  echo "Dependencies are missing. Run ./setup.sh first."
  exit 1
fi
# Reuse a healthy TubeClip instance instead of starting duplicate servers.
if .venv/bin/python - <<'PY'
import json
import sys
import urllib.request
try:
    def read(url):
        with urllib.request.urlopen(url, timeout=2) as response:
            return response.read().decode('utf-8')
    health = json.loads(read('http://127.0.0.1:8000/api/health'))
    schema = json.loads(read('http://127.0.0.1:8000/openapi.json'))
    frontend = read('http://127.0.0.1:5173')
    if health.get('status') != 'ok' or schema.get('info', {}).get('title') != 'TubeClip' or '<title>TubeClip' not in frontend:
        sys.exit(1)
except Exception:
    sys.exit(1)
print('TubeClip is already running: http://127.0.0.1:5173')
print('Open that address in your browser. No second server is needed.')
PY
then
  exit 0
fi
.venv/bin/python - <<'PY'
import socket
for port in (8000,5173):
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try: s.bind(('127.0.0.1',port))
        except OSError: raise SystemExit(f'Port {port} is already in use. Stop the previous TubeClip session or the conflicting server.')
PY
backend_pid=''
frontend_pid=''
cleanup() {
  trap - EXIT INT TERM
  if [ -n "$frontend_pid" ]; then kill "$frontend_pid" 2>/dev/null || true; fi
  if [ -n "$backend_pid" ]; then kill "$backend_pid" 2>/dev/null || true; fi
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM
.venv/bin/python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000 &
backend_pid=$!
(cd frontend && exec ./node_modules/.bin/vite --host 127.0.0.1 --port 5173 --strictPort) &
frontend_pid=$!
.venv/bin/python - <<'PY'
import urllib.request,time
for attempt in range(60):
    try:
        for url in ('http://127.0.0.1:8000/api/health','http://127.0.0.1:5173'):
            with urllib.request.urlopen(url,timeout=1) as r: assert r.status==200
        break
    except Exception: time.sleep(.25)
else: raise SystemExit('Servers did not become ready. Review the errors above.')
PY
printf '\nTubeClip is ready: http://127.0.0.1:5173\nBackend: http://127.0.0.1:8000/api/health\nPress Ctrl+C to stop both servers.\n\n'
while kill -0 "$backend_pid" 2>/dev/null && kill -0 "$frontend_pid" 2>/dev/null; do sleep 1; done
