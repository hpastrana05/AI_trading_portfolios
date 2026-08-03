#!/usr/bin/env bash
# Start Demo (:8100) and Live (:8101) uvicorn instances side by side.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

DEMO_PORT="${DEMO_PORT:-8100}"
LIVE_PORT="${LIVE_PORT:-8101}"
HOST="${APP_HOST:-0.0.0.0}"

mkdir -p data/demo data/live

PIDS=()

cleanup() {
  echo ""
  echo "Stopping Demo and Live..."
  for pid in "${PIDS[@]:-}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "Starting DEMO on ${HOST}:${DEMO_PORT} ..."
T212_ENV=DEMO \
  APP_PORT="$DEMO_PORT" \
  OTHER_ENV_PORT="$LIVE_PORT" \
  uvicorn main:app --host "$HOST" --port "$DEMO_PORT" &
PIDS+=($!)

echo "Starting LIVE on ${HOST}:${LIVE_PORT} ..."
T212_ENV=LIVE \
  APP_PORT="$LIVE_PORT" \
  OTHER_ENV_PORT="$DEMO_PORT" \
  uvicorn main:app --host "$HOST" --port "$LIVE_PORT" &
PIDS+=($!)

echo ""
echo "Demo:  http://<this-host>:${DEMO_PORT}"
echo "Live:  http://<this-host>:${LIVE_PORT}"
echo "Sidebar Open DEMO/LIVE reuses the hostname from your browser."
echo "Press Ctrl+C to stop both."
echo ""

wait
