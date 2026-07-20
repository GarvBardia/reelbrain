"""Batch-retry rows whose Title is still the raw caption (pre-FIX-1 captures).

FIX 1 (see PROGRESS.md) proved live that /retry now turns a raw-caption row into
a real extraction — synthesized title, Topics, honest value_score/Priority
(before/after on DZSFkNppVW_). This script finds every remaining row that would
benefit and retries them, one command.

Selection (queried live from Notion, the durable source):
  - Title looks like a raw caption dump — heuristic per the batch request:
    it starts with "comment ", or it contains the row's own gate keyword in the
    literal "comment <keyword>" pattern (quoted or not, any case, curly quotes
    included).
  - Status is NOT "📷 Photo — manual": confirmed live that those rows have no
    caption at all (Instagram login-walls the OG scrape from Render's IP), so a
    retry can never improve them — retrying would just burn fetch budget.

Mechanics mirror scripts/bulk_import.py deliberately: same progress-file
re-runnability (a retry that got 202 isn't re-submitted next run), same
client-side MIN_FETCH_SPACING_SECONDS spacing + jitter, same MAX_FETCHES_PER_DAY
self-throttle ceiling (each /retry triggers a real server-side fetch, so it
consumes the same daily budget as a capture — and just like bulk_import, the
server gives no cap-specific response; the count here is a ceiling on THIS
script's own contribution only).

Note: /retry requires no secret (unlike /capture) — it's rate-limit-guarded
only, and the shortcode must already exist. CAPTURE_SECRET is therefore not
needed here.

Usage:
    python scripts/batch_retry_stale_titles.py --dry-run   # list matches, send nothing
    python scripts/batch_retry_stale_titles.py             # actually retry them

Env vars:
    REELBRAIN_URL               deployed base URL (default: https://reelbrain.onrender.com)
    MIN_FETCH_SPACING_SECONDS    client-side delay between retries, seconds (default 20)
    MAX_FETCHES_PER_DAY          client-side self-throttle ceiling (default 25)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

# Make `python scripts/batch_retry_stale_titles.py` work as well as -m form.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("reelbrain.batch_retry")

DEFAULT_BASE_URL = "https://reelbrain.onrender.com"
JITTER_MAX_SECONDS = 5.0
REQUEST_TIMEOUT_SECONDS = 60.0  # Render free-tier cold start is ~30-50s

PHOTO_MANUAL_LABEL = "📷 Photo — manual"

# A retry that the server accepted (202) is done from this script's viewpoint —
# whether the background pipeline then improved the row is checked in Notion,
# not re-submitted. Errors/404s stay retryable on the next run.
TERMINAL_STATUSES = {"processing"}

_QUOTE_CHARS = "\"'“”‘’"


def looks_like_raw_caption_title(title: str, gate_keyword: str | None) -> bool:
    """The batch request's heuristic for 'Title is still the raw caption':
    the title starts with 'comment ', or contains the row's own gate keyword in
    a literal 'comment <keyword>' pattern (quotes optional, any case). A
    synthesized title (FIX 1 output) describes the content instead of
    parroting the call-to-action, so it doesn't trip either check."""
    if not title:
        return False
    if title.strip().lower().startswith("comment "):
        return True
    if gate_keyword:
        pattern = re.compile(
            r"comment\s+[" + _QUOTE_CHARS + r"]?" + re.escape(gate_keyword) + r"[" + _QUOTE_CHARS + r"]?",
            re.IGNORECASE,
        )
        if pattern.search(title):
            return True
    return False


def find_stale_title_candidates() -> list[dict]:
    """Every Saves row matching the heuristic, straight from Notion. Reuses
    find_saves_pages_since with an epoch cutoff (= all pages, paginated) and
    the existing field extractors rather than a new query path."""
    from app import notion_writer

    pages = notion_writer.find_saves_pages_since("1970-01-01T00:00:00")
    candidates = []
    for page in pages:
        fields = notion_writer.extract_saves_fields(page)
        if not fields["shortcode"]:
            continue
        if fields["status_label"] == PHOTO_MANUAL_LABEL:
            continue  # no caption exists for these — a retry can never improve them
        if looks_like_raw_caption_title(fields["title"], fields["gate_keyword"]):
            candidates.append({
                "shortcode": fields["shortcode"],
                "title": fields["title"][:80].replace("\n", " "),
                "status": fields["status_label"],
            })
    return candidates


def load_progress(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_progress(path: str, progress: dict) -> None:
    """Atomic write (temp + rename), same as bulk_import."""
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(progress, f, indent=2, sort_keys=True)
    os.replace(tmp_path, path)


def today_str() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def count_submitted_today(progress: dict) -> int:
    today = today_str()
    return sum(
        1 for entry in progress.values()
        if entry.get("status") == "processing" and entry.get("date") == today
    )


def submit_retry(base_url: str, shortcode: str,
                  timeout: float = REQUEST_TIMEOUT_SECONDS) -> dict:
    """POST /retry/{shortcode}. Same never-raises contract as bulk_import's
    submit_capture: network errors come back in the same dict shape."""
    import httpx

    try:
        response = httpx.post(
            f"{base_url.rstrip('/')}/retry/{shortcode}",
            timeout=timeout,
        )
    except httpx.RequestError as exc:
        return {"status": "error", "http_status": None, "detail": f"network error: {exc}"}

    try:
        body = response.json()
    except ValueError:
        body = {}

    if response.status_code == 202:
        return {"status": "processing", "http_status": 202, "detail": body}
    if response.status_code == 404:
        return {"status": "error", "http_status": 404, "detail": body.get("detail", "unknown shortcode")}
    if response.status_code == 429:
        return {"status": "rate_limited", "http_status": 429, "detail": body.get("detail", "rate limited")}
    return {"status": "error", "http_status": response.status_code, "detail": body or response.text}


def run_batch_retry(
    candidates: list[dict],
    progress_file: str,
    base_url: str,
    spacing_seconds: float,
    max_per_day: int,
    dry_run: bool = False,
    submit_fn: Callable[[str, str], dict] = submit_retry,
    sleep_fn: Callable[[float], None] = time.sleep,
    jitter_fn: Callable[[], float] = lambda: random.uniform(0, JITTER_MAX_SECONDS),
    print_fn: Callable[[str], None] = print,
) -> dict:
    """Same injectable-everything shape as bulk_import.run_bulk_import so tests
    never touch the network or the clock."""
    progress = load_progress(progress_file)

    retried = errors = skipped = 0
    stopped_early = False
    submitted_today = count_submitted_today(progress)

    for i, candidate in enumerate(candidates):
        shortcode = candidate["shortcode"]
        existing = progress.get(shortcode)
        if existing and existing.get("status") in TERMINAL_STATUSES:
            skipped += 1
            continue

        if submitted_today >= max_per_day:
            print_fn(f"daily cap reached ({submitted_today}/{max_per_day}) — resume tomorrow")
            stopped_early = True
            break

        if dry_run:
            print_fn(f"[dry-run] would retry: {shortcode}  [{candidate['status']}]  {candidate['title']}")
            continue

        result = submit_fn(base_url, shortcode)
        status = result["status"]

        progress[shortcode] = {
            "status": status,
            "http_status": result.get("http_status"),
            "detail": result.get("detail"),
            "date": today_str(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        save_progress(progress_file, progress)

        if status == "processing":
            retried += 1
            submitted_today += 1
        else:
            errors += 1
            if status == "rate_limited":
                print_fn(f"rate-limited on {shortcode} — pausing longer before continuing.")
                sleep_fn(spacing_seconds * 2)

        print_fn(
            f"[{i + 1}/{len(candidates)}] {shortcode} -> {status}  "
            f"(retried={retried} errors={errors})"
        )

        if i != len(candidates) - 1:
            # Every accepted retry triggers a real server-side fetch — space them all.
            sleep_fn(spacing_seconds + jitter_fn())

    summary = {
        "retried": retried,
        "errors": errors,
        "skipped": skipped,
        "total_candidates": len(candidates),
        "stopped_early": stopped_early,
    }
    print_fn(
        f"\ndone: {retried} retried, {errors} errors, {skipped} already-done skipped, "
        f"out of {len(candidates)} candidates"
        + (" (stopped early)" if stopped_early else "")
    )
    return summary


def main() -> None:
    # Windows consoles default to cp1252, which can't print the emoji in Notion
    # status labels (⏳/📥) — reconfigure rather than crash mid-listing.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--progress-file", default="batch_retry_progress.json")
    parser.add_argument("--base-url", default=os.environ.get("REELBRAIN_URL", DEFAULT_BASE_URL))
    parser.add_argument("--dry-run", action="store_true", help="list matching shortcodes, send nothing")
    args = parser.parse_args()

    print("querying Notion for raw-caption-title rows...")
    candidates = find_stale_title_candidates()
    print(f"found {len(candidates)} candidate(s)\n")

    spacing = float(os.environ.get("MIN_FETCH_SPACING_SECONDS", "20"))
    max_per_day = int(os.environ.get("MAX_FETCHES_PER_DAY", "25"))

    run_batch_retry(
        candidates=candidates,
        progress_file=args.progress_file,
        base_url=args.base_url,
        spacing_seconds=spacing,
        max_per_day=max_per_day,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
