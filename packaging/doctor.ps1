# EchoPortal doctor: checks the things that usually break a first run.
# Exit code 0 = no blocking problems, 1 = at least one FAIL.
$ErrorActionPreference = "Continue"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$fail = 0
function Ok($m)   { Write-Host "  OK    $m" -ForegroundColor Green }
function Warn($m) { Write-Host "  WARN  $m" -ForegroundColor Yellow }
function Bad($m)  { Write-Host "  FAIL  $m" -ForegroundColor Red; $script:fail++ }

Write-Host "EchoPortal doctor" -ForegroundColor Cyan
Write-Host "Install folder: $here"
Write-Host ""

$configPath = Join-Path $here "config.json"
$cfg = $null
if (-not (Test-Path $configPath)) {
    Bad "config.json is missing (run the installer, then start the agent once)"
} else {
    try {
        $cfg = Get-Content $configPath -Raw | ConvertFrom-Json
        Ok "config.json parses"
    } catch {
        Bad "config.json is not valid JSON: $($_.Exception.Message)"
    }
}

if ($cfg) {
    if ($null -eq $cfg.latitude -or $null -eq $cfg.longitude) {
        Warn "latitude/longitude are not set - the weather faces stay quiet"
    } else {
        Ok "location $($cfg.latitude), $($cfg.longitude) ($($cfg.timezone))"
    }

    $faces = @($cfg.faces)
    if ($faces.Count -gt 0) { Ok "server workers: $($faces -join ', ')" } else { Ok "server workers: all (no faces list)" }

    $urls = @($cfg.calendar_urls | Where-Object { $_ })
    if ($urls.Count -gt 0) { Ok "calendar feeds: $($urls.Count)" } else { Warn "no calendar_urls - the Agenda face stays empty" }

    if ($cfg.api_token) {
        Ok "api_token is set (set the same token on the Spot)"
    } else {
        Bad "api_token is empty - this build cannot start unauthenticated; set a token before exposing ports"
    }

    $key = $cfg.gemini_api_key
    if (-not $key) { $key = $env:GEMINI_API_KEY }
    if ($key) { Ok "Gemini key is present" } else { Warn "no Gemini key - this packaged build has no working transcription" }
}

$clip = "fireplace.mp4"
if ($cfg -and $cfg.media_clip) { $clip = [string]$cfg.media_clip }
$media = Join-Path (Join-Path $here "media") $clip
if (Test-Path $media) {
    $mb = [math]::Round((Get-Item $media).Length / 1MB, 1)
    Ok "media/$clip is present ($mb MB)"
} else {
    Warn "media/$clip is missing - the Video Clock falls back to a dark clock"
}

foreach ($port in 8765, 8766) {
    $listening = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue
    if ($listening) { Ok "TCP $port is listening" } else { Warn "TCP $port is not listening - start the agent from the tray" }
}

foreach ($name in "EchoPortal agent (hub 8765)", "EchoPortal agent (media 8766)") {
    $rule = Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue
    if ($rule) { Ok "firewall rule present: $name" } else { Warn "firewall rule missing: $name (run allow-firewall.cmd as administrator)" }
}

$headers = @{}
if ($cfg -and $cfg.api_token) { $headers["X-EchoPortal-Token"] = [string]$cfg.api_token }
try {
    $s = Invoke-RestMethod -Uri "http://127.0.0.1:8766/api/status" -Headers $headers -TimeoutSec 5
    Ok "API answers: voice=$($s.voice), clients=$($s.clients), timers=$(@($s.timers.items).Count), events=$($s.calendar.events)"
} catch {
    Warn "the API did not answer on 8766 ($($_.Exception.Message))"
}

Write-Host ""
if ($fail -gt 0) {
    Write-Host "$fail check(s) failed." -ForegroundColor Red
    exit 1
}
Write-Host "No blocking problems found." -ForegroundColor Green
