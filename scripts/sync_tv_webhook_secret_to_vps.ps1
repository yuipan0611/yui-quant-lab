# 純 PowerShell，不需安裝 bash。將專案根目錄 .env 的 TV_WEBHOOK_SECRET 寫入 VPS。
# 在專案根目錄執行：
#   powershell -ExecutionPolicy Bypass -File scripts/sync_tv_webhook_secret_to_vps.ps1
#   或：pwsh -File scripts/sync_tv_webhook_secret_to_vps.ps1
# 可選參數：-SshTarget、-RemoteEnvPath、-AppService（不含 .service，會在寫入後 restart）

param(
    [string] $SshTarget = "root@72.62.247.17",
    [string] $RemoteEnvPath = "/root/yui-quant-lab/.env",
    [string] $AppService = ""
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$EnvFile = Join-Path $Root ".env"
if (-not (Test-Path -LiteralPath $EnvFile)) { throw "missing: $EnvFile" }

$line = Get-Content -LiteralPath $EnvFile -Encoding UTF8 | Where-Object { $_ -match '^\s*TV_WEBHOOK_SECRET=' } | Select-Object -First 1
if (-not $line) { throw "no TV_WEBHOOK_SECRET= in .env" }
$rest = $line -replace '^\s*TV_WEBHOOK_SECRET=\s*', ''
# 若值曾以引號包覆，可再自行 strip
$secret = $rest.Trim().TrimEnd("`r")
if ([string]::IsNullOrEmpty($secret)) { throw "TV_WEBHOOK_SECRET is empty" }
if ($secret -match "[\r\n]") { throw "TV_WEBHOOK_SECRET must be a single line" }

$escPath = $RemoteEnvPath -replace "\\", "\\\\" -replace "'", "''"
$b64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($secret))

# 單行 base64 不含 '，可直接包在單引號
$py = @"
import base64, pathlib, shutil, sys
secret = base64.b64decode("$b64").decode("utf-8")
if chr(10) in secret or chr(13) in secret:
    sys.exit("TV_WEBHOOK_SECRET must be a single line")
path = pathlib.Path("$escPath")
if not path.is_file():
    sys.exit("missing file: " + str(path))
text = path.read_text(encoding="utf-8")
lines = text.splitlines()
out, found = [], False
for ln in lines:
    s = ln.lstrip()
    if s.startswith("TV_WEBHOOK_SECRET="):
        ind = ln[: len(ln) - len(s)]
        out.append(ind + "TV_WEBHOOK_SECRET=" + secret)
        found = True
    else:
        out.append(ln)
if not found:
    out.append("TV_WEBHOOK_SECRET=" + secret)
bak = path.with_name(path.name + ".bak.sync_tv")
shutil.copy2(path, bak)
path.write_text(chr(10).join(out) + chr(10), encoding="utf-8")
print("backup:", bak)
print("updated:", path)
print("TV_WEBHOOK_SECRET length:", len(secret))
"@

try {
    $py | & ssh -T -o ConnectTimeout=20 $SshTarget "python3 -"
} catch {
    Write-Error "SSH failed (ensure `ssh $SshTarget` works in this terminal): $_"
    exit 1
}

if ($AppService) {
    & ssh $SshTarget "systemctl restart $AppService.service"
    Write-Host "restarted $AppService.service"
} else {
    Write-Host "If the app still uses the old secret, on the VPS run: systemctl restart <your-app-service>"
}
