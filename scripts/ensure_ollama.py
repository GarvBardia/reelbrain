"""Ensure Ollama is up before the nightly local-LLM step runs.

WHY: confirmed live 2026-08-21 (pipeline health audit) -- Ollama wasn't
running when the Task Scheduler job fired at 18:00, and daily_runner's
plain_summary step (Ollama-routed, see app.llm_router.TASK_PROVIDERS)
silently processed 0 of 160 pending rows because of it. Nothing failed
loudly: daily_runner's `local` step handling degrades cleanly around a
missing local provider (that resilience is correct and stays), which is
exactly why this went unnoticed until an explicit audit read the log.

This script's only job is to give that step its best shot: check if Ollama
is already serving, start it if not, and actually confirm it accepts a
request before nightly_autonomous.bat moves on -- rather than firing
daily_runner.py immediately after a bare, unconfirmed `start`.

It must NEVER block the Gemini-routed steps, which do not depend on Ollama
at all -- so this always exits 0, whether or not Ollama ends up running.
If it can't be started, plain_summary degrades exactly as it did tonight
(ollama_stopped=True, every other step still runs); this script just makes
that a logged, understood outcome instead of a silent one.

Usage:
    python scripts/ensure_ollama.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from typing import Callable

STARTUP_TIMEOUT_SECONDS = 30
POLL_INTERVAL_SECONDS = 2

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434").strip()
# Matches this machine's real install path (confirmed via `where ollama` in
# the 2026-08-21 audit); overridable since Task Scheduler's launch context
# doesn't always share an interactive session's PATH.
OLLAMA_EXE = os.environ.get(
    "OLLAMA_EXE",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe"),
)


def ensure_ollama(
    is_up_fn: Callable[[], bool],
    start_fn: Callable[[], bool],
    *,
    sleep_fn: Callable[[float], None] = time.sleep,
    now_fn: Callable[[], float] = time.monotonic,
    timeout_seconds: float = STARTUP_TIMEOUT_SECONDS,
    poll_interval: float = POLL_INTERVAL_SECONDS,
    print_fn: Callable[[str], None] = print,
) -> bool:
    """Pure orchestration; all I/O injected so this is testable without a
    real Ollama process, real HTTP calls, or real sleeps. Returns whether
    Ollama is confirmed serving by the time this returns -- the caller
    (main) always exits 0 regardless, since a local-only degrade must never
    block the Gemini-routed steps that run after it."""
    if is_up_fn():
        print_fn(f"[ensure_ollama] already serving at {OLLAMA_HOST}")
        return True

    print_fn(f"[ensure_ollama] not reachable at {OLLAMA_HOST} -- attempting to start")
    if not start_fn():
        print_fn("[ensure_ollama] FAILED to launch -- plain_summary will degrade "
                 "cleanly and skip for today (see app.local_llm.OllamaUnavailable)")
        return False

    deadline = now_fn() + timeout_seconds
    while now_fn() < deadline:
        if is_up_fn():
            print_fn(f"[ensure_ollama] confirmed serving at {OLLAMA_HOST}")
            return True
        sleep_fn(poll_interval)

    print_fn(f"[ensure_ollama] FAILED: started but never answered a request within "
             f"{timeout_seconds:.0f}s -- plain_summary will degrade cleanly and skip "
             "for today (see app.local_llm.OllamaUnavailable)")
    return False


# --- real I/O, wired only in main() -------------------------------------------------


def _real_is_up() -> bool:
    try:
        import httpx

        r = httpx.get(f"{OLLAMA_HOST}/api/tags", timeout=5.0)
        return r.status_code == 200
    except Exception:  # noqa: BLE001 - "not up" covers every failure mode here
        return False


def _real_start() -> bool:
    if not os.path.exists(OLLAMA_EXE):
        print(f"[ensure_ollama] ollama.exe not found at {OLLAMA_EXE} "
              "-- set OLLAMA_EXE if it's installed elsewhere")
        return False
    try:
        # Detached: this script must return promptly either way, and the
        # server needs to keep running after this process exits (it's a
        # long-lived daemon, not a one-shot command).
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        subprocess.Popen(
            [OLLAMA_EXE, "serve"],
            creationflags=creationflags,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception as exc:  # noqa: BLE001 - reported to the caller as a failed start
        print(f"[ensure_ollama] failed to launch {OLLAMA_EXE}: {exc}")
        return False


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    ensure_ollama(_real_is_up, _real_start)
    # Always exit 0 -- see the module docstring. This step giving up is not
    # a reason to abort daily_runner.py, health_watchdog.py, or
    # daily_capture_report.py, none of which depend on Ollama.


if __name__ == "__main__":
    main()
