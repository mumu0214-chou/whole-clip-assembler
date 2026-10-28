@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 verify_timeline_render.py --no-pause
) else (
    python verify_timeline_render.py --no-pause
)
pause
endlocal
