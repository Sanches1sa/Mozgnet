@echo off
rem ============================================================
rem  install.bat - neuro12-cloud: oblačno mozg-12 for Windows 11
rem  Double-click this file to install. Needs Python 3.8+.
rem ============================================================
setlocal
cd /d "%~dp0"
where powershell >nul 2>&1
if errorlevel 1 (
  echo [XX] PowerShell not found. Windows 11 always has it.
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
echo.
echo (window can be closed)
pause
endlocal
