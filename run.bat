@echo off
setlocal
cd /d "%~dp0"
rem NOTE: ASCII only, CRLF only. Korean text goes in Python, not in .bat files.
if not exist .venv\Scripts\python.exe (
  echo Run setup.bat first.
  pause
  exit /b 1
)
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
if "%~1"=="" goto :web

rem With arguments = CLI. e.g. run.bat trial   /   run.bat all --job <job>   /   run.bat review --only Q:m01-
.venv\Scripts\python -m errata %*
exit /b %errorlevel%

:web
rem No arguments (double-click) = web app http://127.0.0.1:5191  (keep this window open while it runs)
.venv\Scripts\python -m errata.web.server
if errorlevel 1 pause
exit /b %errorlevel%
