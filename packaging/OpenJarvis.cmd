@echo off
REM Double-click to open the Jarvis chat interface in your default browser.
powershell -NoProfile -Command "$cfg = Get-Content -Raw (Join-Path $env:LOCALAPPDATA 'Programs\EchoPortal\config.json') | ConvertFrom-Json; Start-Process ('http://127.0.0.1:8766/dashboard?token=' + $cfg.api_token)"
