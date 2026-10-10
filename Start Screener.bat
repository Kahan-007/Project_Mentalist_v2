@echo off
cd /d "%~dp0"
where py >nul 2>nul && (py -m india_screener serve) || (python -m india_screener serve)
if errorlevel 1 pause
