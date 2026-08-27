"""Consolidated pipeline health check — the ONE place that answers "is
everything OK" for the whole automation stack (daily_runner, health_watchdog,
daily_capture_report, vault sync, Ollama, ntfy delivery).

WHY THIS EXISTS: built 2026-08-27 after a full 6-day silent outage went
completely undetected. daily_runner.log's last real entry was 2026-08-21;
nothing ran since. Root cause: Task Scheduler refused to even start the
Nightly Runner task (Win32 error 4320, "The operator or administrator has
refused the request") — this machine's task has DisallowStartIfOnBatteries /
StopIfGoingOnBatteries set, and it's a laptop. health_watchdog.py itself runs
INSIDE that same nightly job, so it never got a chance to raise the alarm —
a single point of failure: the watchdog depends on the exact thing it's
supposed to be watching.

This script's freshness check is deliberately NOT "did Task Scheduler report
success" — that field can look fine even when the underlying script silently
hangs or gets killed mid-run. It's "when did the log this job is SUPPOSED to
write actually last get touched," which is the one signal that survives
every failure mode found this session, including the one that hid the outage
in the first place. Task Scheduler's own result code is still queried and
surfaced as a supporting detail, decoded when it's the known battery-refusal
code, but it is never the primary pass/fail signal.

Does NOT replace daily_runner/health_watchdog/vault_librarian/
daily_capture_report — it sits above them and reports on whether they're
actually doing their jobs. Reuses daily_capture_report's backlog/vault
logic directly rather than re-implementing it.

Zero Gemini calls: every check here is a read against already-existing state
or a live service ping (backend /health, Ollama /api/tags, ntfy.sh).

Usage:
    python scripts/pipeline_health.py             # run checks, alert only if degraded/broken
    python scripts/pipeline_health.py --dry-run    # print the report, never push, never write files
"""
from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("reelbrain.pipeline_health")

LOG_FILE = "pipeline_health.log"
NIGHTLY_TASK_NAME = "ReelBrain Nightly Runner"
DAILY_RUNNER_LOG = Path("daily_runner.log")
MAX_LOG_AGE_HOURS = 30.0  # the nightly trigger is daily; 30h gives real slack
GEMINI_DAILY_LIMIT = 20
# Backup channel that doesn't depend on ntfy: a dated, impossible-to-miss
# file in the repo root. Old ones are cleaned up so this never accumulates
# forever (see _cleanup_old_markers).
MARKER_PREFIX = "PIPELINE_ALERT_"
MARKER_RETENTION_DAYS = 14

# Windows Task Scheduler result codes worth naming plainly rather than
# leaving as a bare number -- 4320 is exactly what caused the 2026-08-21
# outage, and is worth calling out by name every time it recurs.
KNOWN_SCHEDULER_CODES = {
    0: "success",
    2147946720: "refused by Task Scheduler policy (4320) -- likely "
                "DisallowStartIfOnBatteries/StopIfGoingOnBatteries and the "
                "machine was on battery",
}


@dataclass
class Check:
    name: str
    state: str  # "pass" | "degraded" | "fail"
    detail: str


# --- pure check logic (no I/O; every input is already fetched) --------------------


def check_backend_health(payload: Optional[dict]) -> Check:
    if payload is None:
        return Check("backend", "fail", "/health unreachable")
    problems = []
    if payload.get("status") != "ok":
        problems.append(f"status={payload.get('status')!r}")
    if payload.get("cookie_health") != "ok":
        problems.append(f"cookie_health={payload.get('cookie_health')!r}")
    if payload.get("sqlite_vec") is not True:
        problems.append(f"sqlite_vec={payload.get('sqlite_vec')!r}")
    return Check(
        "backend", "pass" if not problems else "fail",
        "; ".join(problems) or "status ok, cookie_health ok, sqlite_vec true",
    )


def check_nightly_freshness(
    log_age_hours: Optional[float],
    max_age_hours: float = MAX_LOG_AGE_HOURS,
    scheduler_detail: str = "",
) -> Check:
    """The core check this whole script exists for. log_age_hours is hours
    since daily_runner.log was last WRITTEN (not just "task ran") -- see the
    module docstring for why that distinction is the actual fix here."""
    suffix = f" | {scheduler_detail}" if scheduler_detail else ""
    if log_age_hours is None:
        return Check("nightly_pipeline", "fail", "daily_runner.log not found -- has it ever run?" + suffix)
    if log_age_hours > max_age_hours:
        return Check(
            "nightly_pipeline", "fail",
            f"daily_runner.log last written {log_age_hours:.1f}h ago (limit {max_age_hours:.0f}h)" + suffix,
        )
    return Check("nightly_pipeline", "pass", f"daily_runner.log fresh ({log_age_hours:.1f}h ago)" + suffix)


def check_ollama(reachable: bool) -> Check:
    return Check(
        "ollama", "pass" if reachable else "degraded",
        "reachable at :11434" if reachable else "not reachable -- local plain_summary backfill will no-op",
    )


def check_backlog_trend(total_backlog: int, delta: Optional[int]) -> Check:
    """delta: today's backlog minus the last recorded day's, or None with no
    prior day (see daily_capture_report.trend_delta, reused not duplicated)."""
    if delta is None:
        return Check("backlog_trend", "pass", f"{total_backlog} rows pending (no prior day to compare yet)")
    if delta > 0:
        return Check("backlog_trend", "degraded", f"{total_backlog} rows pending, GREW by {delta} since last check")
    if delta < 0:
        return Check("backlog_trend", "pass", f"{total_backlog} rows pending, shrank by {-delta}")
    return Check("backlog_trend", "pass", f"{total_backlog} rows pending, unchanged")


def check_vault_sync(vault_notes: int, notion_rows: int) -> Check:
    if vault_notes == notion_rows:
        return Check("vault_sync", "pass", f"{vault_notes}/{notion_rows} matched")
    return Check(
        "vault_sync", "fail",
        f"vault {vault_notes} vs Notion {notion_rows} (drift {vault_notes - notion_rows:+d})",
    )


def check_gemini_quota(
    remaining: Optional[int],
    limit: int = GEMINI_DAILY_LIMIT,
    quota_log_age_hours: Optional[float] = None,
    backlog: int = 0,
) -> Check:
    """A full, untouched daily quota for a long time WHILE a real backlog
    exists is itself a symptom worth surfacing here too -- a second,
    independent signal pointing at the same root cause check_nightly_freshness
    catches (confirmed live 2026-08-27: quota sat at 20/20 for 3 days
    straight while the backlog grew from 132 to 139, because nothing was
    running at all -- not because nothing needed doing)."""
    if remaining is None:
        return Check("gemini_quota", "degraded", "could not read quota state")
    if remaining >= limit and backlog > 0 and quota_log_age_hours is not None and quota_log_age_hours > MAX_LOG_AGE_HOURS:
        return Check(
            "gemini_quota", "degraded",
            f"{remaining}/{limit} remaining but unused for {quota_log_age_hours:.0f}h with "
            f"{backlog} rows pending -- looks like nothing is running, not that nothing needs doing",
        )
    return Check("gemini_quota", "pass", f"{remaining}/{limit} remaining today")


def check_ntfy_delivery(attempted: bool, status_code: Optional[int], error: Optional[str]) -> Check:
    """Verified end-to-end: this reads ntfy.sh's OWN response, not just
    whether our code raised. Only exercised when this run actually has
    something to alert about (see should_alert) -- forcing a real send every
    single night just to test delivery would mean a phone notification on
    every healthy night too, trading the "silence is healthy" convention this
    whole project already relies on for a check that mostly confirms nothing
    new. The trade-off: a permanently-broken ntfy path stays unverified on a
    night where nothing else is wrong. Accepted deliberately -- see
    PROGRESS.md for the reasoning, and the backup marker-file channel exists
    specifically so a broken ntfy path is never the ONLY way to notice."""
    if not attempted:
        return Check("ntfy_delivery", "pass", "not exercised this run (nothing to alert about)")
    if status_code == 200:
        return Check("ntfy_delivery", "pass", "ntfy.sh accepted the push (200)")
    return Check("ntfy_delivery", "fail", f"ntfy.sh rejected/unreachable: {error or status_code}")


def summarize(checks: list[Check]) -> str:
    """Worst state across all checks: fail > degraded > pass."""
    states = {c.state for c in checks}
    if "fail" in states:
        return "fail"
    if "degraded" in states:
        return "degraded"
    return "pass"


def should_alert(overall: str) -> bool:
    return overall != "pass"


def format_report(checks: list[Check], overall: str, stamp: str) -> str:
    """ONE consolidated block -- an all-green one-liner when healthy, an
    itemized list of exactly what needs attention when not."""
    if overall == "pass":
        return f"[{stamp}] ALL GREEN — {len(checks)}/{len(checks)} checks passing."
    bad = [c for c in checks if c.state != "pass"]
    lines = [f"[{stamp}] {overall.upper()} — {len(bad)}/{len(checks)} check(s) need attention:"]
    marker = {"pass": "OK  ", "degraded": "WARN", "fail": "FAIL"}
    for c in checks:
        lines.append(f"  [{marker[c.state]}] {c.name}: {c.detail}")
    return "\n".join(lines)


# --- real I/O, wired only in run()/main() -------------------------------------------


def _decode_scheduler_result(task_name: str = NIGHTLY_TASK_NAME) -> str:
    """Best-effort: schtasks' own Last Run Time/Last Result, as a supporting
    detail only -- see the module docstring for why this is never the
    primary signal. Never raises; an empty string means "couldn't query,"
    which the caller treats as simply no extra detail, not a failure."""
    try:
        result = subprocess.run(
            ["schtasks", "/query", "/tn", task_name, "/fo", "csv", "/v"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return ""
        import csv
        import io

        rows = list(csv.reader(io.StringIO(result.stdout)))
        if len(rows) < 2:
            return ""
        header, values = rows[0], rows[1]
        row = dict(zip(header, values))
        last_run = row.get("Last Run Time", "?")
        raw_result = row.get("Last Result", "").strip()
        try:
            code = int(raw_result) & 0xFFFFFFFF  # normalize signed/unsigned
        except ValueError:
            return f"scheduler: last run {last_run}, result {raw_result!r}"
        meaning = KNOWN_SCHEDULER_CODES.get(code, f"code {code}")
        return f"scheduler: last run {last_run}, {meaning}"
    except Exception:  # noqa: BLE001 - this is a supporting detail, never load-bearing
        logger.warning("could not query Task Scheduler", exc_info=True)
        return ""


def _fetch_backend_health(base_url: str) -> Optional[dict]:
    try:
        import httpx

        resp = httpx.get(f"{base_url.rstrip('/')}/health", timeout=15.0)
        resp.raise_for_status()
        return resp.json()
    except Exception:  # noqa: BLE001 - unreachable IS the failure signal
        logger.warning("backend health fetch failed", exc_info=True)
        return None


def _ollama_reachable() -> bool:
    try:
        import httpx

        from app.local_llm import OLLAMA_HOST

        r = httpx.get(f"{OLLAMA_HOST}/api/tags", timeout=5.0)
        return r.status_code == 200
    except Exception:  # noqa: BLE001
        return False


def _log_age_hours(path: Path, now: datetime) -> Optional[float]:
    if not path.exists():
        return None
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return (now - mtime).total_seconds() / 3600


def _send_ntfy_and_verify(message: str, title: str) -> tuple[bool, Optional[int], Optional[str]]:
    """Real POST to ntfy.sh, returning ntfy's OWN response -- not app.alerts'
    swallowed bool. This IS the "verified end-to-end" ntfy_delivery check;
    it's local-only (never runs on Render, never exposed over HTTP), so
    logging the real status/error here has none of the exposure concern the
    earlier temporary digest.py diagnostic was reverted over."""
    import os

    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        return False, None, "NTFY_TOPIC not set"
    try:
        import httpx

        resp = httpx.post(
            f"https://ntfy.sh/{topic}",
            content=message.encode("utf-8"),
            headers={"Title": title, "Priority": "high", "Tags": "rotating_light"},
            timeout=10.0,
        )
        return True, resp.status_code, (None if resp.status_code == 200 else resp.text[:200])
    except Exception as exc:  # noqa: BLE001 - reported to the caller, never raised
        return True, None, f"{type(exc).__name__}: {exc}"


def _cleanup_old_markers(repo_root: Path, now: datetime, retention_days: int = MARKER_RETENTION_DAYS) -> None:
    for f in repo_root.glob(f"{MARKER_PREFIX}*.txt"):
        try:
            age_days = (now - datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)).days
            if age_days > retention_days:
                f.unlink()
        except OSError:
            continue


def gather_checks(now: Optional[datetime] = None) -> list[Check]:
    """Real data collection, wired to the pure check functions above. The
    backlog/vault numbers come from daily_capture_report.collect() and its
    history helpers directly -- not re-implemented here."""
    import os

    from scripts import daily_capture_report as dcr

    now = now or datetime.now(timezone.utc)
    base_url = os.environ.get("REELBRAIN_URL", "https://reelbrain.onrender.com")

    backend_payload = _fetch_backend_health(base_url)
    scheduler_detail = _decode_scheduler_result()
    log_age = _log_age_hours(DAILY_RUNNER_LOG, now)
    ollama_up = _ollama_reachable()

    data = dcr.collect(now=now)
    snapshot = data["snapshot"]
    history = dcr.load_history(Path(dcr.HISTORY_FILE))
    today_str = now.strftime("%Y-%m-%d")
    prev_backlog = dcr.latest_prior_backlog(history, today_str)
    delta = dcr.trend_delta(prev_backlog, snapshot["total_backlog"])

    from app import gemini_pipe, gemini_quota

    quota_remaining = None
    try:
        quota_remaining = gemini_quota.remaining_today(gemini_pipe.GEMINI_MODEL, limit=GEMINI_DAILY_LIMIT)
    except Exception:  # noqa: BLE001
        logger.warning("could not read Gemini quota state", exc_info=True)
    quota_log_age = _log_age_hours(Path("gemini_quota.json"), now)

    return [
        check_backend_health(backend_payload),
        check_nightly_freshness(log_age, scheduler_detail=scheduler_detail),
        check_ollama(ollama_up),
        check_backlog_trend(snapshot["total_backlog"], delta),
        check_vault_sync(data["vault_notes"], data["notion_rows"]),
        check_gemini_quota(
            quota_remaining, backlog=snapshot["total_backlog"], quota_log_age_hours=quota_log_age,
        ),
    ]


def run(dry_run: bool = False, print_fn: Callable[[str], None] = print) -> dict:
    now = datetime.now(timezone.utc)
    checks = gather_checks(now)
    overall = summarize(checks)
    stamp = now.strftime("%Y-%m-%d %H:%M UTC")

    ntfy_attempted = False
    ntfy_status: Optional[int] = None
    ntfy_error: Optional[str] = None

    if should_alert(overall) and not dry_run:
        pre_report = format_report(checks, overall, stamp)
        ntfy_attempted, ntfy_status, ntfy_error = _send_ntfy_and_verify(
            pre_report, title="ReelBrain pipeline health"
        )

    # ntfy_delivery is itself a check -- appended after the send attempt so
    # its own outcome is part of the SAME report, not a separate message.
    checks.append(check_ntfy_delivery(ntfy_attempted, ntfy_status, ntfy_error))
    overall = summarize(checks)  # re-derive: a failed ntfy send can itself flip this to fail
    report = format_report(checks, overall, stamp)
    print_fn(report)

    repo_root = Path(__file__).resolve().parent.parent
    if not dry_run:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(report + "\n")
        _cleanup_old_markers(repo_root, now)
        if should_alert(overall):
            # Backup channel that does NOT depend on ntfy being healthy --
            # written whenever anything needs attention, regardless of
            # whether the ntfy push above succeeded, so a broken ntfy path
            # is never the only way this gets noticed.
            marker_path = repo_root / f"{MARKER_PREFIX}{now.strftime('%Y-%m-%d')}.txt"
            marker_path.write_text(report + "\n", encoding="utf-8")
            print_fn(f"(wrote backup marker: {marker_path.name})")

    return {"overall": overall, "checks": checks, "report": report}


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    logging.basicConfig(level=logging.WARNING)

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true",
                        help="print the report but never push, never write pipeline_health.log/markers")
    args = parser.parse_args()

    result = run(dry_run=args.dry_run)
    sys.exit(0 if result["overall"] == "pass" else 1)


if __name__ == "__main__":
    main()
