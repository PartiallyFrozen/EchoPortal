@echo off
rem Right-click this file and choose "Run as administrator".
rem It asks for elevation itself, runs allow-firewall.ps1, then shows what happened.
setlocal
set "PS1=%~dp0allow-firewall.ps1"
set "LOG=%TEMP%\echoportal-fixup.log"
if not exist "%PS1%" (
  echo Could not find allow-firewall.ps1 next to this file.
  pause
  exit /b 1
)
del "%LOG%" >nul 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath powershell.exe -Verb RunAs -Wait -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','\"%PS1%\"'"
if exist "%LOG%" (
  echo.
  type "%LOG%"
)
echo.
pause
endlocal
