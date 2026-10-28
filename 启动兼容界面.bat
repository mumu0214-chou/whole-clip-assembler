@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 app.py --tk
) else (
    python app.py --tk
)
if errorlevel 1 pause
endlocal
