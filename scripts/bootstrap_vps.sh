#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/home/yui/yui-quant-lab"
SERVICE_NAME="yui-quant-lab"
APP_USER="yui"
GUNICORN_BIND="127.0.0.1:8000"
DRY_RUN=0

CURRENT_DIR="$(pwd)"

log() {
  echo "$1"
}

warn() {
  echo "[WARN] $1" >&2
}

fail() {
  echo "[ERROR] $1" >&2
  exit 1
}

print_help() {
  cat <<'EOF'
bootstrap_vps.sh
One-shot VPS deployment for yui-quant-lab.

Usage:
  ./scripts/bootstrap_vps.sh
  ./scripts/bootstrap_vps.sh --dry-run
  ./scripts/bootstrap_vps.sh --help

Options:
  --dry-run   Print commands without executing them.
  --help      Show this help message and exit.
EOF
}

run_cmd() {
  local cmd="$1"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "[DRY-RUN] $cmd"
    return 0
  fi
  eval "$cmd"
}

require_file() {
  local f="$1"
  [[ -f "$f" ]] || fail "Required file not found: $f"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --help)
      print_help
      exit 0
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    *)
      fail "Unknown argument: $1 (Supported: --dry-run, --help)"
      ;;
  esac
done

# [1/7] Checking files
log "[1/7] Checking files"

[[ -d "$CURRENT_DIR" ]] || fail "Current directory does not exist: $CURRENT_DIR"
require_file ".env"
require_file "deploy/yui-quant-lab.service"
require_file "deploy/nginx-yui-quant-lab.conf"
require_file "deploy/nginx-yui-quant-lab-zones.conf"
require_file "requirements.txt"

if grep -n "CHANGEME" "deploy/yui-quant-lab.service" >/dev/null 2>&1; then
  fail "deploy/yui-quant-lab.service still contains CHANGEME. Please replace before bootstrap."
fi

# 保持 workers=1 部署假設；僅警告，不阻擋
if ! grep -nE -- "--workers([[:space:]]+|=)1([[:space:]]|$)" "deploy/yui-quant-lab.service" >/dev/null 2>&1; then
  warn "deploy/yui-quant-lab.service does not explicitly declare --workers 1 or --workers=1."
  warn "Current architecture uses file-based persistence; workers=1 is strongly recommended."
fi

# [2/7] Setting up Python
log "[2/7] Setting up Python"

if [[ ! -d ".venv" ]]; then
  run_cmd "python3 -m venv .venv"
fi

# shellcheck disable=SC1091
if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "[DRY-RUN] source .venv/bin/activate"
else
  source .venv/bin/activate
fi

run_cmd "python -m pip install -U pip"
run_cmd "python -m pip install -r requirements.txt"

# [3/7] Running pre-checks
log "[3/7] Running pre-checks"

run_cmd "python -m py_compile app.py telegram_bot.py decision_engine.py state_manager.py command_writer.py execution_tracker.py"

if command -v pytest >/dev/null 2>&1; then
  run_cmd "pytest -q"
else
  warn "pytest not found in PATH; skipping pytest -q"
fi

# [4/7] Deploying Nginx config
log "[4/7] Deploying Nginx config"

run_cmd "sudo cp deploy/nginx-yui-quant-lab-zones.conf /etc/nginx/conf.d/yui-quant-lab-zones.conf"
run_cmd "sudo cp deploy/nginx-yui-quant-lab.conf /etc/nginx/sites-available/yui-quant-lab"
run_cmd "sudo ln -sf /etc/nginx/sites-available/yui-quant-lab /etc/nginx/sites-enabled/yui-quant-lab"
run_cmd "sudo nginx -t"
run_cmd "sudo systemctl reload nginx"

# [5/7] Deploying systemd service
log "[5/7] Deploying systemd service"

run_cmd "sudo cp deploy/yui-quant-lab.service /etc/systemd/system/yui-quant-lab.service"
run_cmd "sudo systemctl daemon-reload"
run_cmd "sudo systemctl enable --now ${SERVICE_NAME}.service"
run_cmd "sudo systemctl status ${SERVICE_NAME}.service --no-pager"

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "[DRY-RUN] sudo systemctl is-active --quiet ${SERVICE_NAME}.service"
  echo "[DRY-RUN] (if inactive) sudo journalctl -u ${SERVICE_NAME}.service -n 80 --no-pager && exit 1"
elif ! sudo systemctl is-active --quiet "${SERVICE_NAME}.service"; then
  warn "Service is not active after startup: ${SERVICE_NAME}.service"
  sudo journalctl -u "${SERVICE_NAME}.service" -n 80 --no-pager || true
  exit 1
fi

# [6/7] Verifying service health
log "[6/7] Verifying service health"

run_cmd "curl -fsS http://127.0.0.1/health"

if [[ -f "scripts/run_live_chain_check.py" ]]; then
  run_cmd "python scripts/run_live_chain_check.py"
else
  warn "scripts/run_live_chain_check.py not found; skipping live chain check"
fi

# [7/7] Collecting diagnostics
log "[7/7] Collecting diagnostics"

run_cmd "df -h"
run_cmd "sudo journalctl -u ${SERVICE_NAME}.service -n 80 --no-pager"

log "Bootstrap completed successfully."
log "APP_DIR=$APP_DIR APP_USER=$APP_USER GUNICORN_BIND=$GUNICORN_BIND (workers=1 recommended)"
