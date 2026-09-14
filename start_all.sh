#!/usr/bin/env bash
#
# start-all.sh — starts all three models' backends and frontends together.
#
# Assumes, per model, the layout:
#   modelN/backend/   — has a .testing venv already created and deps installed
#   modelN/frontend/  — has node_modules already installed (npm install)
#
# Run from the parent directory containing model1/, model2/, model3/:
#   bash start-all.sh
#
# Stop everything with Ctrl+C — the trap below kills all child processes.

set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$ROOT_DIR/logs"
mkdir -p "$LOG_DIR"

PIDS=()

cleanup() {
  echo ""
  echo "Shutting down all processes..."
  for pid in "${PIDS[@]}"; do
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null
    fi
  done
  wait 2>/dev/null
  echo "All processes stopped."
  exit 0
}
trap cleanup INT TERM

# ---------------------------------------------------------------------------
# Helper: start a backend (FastAPI/uvicorn) from modelN/backend
# ---------------------------------------------------------------------------
start_backend() {
  local model_name="$1"
  local port="$2"
  local dir="$ROOT_DIR/$model_name/backend"

  if [ ! -d "$dir" ]; then
    echo "SKIP: $dir does not exist."
    return
  fi
  if [ ! -x "$dir/.testing/bin/uvicorn" ]; then
    echo "SKIP: $model_name backend — no .testing venv with uvicorn found at $dir/.testing"
    echo "      Set it up first: cd $dir && python3.12 -m venv .testing && .testing/bin/pip install -r requirements.txt"
    return
  fi
  if [ ! -f "$dir/.env" ]; then
    echo "WARNING: $model_name backend has no .env — it will likely fail to connect/auth correctly."
  fi

  echo "Starting $model_name backend on port $port (HTTP, TLS forced off)..."
  (
    cd "$dir" && \
    export ENABLE_HTTPS=false && \
    exec ./.testing/bin/uvicorn main:app --reload --port "$port"
  ) > "$LOG_DIR/${model_name}-backend.log" 2>&1 &

  PIDS+=($!)
  echo "  -> PID $!  (log: logs/${model_name}-backend.log)"
}

# ---------------------------------------------------------------------------
# Helper: start a frontend (Vite dev server) from modelN/frontend
# ---------------------------------------------------------------------------
start_frontend() {
  local model_name="$1"
  local dir="$ROOT_DIR/$model_name/frontend"

  if [ ! -d "$dir" ]; then
    echo "SKIP: $dir does not exist."
    return
  fi
  if [ ! -d "$dir/node_modules" ]; then
    echo "SKIP: $model_name frontend — no node_modules found at $dir"
    echo "      Set it up first: cd $dir && npm install"
    return
  fi

  echo "Starting $model_name frontend..."
  (
    cd "$dir" && \
    exec npm run dev
  ) > "$LOG_DIR/${model_name}-frontend.log" 2>&1 &

  PIDS+=($!)
  echo "  -> PID $!  (log: logs/${model_name}-frontend.log)"
}

# ---------------------------------------------------------------------------
# Start everything
# ---------------------------------------------------------------------------
echo "=== Starting Model 1 (Registry) ==="
start_backend  "model1" 8000
start_frontend "model1"

echo ""
echo "=== Starting Model 2 (Viewer & ANPR) ==="
start_backend  "model2" 8001
start_frontend "model2"

echo ""
echo "=== Starting Model 3 (Federation) ==="
start_backend  "model3" 8002
start_frontend "model3"

echo ""
echo "============================================================"
echo " All available services starting. Give them ~5-10s to come up."
echo ""
echo "   Model 1 — Registry     : http://localhost:5173  (API: http://localhost:8000/docs)"
echo "   Model 2 — Viewer/ANPR  : http://localhost:5174  (API: http://localhost:8001/docs)"
echo "   Model 3 — Federation   : http://localhost:5175  (API: http://localhost:8002/docs)"
echo ""
echo " All backends are forced to plain HTTP (ENABLE_HTTPS=false) regardless"
echo " of what each model's own .env says, to keep local dev consistent."
echo ""
echo " Logs are streaming to: $LOG_DIR/"
echo " Press Ctrl+C to stop everything."
echo "============================================================"
echo ""

# Give background processes a moment to actually open their log files
# before checking for them (avoids a race where this check runs first).
sleep 1

# Tail all logs together so you see everything in one place.
if compgen -G "$LOG_DIR"/*.log > /dev/null; then
  tail -f "$LOG_DIR"/*.log &
  TAIL_PID=$!
  PIDS+=($TAIL_PID)
else
  echo "No services actually started — nothing to tail. Check the SKIP messages above."
fi

wait
