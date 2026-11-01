@echo off
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel% == 0 (
  py -3 JOIN-OPENROUTER.py %*
) else (
  python JOIN-OPENROUTER.py %*
)
pause
