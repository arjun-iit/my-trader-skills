@echo off
rem India Trader: double-click to start. Needs Python 3.8+ from python.org (tick "tcl/tk" during install).
cd /d "%~dp0"
where pyw >nul 2>nul && (start "" pyw -3 trader_app.py & exit /b)
where pythonw >nul 2>nul && (start "" pythonw trader_app.py & exit /b)
python trader_app.py
if errorlevel 1 pause
