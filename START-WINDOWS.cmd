@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
    py -3 "%~dp0nova3_start.py" %*
    goto finish
)
where python >nul 2>nul
if not errorlevel 1 (
    python "%~dp0nova3_start.py" %*
    goto finish
)
echo Python 3.10+ is missing.
echo Run this in PowerShell, then reopen START-WINDOWS.cmd:
echo     winget install -e --id Python.Python.3.12
:finish
echo.
echo Press any key to close this window.
pause >nul
