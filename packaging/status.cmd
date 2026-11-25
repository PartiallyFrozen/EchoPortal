@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0status.ps1" %*
if "%~1"=="" pause
