@echo off
cd /d "%~dp0"
where pythonw.exe >nul 2>nul
if %errorlevel%==0 (
  start "" pythonw.exe "screenplay_annotation_tool.pyw"
) else (
  python -m app.desktop
)
