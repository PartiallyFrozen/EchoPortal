# EchoPortal agent status: one-line answer to "is it working?".
# Reads config.json next to this script for api_token, then asks the local API.
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$configPath = Join-Path $here "config.json"
$token = ""
if (Test-Path $configPath) {
    try {
        $cfg = Get-Content $configPath -Raw | ConvertFrom-Json
        if ($cfg.api_token) { $token = [string]$cfg.api_token }
    } catch {
        Write-Host "config.json is not valid JSON; continuing without a token." -ForegroundColor Yellow
    }
}

$headers = @{}
if ($token) { $headers["X-EchoPortal-Token"] = $token }

try {
    $s = Invoke-RestMethod -Uri "http://127.0.0.1:8766/api/status" -Headers $headers -TimeoutSec 5
} catch {
    Write-Host "EchoPortal agent is not answering on port 8766." -ForegroundColor Red
    Write-Host "Start it from the tray, then try again. ($($_.Exception.Message))"
    exit 1
}

Write-Host "EchoPortal agent" -ForegroundColor Cyan
Write-Host ("  host:      {0}  (version {1})" -f $s.host, $s.version)
Write-Host ("  clients:   {0}   voice: {1}" -f $s.clients, $s.voice)
Write-Host ("  token:     {0}" -f $(if ($s.token_required) { "required" } else { "not required" }))
Write-Host ("  clip:      {0}" -f $s.clip)
Write-Host ("  faces:     {0}" -f $(if ($s.faces) { $s.faces -join ", " } else { "all" }))
Write-Host ("  timers:    {0}" -f @($s.timers.items).Count)
Write-Host ("  calendar:  {0} events, configured={1}, error={2}" -f $s.calendar.events, $s.calendar.configured, $s.calendar.error)
Write-Host ("  alerts:    {0} rules" -f $s.alerts)
if ($null -ne $s.sample_age_s) {
    Write-Host ("  last stats sample: {0}s ago" -f $s.sample_age_s)
}
Write-Host ""
Write-Host "Full response:" -ForegroundColor DarkGray
$s | ConvertTo-Json -Depth 8
