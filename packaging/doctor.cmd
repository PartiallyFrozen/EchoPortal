@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0doctor.ps1" %*
if "%~1"=="" pause
