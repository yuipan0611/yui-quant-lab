# 從專案根 .env 讀取 TV_WEBHOOK_SECRET，POST 到 /tv-webhook 做煙測（期望非 invalid_secret）。
# 專案根執行：  powershell -ExecutionPolicy Bypass -File scripts/verify_tv_webhook.ps1
# 可選： -Url "https://yuistrategy.com/tv-webhook"

param(
    [string] $Url = "https://yuistrategy.com/tv-webhook"
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$EnvFile = Join-Path $Root ".env"
if (-not (Test-Path -LiteralPath $EnvFile)) { throw "missing: $EnvFile" }

$line = Get-Content -LiteralPath $EnvFile -Encoding UTF8 | Where-Object { $_ -match '^\s*TV_WEBHOOK_SECRET=' } | Select-Object -First 1
if (-not $line) { throw "no TV_WEBHOOK_SECRET= in .env" }
$secret = ($line -replace '^\s*TV_WEBHOOK_SECRET=\s*', '').Trim().TrimEnd("`r")
if ([string]::IsNullOrEmpty($secret)) { throw "TV_WEBHOOK_SECRET is empty" }

# 價加隨機小數，降低連續兩次 dedupe 命中同 fingerprint 的機率
$jitter = (Get-Random -Maximum 99999) / 1e6
$price = [math]::Round(27250.0 + $jitter, 6)
$bodyObj = [ordered]@{
    secret           = $secret
    symbol           = "NQ"
    signal           = "long_breakout"
    price            = $price
    breakout_level   = 27200.0
    delta_strength   = 1.0
}
$json = $bodyObj | ConvertTo-Json -Compress
Write-Host "POST $Url" 
try {
    $r = Invoke-WebRequest -Uri $Url -Method POST -ContentType "application/json; charset=utf-8" -Body $json -UseBasicParsing -TimeoutSec 30
    Write-Host "Status:" $r.StatusCode
    Write-Host $r.Content
    if ($r.StatusCode -eq 403) {
        $c = $r.Content
        if ($c -match 'invalid_secret') { Write-Host "FAIL: invalid_secret (VPS/TradingView secret not aligned?)"; exit 2 }
    } elseif ($r.StatusCode -eq 200) { if ($r.Content -match '"ok":\s*true') { Write-Host "OK: secret accepted (check decision/duplicate in body)." } }
} catch {
    $resp = $_.Exception.Response
    if ($resp) {
        $reader = New-Object System.IO.StreamReader($resp.GetResponseStream())
        $txt = $reader.ReadToEnd()
        Write-Host "Status:" ([int]$resp.StatusCode)
        Write-Host $txt
        if ([int]$resp.StatusCode -eq 403 -and $txt -match 'invalid_secret') { Write-Host "FAIL: invalid_secret"; exit 2 }
    } else {
        Write-Warning "Request failed: $($_.Exception.Message)"
        $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
        if ($curl) { Write-Host "Try: curl.exe -sS -i -X POST `"$Url`" -H content-type:application/json -d `"$json`"" }
        exit 1
    }
}
