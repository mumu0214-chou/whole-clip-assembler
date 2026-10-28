@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 upgrade.py
) else (
    python upgrade.py
)
if errorlevel 1 pause
endlocal
