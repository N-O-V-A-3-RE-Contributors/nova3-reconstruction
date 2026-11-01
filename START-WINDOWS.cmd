@echo off
rem NOVA3 worker -- Windows launcher (double-click). First run: setup + your API key.
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
  py -3 tools\dsh_loop\install.py --workspace "%CD%" --then-run %*
) else (
  python tools\dsh_loop\install.py --workspace "%CD%" --then-run %*
)
if errorlevel 1 pause
