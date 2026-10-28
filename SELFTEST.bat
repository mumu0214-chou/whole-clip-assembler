@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 verify_processing_render.py
) else (
    python verify_processing_render.py
)
if errorlevel 1 pause
endlocal
