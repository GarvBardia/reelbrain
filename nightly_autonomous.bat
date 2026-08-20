@echo off
REM Nightly autonomous pass — launched by Windows Task Scheduler.
REM Spends the day's Gemini quota across the pending backlog in priority
REM order, then stops cleanly and resumes tomorrow. See WORKER_SETUP.md.
REM
REM Runs the daily backlog pass, then the health watchdog (which pushes a
REM single ntfy notification only if something is wrong -- silence is healthy),
REM then the capture report (which tracks the backlog trend and pushes only
REM when the backlog GREW that day). All three are one Task Scheduler entry.
cd /d "%~dp0"
call venv\Scripts\activate.bat
python scripts\daily_runner.py >> nightly_autonomous.log 2>&1
python scripts\health_watchdog.py >> nightly_autonomous.log 2>&1
python scripts\daily_capture_report.py >> nightly_autonomous.log 2>&1
