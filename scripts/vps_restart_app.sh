#!/usr/bin/env bash
# 在「已 ssh 到 VPS 的 shell」內執行：列出可能服務，方便你重啟。
#   bash scripts/vps_restart_app.sh
# 若已知道名稱：  sudo systemctl restart YOUR.service
set -euo pipefail
echo "== running (gunicorn/yui/flask/uvicorn) =="
systemctl list-units --type=service --state=running --no-legend 2>/dev/null | grep -iE 'gunicorn|yui|flask|quant|uvicorn|wsgi' || true
echo "== unit files (name match) =="
systemctl list-unit-files --type=service --no-legend 2>/dev/null | grep -iE 'gunicorn|yui|flask|quant|uvicorn' || true
echo "== nginx (通常不需 restart 讓 .env 生效) =="
systemctl is-active nginx 2>/dev/null || true
echo "Pick your app service, then: systemctl restart <name>.service"
