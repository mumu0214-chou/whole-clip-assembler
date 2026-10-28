@echo off
setlocal
chcp 65001 >nul
set "PIP_CACHE_DIR=D:\GPTData\cache\pip"
set "TEMP=D:\GPTData\temp"
set "TMP=D:\GPTData\temp"
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 install_qt_ui.py
) else (
    python install_qt_ui.py
)
if errorlevel 1 pause
endlocal
