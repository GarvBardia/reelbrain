"""Daily capture-vs-processing health report — is the backlog growing or shrinking?

The ongoing constraint on this project has never been capture; it's Gemini
quota. Reels come in faster than a 20-call/day free tier can extract them, so
the number that actually matters is the *trend*: did today's captures outrun
today's processing, or is the backlog finally draining?

This is a pure READ. It makes ZERO Gemini calls -- it only reads Notion, the
Obsidian vault, and a small local history file. It must never compete with the
extraction backlog for the very quota it's reporting on, which is the whole
reason it exists as a separate step from daily_runner.

What it answers each day:
  * how many new rows were captured in the last 24h,
  * of those, how many already finished extraction vs are still waiting,
  * the total backlog (content_type=unknown OR a pipeline-marker topic),
  * whether that backlog GREW or SHRANK versus the last recorded day,
  * a rough drain estimate at the recent observed Gemini pace,
  * whether the Obsidian vault is still 1:1 with Notion.

Output: one plain-English paragraph (same voice as daily_runner.log), appended
to daily_capture_report.log. A single ntfy push fires ONLY when the backlog
grew -- a shrinking or steady backlog is good news and stays silent, the same
"noise means look now" discipline health_watchdog uses.

Usage:
    python scripts/daily_capture_report.py
    python scripts/daily_capture_report.py --dry-run   # print, never push, never write history
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("reelbrain.daily_capture_report")

LOG_FILE = "daily_capture_report.log"
HISTORY_FILE = "backlog_history.json"
CAPTURE_WINDOW_HOURS = 24
# recover_placeholders spends an extraction call plus up to a few research
# calls per row -- the same 3 daily_runner.ROW_COST and backlog_status.COSTS
# both use for this backlog. Kept in sync with them by hand (they don't import
# each other) and asserted in the tests.
MULTIMODAL_CALLS_PER_ROW = 3
# The Notion topic values that are pipeline-state markers, not real subjects --
# a row carrying one of these has not been genuinely categorized yet.
BACKLOG_MARKER_TOPICS = ("uncategorized", "pending-extraction")
# Fallback drain pace when daily_runner.log has no parseable recent runs. The
# observed free-tier limit; labelled as an assumption in the summary so it's
# never mistaken for a measured rate.
FALLBACK_CALLS_PER_DAY = 18


# --- pure logic (no Notion, no clock, no files) -----------------------------------


def is_backlog_row(content_type: str, topics: list[str]) -> bool:
    """A row still counts as backlog when extraction hasn't genuinely finished:
    either the content type never resolved past 'unknown', or its only topics
    are pipeline-state markers. Matches the query verified in prior sessions
    (backlog_status.py's marker logic + the content_type gate)."""
    if content_type.strip().lower() in ("", "unknown"):
        return True
    return any(t in BACKLOG_MARKER_TOPICS for t in topics)


def within_window(created_iso: str, now: datetime, hours: int = CAPTURE_WINDOW_HOURS) -> bool:
    if not created_iso:
        return False
    created = datetime.fromisoformat(created_iso.replace("Z", "+00:00"))
    return (now - created) <= timedelta(hours=hours)


def partition_capture(rows: list[dict], now: datetime,
                      hours: int = CAPTURE_WINDOW_HOURS) -> dict:
    """rows: [{created_time, content_type, topics}]. Splits the last `hours`
    of captures into finished vs still-waiting, and counts the whole backlog
    across ALL rows (not just today's)."""
    captured = [r for r in rows if within_window(r["created_time"], now, hours)]
    captured_pending = [
        r for r in captured if is_backlog_row(r["content_type"], r["topics"])
    ]
    total_backlog = sum(1 for r in rows if is_backlog_row(r["content_type"], r["topics"]))
    unknown_type = sum(1 for r in rows if r["content_type"].strip().lower() in ("", "unknown"))
    return {
        "captured_today": len(captured),
        "captured_today_pending": len(captured_pending),
        "captured_today_processed": len(captured) - len(captured_pending),
        "total_backlog": total_backlog,
        "needs_extraction": unknown_type,
    }


def parse_recent_pace(log_lines: list[str], samples: int = 5) -> Optional[float]:
    """Average Gemini calls/day over the last `samples` daily_runner runs that
    actually spent quota. Reads the 'N/M calls used' field that
    daily_runner.format_log_paragraph writes. The label on that field changed
    from 'Quota:' to 'Gemini:' partway through the log's history, so both are
    accepted -- matching only the current one silently ignored every older run
    and forced the fallback pace. Runs that spent 0 (a fully quota-blocked or
    nothing-pending day) are skipped so they don't drag the pace to an
    unrealistically slow number."""
    used = []
    for line in reversed([ln for ln in log_lines if ln.strip()]):
        m = re.search(r"(?:Gemini|Quota):\s*(\d+)/\d+\s*calls used", line)
        if not m:
            continue
        n = int(m.group(1))
        if n > 0:
            used.append(n)
        if len(used) >= samples:
            break
    if not used:
        return None
    return sum(used) / len(used)


def compute_processed_today(prev_backlog: Optional[int], captured_today_pending: int,
                            current_backlog: int) -> Optional[int]:
    """Net rows that LEFT the backlog today (i.e. genuinely finished), derived
    from the trend rather than per-field edit history Notion doesn't expose:
        current = prev + (new unprocessed arrivals) - (rows that finished)
    so  finished = prev + new_unprocessed - current.
    None on the first ever run, when there's no prior day to difference against.
    Floored at 0 -- a negative would just mean the estimate's assumptions
    (e.g. a deletion) don't hold, not that negative rows were processed."""
    if prev_backlog is None:
        return None
    return max(0, prev_backlog + captured_today_pending - current_backlog)


def trend_delta(prev_backlog: Optional[int], current_backlog: int) -> Optional[int]:
    """current - prev; None when there's no prior day. Positive = grew."""
    if prev_backlog is None:
        return None
    return current_backlog - prev_backlog


def should_push(delta: Optional[int]) -> bool:
    """Push only when the backlog GREW. First run (delta None) and any
    shrink-or-steady day stay silent."""
    return delta is not None and delta > 0


def estimate_days_to_clear(needs_extraction: int, pace: Optional[float]) -> Optional[int]:
    """Rows needing real extraction, at MULTIMODAL_CALLS_PER_ROW each, over the
    recent (or fallback) calls/day pace. None when there's nothing to clear."""
    if needs_extraction <= 0:
        return 0
    effective = pace if pace and pace > 0 else FALLBACK_CALLS_PER_DAY
    return math.ceil(needs_extraction * MULTIMODAL_CALLS_PER_ROW / effective)


def format_summary(*, date_str: str, snapshot: dict, prev_backlog: Optional[int],
                   processed_today: Optional[int], pace: Optional[float],
                   days_to_clear: Optional[int], vault_notes: int,
                   notion_rows: int) -> str:
    """One paragraph, daily_runner.log voice."""
    captured = snapshot["captured_today"]
    pending = snapshot["captured_today_pending"]
    backlog = snapshot["total_backlog"]
    delta = trend_delta(prev_backlog, backlog)

    if delta is None:
        trend = f"backlog at {backlog} (first tracked day — no prior to compare)"
    elif delta > 0:
        trend = f"backlog GREW from {prev_backlog} to {backlog} (+{delta})"
    elif delta < 0:
        trend = f"backlog shrank from {prev_backlog} to {backlog} ({delta})"
    else:
        trend = f"backlog held steady at {backlog}"

    proc = "unknown (first run)" if processed_today is None else str(processed_today)

    if days_to_clear == 0:
        pace_bit = "nothing left needing extraction"
    else:
        pace_src = (f"~{pace:.0f} calls/day observed" if pace and pace > 0
                    else f"~{FALLBACK_CALLS_PER_DAY} calls/day assumed")
        pace_bit = (f"at {pace_src}, {MULTIMODAL_CALLS_PER_ROW} calls/row, "
                    f"~{days_to_clear} day(s) to clear the {snapshot['needs_extraction']} "
                    f"still needing extraction")

    vault_bit = (f"Vault: {vault_notes}/{notion_rows}, synced"
                 if vault_notes == notion_rows
                 else f"Vault: {vault_notes}/{notion_rows}, DRIFT {vault_notes - notion_rows:+d}")

    return (f"{date_str}: {captured} captured today ({pending} still unprocessed), "
            f"~{proc} processed today, {trend}. "
            f"{pace_bit}. {vault_bit}.")


# --- history persistence ----------------------------------------------------------


def load_history(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        logger.warning("backlog history unreadable — starting fresh", exc_info=True)
        return []


def latest_prior_backlog(history: list[dict], today: str) -> Optional[int]:
    """The total_backlog of the most recent entry whose date is BEFORE today.
    Skips any earlier run from today itself so a second same-day run compares
    against yesterday, not against this morning."""
    for entry in reversed(history):
        if entry.get("date", "") < today:
            return entry.get("total_backlog")
    return None


def upsert_today(history: list[dict], entry: dict) -> list[dict]:
    """Replace an existing same-date entry (idempotent re-runs) or append."""
    out = [e for e in history if e.get("date") != entry["date"]]
    out.append(entry)
    out.sort(key=lambda e: e.get("date", ""))
    return out


# --- real data sources ------------------------------------------------------------


def _page_rows(pages: list[dict]) -> list[dict]:
    """Reduce Notion pages to the {created_time, content_type, topics} the pure
    logic needs, keeping only real Saves rows (those with a shortcode)."""
    from app import notion_writer

    rows = []
    for page in pages:
        digest = notion_writer.extract_digest_fields(page)
        if not digest["shortcode"]:
            continue
        content_type = (((page.get("properties", {}).get("Content type") or {}).get("select")) or {}).get("name", "")
        rows.append({
            "created_time": page.get("created_time", ""),
            "content_type": content_type,
            "topics": list(digest["topics"]),
        })
    return rows


def collect(now: Optional[datetime] = None) -> dict:
    """Everything the report needs, from one Notion scan plus the vault and the
    runner log. Vault/row comparison reuses the same obsidian_sync helper
    health_watchdog and backlog_status use, rather than a second implementation."""
    from app import notion_writer, obsidian_sync

    now = now or datetime.now(timezone.utc)
    pages = notion_writer.find_saves_pages_since("1970-01-01T00:00:00")
    rows = _page_rows(pages)
    snapshot = partition_capture(rows, now)

    vault_notes = len(obsidian_sync.existing_notes_by_shortcode(Path(obsidian_sync.VAULT_PATH)))

    log_path = Path("daily_runner.log")
    log_lines = log_path.read_text(encoding="utf-8").splitlines() if log_path.exists() else []
    pace = parse_recent_pace(log_lines)

    return {
        "now": now,
        "snapshot": snapshot,
        "notion_rows": len(rows),
        "vault_notes": vault_notes,
        "pace": pace,
    }


def run(dry_run: bool = False, history_path: Path = Path(HISTORY_FILE),
        log_path: Path = Path(LOG_FILE),
        print_fn: Callable[[str], None] = print) -> dict:
    data = collect()
    now = data["now"]
    snapshot = data["snapshot"]
    today = now.strftime("%Y-%m-%d")

    history = load_history(history_path)
    prev_backlog = latest_prior_backlog(history, today)

    processed_today = compute_processed_today(
        prev_backlog, snapshot["captured_today_pending"], snapshot["total_backlog"])
    delta = trend_delta(prev_backlog, snapshot["total_backlog"])
    days = estimate_days_to_clear(snapshot["needs_extraction"], data["pace"])

    summary = format_summary(
        date_str=now.strftime("%b %d"), snapshot=snapshot, prev_backlog=prev_backlog,
        processed_today=processed_today, pace=data["pace"], days_to_clear=days,
        vault_notes=data["vault_notes"], notion_rows=data["notion_rows"])

    print_fn(summary)

    pushed = False
    if should_push(delta):
        if dry_run:
            print_fn("(dry-run: backlog grew — a push WOULD be sent)")
        else:
            from app import alerts
            pushed = alerts.send_push(summary, title="ReelBrain capture report",
                                      tags="chart_with_upwards_trend")
            print_fn(f"Pushed backlog-growth alert: {pushed}")
    else:
        print_fn("(no push — backlog did not grow)")

    entry = {
        "date": today,
        "total_backlog": snapshot["total_backlog"],
        "captured_today": snapshot["captured_today"],
        "processed_today": processed_today,
    }

    if not dry_run:
        new_history = upsert_today(history, entry)
        history_path.write_text(json.dumps(new_history, indent=2) + "\n", encoding="utf-8")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(summary + "\n")

    return {"summary": summary, "entry": entry, "delta": delta,
            "pushed": pushed, "dry_run": dry_run}


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    logging.basicConfig(level=logging.WARNING)

    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true",
                        help="print the report but never push, never write history/log")
    args = parser.parse_args()

    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
