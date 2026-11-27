@echo off
rem Double-click to start the AI Shorts Clipper web app (http://127.0.0.1:8765).
rem Keep this window open while you use the app; close it to stop.
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" run_app.py %*
) else (
  python run_app.py %*
)
if errorlevel 1 pause
