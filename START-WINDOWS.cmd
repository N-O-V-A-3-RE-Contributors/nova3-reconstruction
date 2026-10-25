@echo off
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
  py -3 nova3_easy.py %*
) else (
  python nova3_easy.py %*
)
echo.
pause
