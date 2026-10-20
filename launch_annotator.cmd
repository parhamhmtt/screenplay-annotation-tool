@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" desktop\app.py
) else (
  python desktop\app.py
)
if errorlevel 1 (
  echo.
  echo See README.md for setup. The updated app requires PySide6.
  pause
)
