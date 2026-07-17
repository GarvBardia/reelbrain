"""Bulk-import a list of Instagram reel URLs into the deployed ReelBrain /capture
endpoint, respecting client-side spacing and the server's daily fetch cap.

IMPORTANT — a real discovery, not an assumption baked into this script's design:
/capture responds 202 "processing" IMMEDIATELY and does the actual yt-dlp fetch in
a background task (see app/main.py::run_pipeline). The server's MAX_FETCHES_PER_DAY
cap is enforced INSIDE that background task (app/fetcher.py::_enforce_rate_discipline),
NOT as a distinct HTTP-level rejection — there is no special status code or error
body for "daily cap reached". A submission that would exceed the cap still gets a
plain 202 back; only later (invisibly to this script) does it become a Notion page
with status "Failed - retry" and a note explaining the cap was hit.

So this script CANNOT detect the cap from the server's response. Instead it enforces
the same cap client-side: it counts how many non-duplicate submissions IT has made
today (from the progress file) and stops before submitting more than
MAX_FETCHES_PER_DAY, printing a clear "resume tomorrow" message. This is an
approximation, not a guarantee — other captures (the iOS Shortcut, /retry, another
bulk-import run) consume the same server-side daily budget and this script has no
way to see those. Treat the count as a ceiling on THIS script's own contribution.

Usage:
    python scripts/bulk_import.py urls.txt
    python scripts/bulk_import.py urls.txt --progress-file my_progress.json --dry-run

Env vars:
    REELBRAIN_URL               deployed base URL (default: https://reelbrain.onrender.com)
    CAPTURE_SECRET               required — same secret /capture checks
    MIN_FETCH_SPACING_SECONDS    client-side delay between requests, seconds (default 20)
    MAX_FETCHES_PER_DAY          client-side self-throttle ceiling (default 25)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

# Make `python scripts/bulk_import.py` work as well as `python -m scripts.bulk_import`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("reelbrain.bulk_import")

DEFAULT_BASE_URL = "https://reelbrain.onrender.com"
JITTER_MAX_SECONDS = 5.0
REQUEST_TIMEOUT_SECONDS = 60.0  # generous: Render free-tier cold start is ~30-50s

# Statuses that mean "the server has already recorded this URL" — skipped on rerun.
# "error"/"rate_limited" are deliberately NOT terminal, so a transient failure
# (network blip, cold start, a temporary rate limit) gets retried automatically
# on the next run rather than silently blacklisting the URL forever.
TERMINAL_STATUSES = {"processing", "duplicate"}


def read_urls(path: str) -> list[str]:
    """One URL per line; blank lines and #-comments ignored."""
    urls = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                urls.append(line)
    return urls


def load_progress(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_progress(path: str, progress: dict) -> None:
    """Atomic write (temp file + rename) so a crash mid-save can't corrupt the
    progress file a multi-day run depends on."""
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(progress, f, indent=2, sort_keys=True)
    os.replace(tmp_path, path)


def today_str() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def count_submitted_today(progress: dict) -> int:
    """Non-duplicate submissions recorded today. Duplicates don't consume a
    server-side fetch (dedupe short-circuits before any fetch attempt), so
    they're excluded from the self-throttle count."""
    today = today_str()
    return sum(
        1 for entry in progress.values()
        if entry.get("status") == "processing" and entry.get("date") == today
    )


def submit_capture(base_url: str, secret: str, url: str,
                    timeout: float = REQUEST_TIMEOUT_SECONDS) -> dict:
    """POST to /capture. Returns {"status", "http_status", "detail"} — never raises;
    network/transport errors are captured into the same shape as HTTP errors.
    status is one of: processing, duplicate, error, auth_error, rate_limited.
    """
    import httpx

    try:
        response = httpx.post(
            f"{base_url.rstrip('/')}/capture",
            json={"url": url, "note": None, "secret": secret},
            timeout=timeout,
        )
    except httpx.RequestError as exc:
        return {"status": "error", "http_status": None, "detail": f"network error: {exc}"}

    try:
        body = response.json()
    except ValueError:
        body = {}

    if response.status_code == 401:
        return {"status": "auth_error", "http_status": 401, "detail": body.get("detail", "invalid secret")}
    if response.status_code == 429:
        return {"status": "rate_limited", "http_status": 429, "detail": body.get("detail", "rate limited")}
    if response.status_code == 400:
        return {"status": "error", "http_status": 400, "detail": body.get("detail", "bad request")}
    if response.status_code == 200 and body.get("status") == "duplicate":
        return {"status": "duplicate", "http_status": 200, "detail": body}
    if response.status_code == 202:
        return {"status": "processing", "http_status": 202, "detail": body}

    return {"status": "error", "http_status": response.status_code, "detail": body or response.text}


def run_bulk_import(
    urls_file: str,
    progress_file: str,
    base_url: str,
    secret: str,
    spacing_seconds: float,
    max_per_day: int,
    dry_run: bool = False,
    submit_fn: Callable[[str, str, str], dict] = submit_capture,
    sleep_fn: Callable[[float], None] = time.sleep,
    jitter_fn: Callable[[], float] = lambda: random.uniform(0, JITTER_MAX_SECONDS),
    print_fn: Callable[[str], None] = print,
) -> dict:
    """Drives the whole import. Pure enough to unit-test: submit_fn/sleep_fn/
    jitter_fn/print_fn are all injectable so tests never touch the network,
    the clock, or stdout noise."""
    urls = read_urls(urls_file)
    progress = load_progress(progress_file)

    captured = duplicates = errors = skipped = 0
    stopped_early = False
    submitted_today = count_submitted_today(progress)

    for i, url in enumerate(urls):
        existing = progress.get(url)
        if existing and existing.get("status") in TERMINAL_STATUSES:
            skipped += 1
            continue

        if submitted_today >= max_per_day:
            print_fn(f"daily cap reached ({submitted_today}/{max_per_day}) — resume tomorrow")
            stopped_early = True
            break

        if dry_run:
            print_fn(f"[dry-run] would submit: {url}")
            continue

        result = submit_fn(base_url, secret, url)
        status = result["status"]

        if status == "auth_error":
            print_fn(f"AUTH ERROR: {result['detail']} — check CAPTURE_SECRET. Stopping.")
            stopped_early = True
            break

        progress[url] = {
            "status": status,
            "http_status": result.get("http_status"),
            "detail": result.get("detail"),
            "date": today_str(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        save_progress(progress_file, progress)

        if status == "processing":
            captured += 1
            submitted_today += 1
        elif status == "duplicate":
            duplicates += 1
        else:
            errors += 1
            if status == "rate_limited":
                print_fn(f"rate-limited on {url} — pausing longer than usual before continuing.")
                sleep_fn(spacing_seconds * 2)

        print_fn(
            f"[{i + 1}/{len(urls)}] {url} -> {status}  "
            f"(captured={captured} duplicates={duplicates} errors={errors})"
        )

        is_last = i == len(urls) - 1
        if not is_last and status != "duplicate":
            # Only a fresh submission actually triggers a server-side fetch attempt —
            # a duplicate short-circuits with no fetch, so there's nothing to space out.
            sleep_fn(spacing_seconds + jitter_fn())

    summary = {
        "captured": captured,
        "duplicates": duplicates,
        "errors": errors,
        "skipped": skipped,
        "total_urls": len(urls),
        "stopped_early": stopped_early,
    }
    print_fn(
        f"\ndone: {captured} captured, {duplicates} duplicates, {errors} errors, "
        f"{skipped} already-done skipped, out of {len(urls)} URLs"
        + (" (stopped early)" if stopped_early else "")
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("urls_file", help="text file, one Instagram reel URL per line")
    parser.add_argument("--progress-file", default="bulk_import_progress.json")
    parser.add_argument("--base-url", default=os.environ.get("REELBRAIN_URL", DEFAULT_BASE_URL))
    parser.add_argument("--dry-run", action="store_true", help="list what would be submitted, no requests sent")
    args = parser.parse_args()

    secret = os.environ.get("CAPTURE_SECRET", "").strip()
    if not secret and not args.dry_run:
        sys.exit("CAPTURE_SECRET is not set (check your .env) — required to authenticate against /capture")

    spacing = float(os.environ.get("MIN_FETCH_SPACING_SECONDS", "20"))
    max_per_day = int(os.environ.get("MAX_FETCHES_PER_DAY", "25"))

    run_bulk_import(
        urls_file=args.urls_file,
        progress_file=args.progress_file,
        base_url=args.base_url,
        secret=secret,
        spacing_seconds=spacing,
        max_per_day=max_per_day,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
