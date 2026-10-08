@echo off
setlocal
cd /d "%~dp0"
rem ==========================================================================
rem  Errata engine - first-time setup (safe to run again)
rem  NOTE: ASCII only, CRLF only. Korean messages live in Python.
rem  Needs: python 3.11-3.13 (py launcher), git, and the claude CLI logged in
rem  (Claude Code subscription, no API key). All data goes to .\data (not in git).
rem  The MatrAIx persona code/data are fetched on the first "panel" run.
rem ==========================================================================
set PYTHONUTF8=1
if not exist .venv\Scripts\python.exe (
  py -3.12 -m venv .venv 2>nul || python -m venv .venv
  if errorlevel 1 goto :fail
)
.venv\Scripts\python -m pip install -q --upgrade pip
.venv\Scripts\python -m pip install -q -r requirements.txt
if errorlevel 1 goto :fail
if not exist data\jobs mkdir data\jobs
if not exist data\pack mkdir data\pack
echo Setup done. Open this folder in Claude Code and say: start  (or double-click run.bat)
pause
exit /b 0
:fail
echo Setup failed.
pause
exit /b 1