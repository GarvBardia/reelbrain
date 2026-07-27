"""Automated placeholder-recovery worker (Windows Task Scheduler friendly).

LOCAL-ONLY — run from your own machine, never deployed to Render, never called
by the live app. LIVE when run: hits Instagram (home IP), Gemini, and Notion.

WHY: 📷 Photo — manual rows and "No caption or transcript available."
placeholders accumulate because Render's datacenter IP can't fetch them. From
this machine's residential IP they usually CAN be recovered — either as a full
video extraction (many /p/ URLs are actually videos shared with the photo URL
shape) or as a caption-only extraction via the OG-tag bot-UA trick
(scripts/recover_photo_captions.py's proven mechanism, reused directly here).

Selection (straight from Notion, the durable source):
    Status == "📷 Photo — manual"  OR  Title == "No caption or transcript available."

Per row, in order:
    1. yt-dlp media fetch from home IP (scripts/local_fetch.py's logic) — if a
       real video downloads, run the FULL extraction (transcript + everything).
    2. Otherwise the anonymous bot-UA OG caption fetch → caption-only extraction.
On success the SAME Notion row is updated in place (notion_writer.update_page)
and Status flips out of the placeholder state:
    - "⏳ Awaiting DM" if the recovered extraction detected a comment gate
      (flipping a gated row straight to Inbox would silently lose the gate);
    - "📥 Inbox" otherwise.

Attempt tracking is a LOCAL JSON file (never Notion): each failure increments
`attempts`; rows with >= MAX_ATTEMPTS failures are permanent no-caption cases
and are skipped forever after. Success is terminal too. Resumable at any time.

Quota safety:
    - Fetch spacing: local_fetch.enforce_local_fetch_spacing() (>= MIN_FETCH_
      SPACING_SECONDS between burner-session hits, measured across invocations).
    - Gemini spacing: gemini_pipe.MIN_GEMINI_CALL_SPACING_SECONDS is enforced
      inside every Gemini call site automatically.
    - A Gemini 429/RESOURCE_EXHAUSTED mid-run stops the WHOLE run cleanly
      (detected via the reelbrain.gemini logger, since extraction degrades
      instead of raising) — the row's attempt counter is NOT incremented for a
      quota stop, so quota exhaustion never burns a row's 3 chances.

Usage:
    python scripts/recover_placeholders.py --dry-run   # list candidates only
    python scripts/recover_placeholders.py             # actually recover
    python scripts/recover_placeholders.py --limit 2   # first N candidates only

Env vars: NOTION_TOKEN/NOTION_DB_ID, GEMINI_API_KEY, MIN_FETCH_SPACING_SECONDS,
MIN_GEMINI_CALL_SPACING_SECONDS. See WORKER_SETUP.md for Task Scheduler setup.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("reelbrain.recover_placeholders")

PHOTO_MANUAL_LABEL = "📷 Photo — manual"
FAILED_RETRY_LABEL = "⚠️ Failed — retry"
PLACEHOLDER_TITLE = "No caption or transcript available."
# Statuses this worker owns. Failed—retry rows never had a successful fetch at
# all (their Title is still the raw permalink), and Render's datacenter IP is
# exactly why — so the home-IP worker is the right place to retry them
# automatically, rather than leaving them to rot or archiving them.
RECOVERABLE_STATUSES = {PHOTO_MANUAL_LABEL, FAILED_RETRY_LABEL}
MAX_ATTEMPTS = 3
DEFAULT_PROGRESS_FILE = "recover_placeholders_progress.json"
QUOTA_MARKERS = ("429", "RESOURCE_EXHAUSTED")
RECOVERED_NOTE_SUFFIX = "recovered by the placeholder worker from a home-IP fetch"


class _QuotaWatcher(logging.Handler):
    """Extraction degrades instead of raising, so a Gemini 429 is invisible in
    the return value — but gemini_pipe logs every failed call's exception. This
    handler watches the reelbrain.gemini logger for quota markers so the worker
    can tell "degraded because quota" (stop the whole run, don't burn the
    row's attempt) apart from "degraded for some other transient reason"
    (count the attempt, move on)."""

    def __init__(self) -> None:
        super().__init__()
        self.quota_hit = False

    def emit(self, record: logging.LogRecord) -> None:
        text = record.getMessage()
        if record.exc_info and record.exc_info[1] is not None:
            text += " " + str(record.exc_info[1])
        if any(marker in text for marker in QUOTA_MARKERS):
            self.quota_hit = True


def _title_is_bare_permalink(title: str, shortcode: str) -> bool:
    """A Failed—retry row never got a real extraction, so its Title is still
    the raw Instagram permalink the capture came in with."""
    return bool(title) and title.startswith("http") and shortcode in title


def find_placeholder_rows() -> list[dict]:
    """Every row still needing recovery:
      - Photo — manual (yt-dlp can't fetch these; OG caption may still work)
      - ⚠️ Failed — retry (fetch never succeeded at all, usually because
        Render's datacenter IP is blocked — the home-IP path may well work)
      - any row still showing the literal placeholder title, whatever its
        status (a non-photo row whose extraction degraded)
    Broader than recover_photo_captions.find_placeholder_rows, which only
    looked at photo_manual rows."""
    from app import notion_writer

    pages = notion_writer.find_saves_pages_since("1970-01-01T00:00:00")
    rows = []
    for page in pages:
        fields = notion_writer.extract_saves_fields(page)
        if not fields["shortcode"]:
            continue
        if (
            fields["status_label"] in RECOVERABLE_STATUSES
            or fields["title"] == PLACEHOLDER_TITLE
            or _title_is_bare_permalink(fields["title"], fields["shortcode"])
        ):
            rows.append(fields)
    return rows


def _routed_status(extraction) -> str:
    """Where a successfully-recovered row lands. The task says "flip Status to
    Inbox", but a recovered extraction that detected a comment gate must go to
    Awaiting DM instead — flipping it to Inbox would silently lose the gate the
    whole /attach flow exists to fulfill."""
    return "awaiting_dm" if extraction.comment_gate.detected else "done"


def recover_one(fields: dict) -> dict:
    """Full per-row recovery: media-first, OG-caption fallback, in-place Notion
    update. Returns {"status": "recovered"|"no_caption"|"error"|"quota_stop", ...}."""
    from app import fetcher, gemini_pipe, notion_writer, store
    from app.models import ReelData
    from scripts.local_fetch import _try_media_fetch
    from scripts.recover_photo_captions import clean_og_caption, fetch_og_tags_with_bot_ua

    shortcode = fields["shortcode"]
    permalink = fields["permalink"] or f"https://www.instagram.com/p/{shortcode}/"

    watcher = _QuotaWatcher()
    gemini_logger = logging.getLogger("reelbrain.gemini")
    gemini_logger.addHandler(watcher)
    try:
        info, media_note = _try_media_fetch(shortcode, permalink)
        logger.info("%s media attempt: %s", shortcode, media_note)

        if info is not None:
            reel = fetcher._info_to_reel_data(shortcode, permalink, info)
            extraction = gemini_pipe.run_extraction(reel, note=None, taxonomy=store.get_taxonomy())
            path = "full"
        else:
            tags = fetch_og_tags_with_bot_ua(permalink)
            caption, username = clean_og_caption(tags) if tags else (None, None)
            if not caption:
                return {"status": "no_caption", "detail": "no media and no OG caption retrievable"}
            reel = ReelData(
                shortcode=shortcode, permalink=permalink, caption=caption,
                creator_username=username, is_photo_or_carousel=True,
            )
            extraction = gemini_pipe.run_caption_only_extraction(
                caption, creator=username, note=None, taxonomy=store.get_taxonomy()
            )
            path = "caption-only"

        degraded = extraction.content_type == "unknown" and not extraction.topic_tags
        if degraded:
            if watcher.quota_hit:
                return {"status": "quota_stop", "detail": "Gemini 429/quota — stopping the run"}
            return {"status": "error", "detail": "extraction degraded (transient Gemini failure?) — retryable"}
    finally:
        gemini_logger.removeHandler(watcher)

    original_note = (fields["note"] or "").split("⚠️")[0].strip() or None
    note = f"{original_note}\n\n{RECOVERED_NOTE_SUFFIX}" if original_note else RECOVERED_NOTE_SUFFIX
    status = _routed_status(extraction)
    try:
        notion_writer.update_page(fields["page_id"], reel, extraction, status, note=note)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Notion update failed for %s", shortcode)
        return {"status": "error", "detail": f"notion update failed: {exc}"}

    return {
        "status": "recovered", "path": path, "new_status": status,
        "title": extraction.main_point[:80], "topics": extraction.topic_tags,
    }


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


def run_worker(
    rows: list[dict],
    progress_file: str,
    dry_run: bool = False,
    recover_fn: Callable[[dict], dict] = recover_one,
    print_fn: Callable[[str], None] = print,
) -> dict:
    """The resumable loop. No explicit inter-row sleep needed: fetch spacing is
    enforced at the fetch point (across invocations) and Gemini spacing at each
    Gemini call site."""
    progress = load_progress(progress_file)

    recovered = errors = skipped = 0
    quota_stopped = False
    for i, fields in enumerate(rows):
        shortcode = fields["shortcode"]
        entry = progress.get(shortcode, {})
        if entry.get("status") == "recovered":
            skipped += 1
            continue
        if entry.get("attempts", 0) >= MAX_ATTEMPTS:
            skipped += 1
            print_fn(f"[{i + 1}/{len(rows)}] {shortcode} -> skipped (permanent: {MAX_ATTEMPTS} failed attempts)")
            continue

        if dry_run:
            print_fn(f"[dry-run] would attempt: {shortcode}  {fields['permalink']}  "
                     f"(prior attempts: {entry.get('attempts', 0)})")
            continue

        result = recover_fn(fields)
        status = result["status"]

        if status == "quota_stop":
            # NOT counted as an attempt — quota exhaustion is not the row's fault.
            print_fn(f"[{i + 1}/{len(rows)}] {shortcode} -> QUOTA STOP ({result.get('detail')}); "
                     f"stopping cleanly, resume on the next scheduled run")
            quota_stopped = True
            break

        attempts = entry.get("attempts", 0) + (0 if status == "recovered" else 1)
        progress[shortcode] = {
            **result,
            "attempts": attempts,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        save_progress(progress_file, progress)

        if status == "recovered":
            recovered += 1
            print_fn(f"[{i + 1}/{len(rows)}] {shortcode} -> RECOVERED via {result.get('path')} "
                     f"-> {result.get('new_status')}: {result.get('title', '')}")
        else:
            errors += 1
            print_fn(f"[{i + 1}/{len(rows)}] {shortcode} -> {status.upper()} "
                     f"(attempt {attempts}/{MAX_ATTEMPTS}): {result.get('detail')}")

    summary = {
        "recovered": recovered, "errors": errors, "skipped": skipped,
        "quota_stopped": quota_stopped, "total_rows": len(rows),
    }
    print_fn(f"\ndone: {recovered} recovered, {errors} failures, {skipped} skipped, "
             f"quota_stopped={quota_stopped}, out of {len(rows)} candidate rows")
    return summary


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--progress-file", default=DEFAULT_PROGRESS_FILE)
    parser.add_argument("--dry-run", action="store_true", help="list candidates, fetch nothing")
    parser.add_argument("--limit", type=int, default=None, help="attempt at most N rows this run")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    from app import store
    store.init_db()

    print("querying Notion for placeholder rows...")
    rows = find_placeholder_rows()
    print(f"found {len(rows)} candidate row(s)")
    if args.limit is not None:
        rows = rows[:args.limit]

    run_worker(rows=rows, progress_file=args.progress_file, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
