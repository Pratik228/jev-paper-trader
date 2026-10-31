#!/usr/bin/env bash
# Keep public Binance recording alive. No orders. Safe to run from cron.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SESSION="jev-record"
PYTHON="${ROOT}/.venv/bin/python"
LOG="${ROOT}/data/watchdog.log"
STALE_MS=180000

cd "$ROOT"
mkdir -p "${ROOT}/data/raw"
touch "$LOG"

ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }

running() {
  pgrep -f "${PYTHON} -m jev_paper.data.record" >/dev/null 2>&1
}

heartbeat_age_ms() {
  local hb="${ROOT}/data/heartbeat.jsonl"
  if [[ ! -s "$hb" ]]; then
    echo 999999999
    return
  fi
  python3 - "$hb" <<'PY'
import json, sys, time
path = sys.argv[1]
last = None
with open(path, encoding="utf-8") as handle:
    for line in handle:
        line = line.strip()
        if line:
            last = line
if not last:
    print(999999999)
    raise SystemExit
row = json.loads(last)
print(int(time.time() * 1000) - int(row.get("ts_ms") or 0))
PY
}

start() {
  if tmux has-session -t "$SESSION" 2>/dev/null; then
    tmux kill-session -t "$SESSION" >/dev/null 2>&1 || true
  fi
  tmux new-session -d -s "$SESSION" "cd '${ROOT}' && exec '${PYTHON}' -m jev_paper.data.record --config config.yaml --data-dir data"
  echo "$(ts) started tmux session ${SESSION}" >>"$LOG"
}

if ! running; then
  echo "$(ts) process missing; restarting" >>"$LOG"
  start
  exit 0
fi

hb="${ROOT}/data/heartbeat.jsonl"
if [[ ! -s "$hb" ]]; then
  exit 0
fi

age="$(heartbeat_age_ms || echo 999999999)"
if [[ "$age" -gt "$STALE_MS" ]]; then
  echo "$(ts) heartbeat stale ${age}ms; restarting" >>"$LOG"
  pkill -f "${PYTHON} -m jev_paper.data.record" >/dev/null 2>&1 || true
  start
fi
