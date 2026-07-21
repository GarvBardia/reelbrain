"""Bulk-ingest a URL list via the LOCAL (home-IP) full-video path proven in
Stage 1. LOCAL-ONLY — run from your machine, never deployed.

Per-URL flow:
  1. De-dupe against Notion — if the shortcode already has a Saves row, skip it
     entirely (no fetch, no burner-session hit).
  2. Otherwise fetch + extract via scripts/local_fetch.probe_one(write=True):
     yt-dlp media download (home IP + burner cookies) -> full extraction ->
     real Notion page (Title/Topics/Value/Priority/gate + body). Spacing
     (>=MIN_FETCH_SPACING_SECONDS) is enforced inside that fetch, shared with
     the server's discipline.
  3. Gemini free-tier 503s degrade an extraction (media downloads fine, but the
     analysis fails). probe_one never WRITES a degraded row — instead this
     script queues it and retries ONCE at the end after a delay, since 503 is
     transient capacity, not a real failure. Whatever still degrades after the
     retry is reported, not silently dropped.

Progress-tracked + resumable: a row that got written (or was a duplicate) is
terminal and skipped on re-run; degraded/blocked/error rows stay retryable.

NOTE (honest limitation): this writes via notion_writer.create_page, the same
page builder a normal capture uses — so Title/Topics/Value/Priority/gate/body
are fully present. It does NOT run the embeddings/near-dup/related-saves step
or the Creator relation that the server's run_pipeline adds (those need the
deployed sqlite-vec + Creators DB flow). The core knowledge is complete;
Related-links and creator rollups for these specific rows are the tradeoff.

Usage:
    python scripts/bulk_ingest_local.py --from-file urls.txt --dry-run
    python scripts/bulk_ingest_local.py --from-file urls.txt

Env vars:
    NOTION_TOKEN / NOTION_DB_ID / GEMINI_API_KEY   required
    MIN_FETCH_SPACING_SECONDS                       fetch spacing (default 20)
    MAX_FETCHES_PER_DAY                             raise for a big one-off batch
    GEMINI_503_RETRY_DELAY_SECONDS                  delay before the retry pass (default 60)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from scripts.local_fetch import _read_urls, probe_one

TERMINAL_OUTCOMES = {"duplicate"}  # "extracted+written" handled explicitly below


def load_progress(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_progress(path: str, progress: dict) -> None:
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(progress, f, indent=2, sort_keys=True)
    os.replace(tmp_path, path)


def existing_notion_shortcodes() -> set[str]:
    """Every shortcode already in the Saves DB — one paginated query, so
    de-dupe is a set lookup rather than 40 per-URL Notion queries."""
    from app import notion_writer

    pages = notion_writer.find_saves_pages_since("1970-01-01T00:00:00")
    out = set()
    for page in pages:
        sc = notion_writer.extract_saves_fields(page)["shortcode"]
        if sc:
            out.add(sc)
    return out


def _is_written(entry: dict) -> bool:
    return entry.get("outcome") == "duplicate" or (
        entry.get("outcome") == "extracted" and entry.get("written") is True
    )


def run_bulk_ingest(
    urls: list[str],
    progress_file: str,
    dry_run: bool = False,
    probe_fn: Callable[..., dict] = probe_one,
    sleep_fn: Callable[[float], None] = time.sleep,
    print_fn: Callable[[str], None] = print,
    existing_fn: Callable[[], set[str]] = existing_notion_shortcodes,
    retry_delay: float = 60.0,
) -> dict:
    from app import fetcher

    progress = load_progress(progress_file)
    existing = existing_fn()

    written = duplicates = degraded = blocked = errors = skipped = 0
    retry_queue: list[str] = []

    def _record(url: str, result: dict) -> None:
        progress[url] = {**result, "timestamp": datetime.now(timezone.utc).isoformat()}
        if not dry_run:
            save_progress(progress_file, progress)

    for i, url in enumerate(urls):
        try:
            shortcode = fetcher.normalize_url(url)
        except ValueError:
            continue  # header line / junk

        prior = progress.get(url)
        if prior and _is_written(prior):
            skipped += 1
            continue

        if shortcode in existing:
            duplicates += 1
            _record(url, {"url": url, "shortcode": shortcode, "outcome": "duplicate"})
            print_fn(f"[{i + 1}/{len(urls)}] {shortcode} -> already in Notion, skipped")
            continue

        if dry_run:
            print_fn(f"[dry-run] would ingest: {shortcode}  {url}")
            continue

        result = probe_fn(url, write=True)
        _record(url, result)
        outcome = result["outcome"]

        if outcome == "extracted" and result.get("written"):
            written += 1
        elif outcome == "degraded":
            degraded += 1
            retry_queue.append(url)
        elif outcome == "blocked":
            blocked += 1
        else:
            errors += 1

    # --- 503 retry pass: degraded rows are transient Gemini capacity, not failures ---
    still_degraded: list[str] = []
    if retry_queue and not dry_run:
        print_fn(f"\n{'='*70}\nRETRY PASS: {len(retry_queue)} degraded (Gemini 503) — "
                 f"waiting {retry_delay:.0f}s then retrying once each")
        sleep_fn(retry_delay)
        for url in retry_queue:
            result = probe_fn(url, write=True)
            _record(url, result)
            if result["outcome"] == "extracted" and result.get("written"):
                written += 1
                degraded -= 1
                print_fn(f"  retry OK: {result.get('shortcode')}")
            else:
                still_degraded.append(result.get("shortcode", url))
                print_fn(f"  retry STILL FAILING: {result.get('shortcode', url)}")

    summary = {
        "written": written,
        "duplicates": duplicates,
        "degraded_remaining": len(still_degraded),
        "still_degraded": still_degraded,
        "blocked": blocked,
        "errors": errors,
        "skipped": skipped,
        "total_urls": len(urls),
    }
    print_fn(
        f"\n{'='*70}\ndone: {written} written, {duplicates} duplicates, "
        f"{len(still_degraded)} still degraded after retry, {blocked} blocked, "
        f"{errors} errors, {skipped} already-done skipped, of {len(urls)} URLs"
    )
    if still_degraded:
        print_fn(f"STILL DEGRADED (re-run later — Gemini was overloaded): {still_degraded}")
    return summary


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from-file", default="urls.txt", help="URL list file (default urls.txt)")
    parser.add_argument("--progress-file", default="bulk_ingest_progress.json")
    parser.add_argument("--dry-run", action="store_true", help="list what would be ingested, fetch nothing")
    args = parser.parse_args()

    from app import store
    store.init_db()

    urls = _read_urls(args.from_file)
    print(f"{len(urls)} URLs from {args.from_file}\n")

    retry_delay = float(os.environ.get("GEMINI_503_RETRY_DELAY_SECONDS", "60"))
    run_bulk_ingest(urls, args.progress_file, dry_run=args.dry_run, retry_delay=retry_delay)


if __name__ == "__main__":
    main()
