@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 setup_renderer.py
) else (
    python setup_renderer.py
)
endlocal
