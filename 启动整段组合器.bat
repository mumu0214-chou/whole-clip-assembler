@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 app.py
) else (
    python app.py
)
if errorlevel 1 (
    echo.
    echo 启动失败。请确认已安装 Python 3.10 或更新版本，且完整解压了所有文件。
    pause
)
endlocal
