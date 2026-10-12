@echo off
cd /d "%~dp0"
if not exist "data\logs" mkdir "data\logs"
where py >nul 2>nul
if errorlevel 1 (
  python -m india_screener daily >> "data\logs\daily.log" 2>&1
) else (
  py -m india_screener daily >> "data\logs\daily.log" 2>&1
)
