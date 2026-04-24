#!/usr/bin/env bash
# 將本機 repo 根目錄 .env 的 TV_WEBHOOK_SECRET 寫入 VPS 上的 .env（與 TradingView 應一致）。
# 用法（在已可 ssh 登入 VPS 的終端執行，例如 Git Bash / WSL）：
#  Windows 若沒有 bash，請用純 PowerShell：powershell -File scripts/sync_tv_webhook_secret_to_vps.ps1
#   ./scripts/sync_tv_webhook_secret_to_vps.sh
#   ./scripts/sync_tv_webhook_secret_to_vps.sh root@72.62.247.17 /root/yui-quant-lab/.env
# 可選第三參數：systemd 服務名（不含 .service），寫入後會嘗試 restart。
#   ./scripts/sync_tv_webhook_secret_to_vps.sh root@72.62.247.17 /root/yui-quant-lab/.env yui-quant-lab
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/.env"
REMOTE="${1:-root@72.62.247.17}"
REMOTE_ENV="${2:-/root/yui-quant-lab/.env}"
APP_SERVICE="${3:-}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "missing: $ENV_FILE" >&2
  exit 1
fi

LINE="$(grep -m1 '^[[:space:]]*TV_WEBHOOK_SECRET=' "$ENV_FILE" || true)"
if [[ -z "$LINE" ]]; then
  echo "no TV_WEBHOOK_SECRET= line in $ENV_FILE" >&2
  exit 1
fi
SECRET="${LINE#*=}"
SECRET="${SECRET#"${SECRET%%[![:space:]]*}"}"
SECRET="${SECRET%"${SECRET##*[![:space:]]}"}"
SECRET="${SECRET//$'\r'/}"
if [[ -z "$SECRET" ]]; then
  echo "TV_WEBHOOK_SECRET is empty" >&2
  exit 1
fi
if [[ "$SECRET" == *$'\n'* ]]; then
  echo "TV_WEBHOOK_SECRET must be a single line" >&2
  exit 1
fi

B64="$(printf '%s' "$SECRET" | base64 | tr -d '\n')"

ssh "$REMOTE" "export B64='$B64' REMOTE_ENV='$REMOTE_ENV'; python3 <<'PY'
import base64, os, pathlib, shutil, subprocess, sys

secret = base64.b64decode(os.environ['B64']).decode('utf-8')
path = pathlib.Path(os.environ['REMOTE_ENV'])
if not path.is_file():
    sys.exit(f'missing file: {path}')

text = path.read_text(encoding='utf-8')
lines = text.splitlines()
out = []
found = False
for ln in lines:
    stripped = ln.lstrip()
    if stripped.startswith('TV_WEBHOOK_SECRET='):
        indent = ln[: len(ln) - len(stripped)]
        out.append(indent + 'TV_WEBHOOK_SECRET=' + secret)
        found = True
    else:
        out.append(ln)
if not found:
    out.append('TV_WEBHOOK_SECRET=' + secret)

bak = path.with_name(path.name + '.bak.sync_tv')
shutil.copy2(path, bak)
path.write_text('\n'.join(out) + '\n', encoding='utf-8')
print('backup:', bak)
print('updated:', path)
print('TV_WEBHOOK_SECRET length:', len(secret))
PY
"

if [[ -n "$APP_SERVICE" ]]; then
  ssh "$REMOTE" "systemctl restart ${APP_SERVICE}.service" && echo "restarted ${APP_SERVICE}.service"
else
  echo "若 Flask/gunicorn 仍讀舊環境變數，請在 VPS 執行： systemctl restart <你的服務名>"
  echo "查服務： systemctl list-units --type=service --state=running | grep -iE 'yui|gunicorn|flask|uvicorn'"
fi
