@echo off
cd /d "%~dp0"
"C:\Users\kahan\AppData\Local\Python\pythoncore-3.14-64\pythonw.exe" -m india_screener daily >> "data\logs\daily.log" 2>&1
