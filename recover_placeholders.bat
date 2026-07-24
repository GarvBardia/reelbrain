@echo off
REM Placeholder recovery worker — launched by Windows Task Scheduler.
REM See WORKER_SETUP.md for the scheduling steps.
cd /d "%~dp0"
call venv\Scripts\activate.bat
python scripts\recover_placeholders.py >> recover_placeholders.log 2>&1
