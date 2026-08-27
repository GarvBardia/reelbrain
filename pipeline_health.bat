@echo off
REM Launcher for the "ReelBrain Pipeline Health" Task Scheduler entry --
REM deliberately separate from nightly_autonomous.bat and its task. See
REM WORKER_SETUP.md: this needs to keep running even when the Nightly Runner
REM task can't (that's exactly the failure mode that hid the 2026-08-21..27
REM outage), so it has no battery restriction and its own schedule, every 4h.
cd /d "%~dp0"
call venv\Scripts\activate.bat
python scripts\pipeline_health.py >> pipeline_health_task.log 2>&1
