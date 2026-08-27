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
REM Confirmed live 2026-08-21 (pipeline health audit): Ollama wasn't running
REM at 18:00, so daily_runner's plain_summary step silently processed 0 of
REM 160 pending rows. This starts it and waits for a real response before
REM daily_runner runs -- always exits 0, never blocks the Gemini-routed
REM steps below, which don't depend on Ollama at all.
python scripts\ensure_ollama.py >> nightly_autonomous.log 2>&1
python scripts\daily_runner.py >> nightly_autonomous.log 2>&1
python scripts\health_watchdog.py >> nightly_autonomous.log 2>&1
python scripts\daily_capture_report.py >> nightly_autonomous.log 2>&1
REM pipeline_health.py deliberately does NOT run here (2026-08-27). It's on
REM its own separate "ReelBrain Pipeline Health" Task Scheduler entry every
REM 4h, with no battery restriction -- the whole point is that it must keep
REM checking even when THIS task can't run at all (which is exactly what
REM caused the 6-day silent outage this script was built to catch). Wiring
REM it in here too would put it right back inside the single point of
REM failure it exists to sit outside of. See WORKER_SETUP.md.
