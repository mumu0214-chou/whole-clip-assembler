@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 verify_render.py
) else (
    python verify_render.py
)
endlocal
