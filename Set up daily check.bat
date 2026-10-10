@echo off
cd /d "%~dp0"
where py >nul 2>nul && (py -m india_screener schedule install --time 18:30) || (python -m india_screener schedule install --time 18:30)
pause
