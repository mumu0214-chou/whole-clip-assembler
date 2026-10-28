@echo off
setlocal
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 goto use_python
py -3 rename_a_to_e.py %*
goto done
:use_python
python rename_a_to_e.py %*
:done
set "RESULT=%errorlevel%"
if "%~1"=="" pause
endlocal & exit /b %RESULT%
