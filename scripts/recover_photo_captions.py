"""Recover captions for 📷 Photo — manual rows via a LOCAL (residential) IP.

LOCAL-ONLY — run from your own machine, never deployed to Render, never called
by the live app. LIVE when run: hits Instagram (anonymous OG fetch), Gemini
(caption-only extraction), and Notion (row update) for real.

WHY THIS EXISTS (viability proven live, see PROGRESS.md): photo/carousel posts
can never be fetched by yt-dlp (video-only), and Instagram's OG caption tags
are blocked from Render's datacenter IP. From a residential IP they ARE served
— but only to link-preview bot user-agents (facebookexternalhit/Twitterbot);
the browser UA gets a tag-less page even from home. So this script fetches
each placeholder row's OG tags with the bot UA, and — only when a real caption
comes back — runs the exact same caption-only extraction a normal capture
uses and updates the Notion row (synthesized title, Topics, Value score,
Priority, gate fields, rebuilt body with the raw caption).

Safety notes:
  - The OG fetch is ANONYMOUS — no cookies, no login, nothing tied to the
    burner account. Same trust boundary as the server's own OG fallback.
  - Status stays "📷 Photo — manual" (photo posts can never have a transcript;
    that honesty rule is unchanged) — only the CONTENT quality improves.
  - Idempotent two ways: a progress file (recovered rows aren't re-submitted),
    and a live re-check (a row whose Title is no longer the placeholder is
    skipped even with no progress entry).

Usage:
    python scripts/recover_photo_captions.py --dry-run   # list candidate rows, no network beyond Notion
    python scripts/recover_photo_captions.py             # actually recover

Env vars:
    NOTION_TOKEN / NOTION_DB_ID   required (same as the app)
    GEMINI_API_KEY                required for the extraction step
    OG_SCRAPE_SPACING_SECONDS     delay between Instagram fetches (default 10)
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
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("reelbrain.recover_photo_captions")

PHOTO_MANUAL_LABEL = "📷 Photo — manual"
PLACEHOLDER_TITLE = "No caption or transcript available."
# The finding that makes this script work: IG serves OG tags to residential IPs
# only for link-preview bot UAs — the browser UA gets a tag-less page.
BOT_USER_AGENT = "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)"
OG_TIMEOUT_SECONDS = 15.0
JITTER_MAX_SECONDS = 3.0
RECOVERED_NOTE_SUFFIX = (
    "⚠️ photo/carousel post — caption recovered via local OG fetch; "
    "no video transcript exists"
)

# og:title: 'Chase AI on Instagram: "the actual caption"'
_OG_TITLE_CAPTION_RE = re.compile(r'on Instagram:\s*[\"“](.+)[\"”]\s*$', re.DOTALL)
# og:description: '1,382 likes, 483 comments - chase.h.ai on July 9, 2026: "caption"'
_OG_DESC_STATS_PREFIX_RE = re.compile(
    r'^[\d,\.KMkm]+\s+likes?,\s*[\d,\.KMkm]+\s+comments?\s*-\s*(\S+)\s+on\s+[^:]+:\s*', re.DOTALL
)


def clean_og_caption(tags: dict[str, str]) -> tuple[Optional[str], Optional[str]]:
    """(caption, username) from OG tags, stripped of Instagram's boilerplate.
    Prefers the quoted caption inside og:title; falls back to og:description
    with its '1,382 likes, 483 comments - user on date:' prefix removed.
    Returns (None, ...) when there's no real caption to extract."""
    username = None
    desc = tags.get("og:description") or ""
    prefix_match = _OG_DESC_STATS_PREFIX_RE.match(desc)
    if prefix_match:
        username = prefix_match.group(1)

    title = tags.get("og:title") or ""
    title_match = _OG_TITLE_CAPTION_RE.search(title)
    if title_match and title_match.group(1).strip():
        return title_match.group(1).strip(), username

    if prefix_match:
        remainder = desc[prefix_match.end():].strip().strip('"“”').strip()
        if remainder:
            return remainder, username

    return None, username


def fetch_og_tags_with_bot_ua(permalink: str) -> Optional[dict[str, str]]:
    """One anonymous GET with the bot UA. Returns parsed og:* tags or None.
    Never raises — a blocked/failed fetch is an expected outcome, not an error."""
    import httpx

    from app import fetcher

    try:
        response = httpx.get(
            permalink,
            headers={"User-Agent": BOT_USER_AGENT},
            timeout=OG_TIMEOUT_SECONDS,
            follow_redirects=True,
        )
        response.raise_for_status()
        tags = fetcher._parse_og_tags(response.text)
    except Exception:  # noqa: BLE001
        logger.warning("OG fetch failed for %s", permalink, exc_info=True)
        return None
    return tags or None


def find_placeholder_rows() -> list[dict]:
    """Every Photo — manual row that still needs recovery, straight from
    Notion: placeholder title, OR a title but no real topics (a row written
    before the degraded-extraction guard existed — caption-as-title with no
    analysis). Successfully recovered rows have topics, so they're excluded —
    that's what makes re-runs safe even without the progress file."""
    from app import notion_writer

    pages = notion_writer.find_saves_pages_since("1970-01-01T00:00:00")
    rows = []
    for page in pages:
        fields = notion_writer.extract_saves_fields(page)
        if not fields["shortcode"]:
            continue
        if fields["status_label"] != PHOTO_MANUAL_LABEL:
            continue
        topics = [
            t for t in notion_writer.extract_digest_fields(page)["topics"]
            if t != "near-duplicate"  # auto-tag, not real analysis
        ]
        if fields["title"] != PLACEHOLDER_TITLE and topics:
            continue  # already recovered for real
        rows.append(fields)
    return rows


def recover_row(fields: dict) -> dict:
    """The full per-row recovery: OG fetch -> caption-only extraction -> Notion
    update. Returns {"status": "recovered"|"no_caption"|"error", ...}."""
    from app import gemini_pipe, notion_writer, store
    from app.models import ReelData

    permalink = fields["permalink"]
    tags = fetch_og_tags_with_bot_ua(permalink)
    if not tags:
        return {"status": "no_caption", "detail": "OG fetch returned no tags"}
    caption, username = clean_og_caption(tags)
    if not caption:
        return {"status": "no_caption", "detail": "OG tags present but no caption text"}

    extraction = gemini_pipe.run_caption_only_extraction(
        caption, creator=username, note=None, taxonomy=store.get_taxonomy()
    )
    # A degraded extraction (caption parroted back, no analysis) here means the
    # Gemini call failed transiently (e.g. 503) even though the caption is long
    # enough — discovered live on the first row. Don't write it and don't mark
    # the row done: leave it retryable so the next run gets a real extraction,
    # instead of permanently burning the row with caption-as-title.
    if extraction.content_type == "unknown" and not extraction.topic_tags:
        return {
            "status": "error",
            "detail": "extraction degraded (Gemini likely unavailable) — will retry next run",
        }

    # Preserve the user's own note if any; the old "no auto-transcript" warning
    # line is replaced by the recovered-caption marker.
    original_note = (fields["note"] or "").split("⚠️")[0].strip() or None
    note = f"{original_note}\n\n{RECOVERED_NOTE_SUFFIX}" if original_note else RECOVERED_NOTE_SUFFIX

    reel = ReelData(
        shortcode=fields["shortcode"],
        permalink=permalink,
        caption=caption,
        creator_username=username,
        is_photo_or_carousel=True,
    )
    try:
        # Full-fidelity update through the same writer a normal capture uses:
        # properties (Title/Topics/Value/Priority/gate fields) AND rebuilt body
        # (main point callout + Raw caption toggle). Status stays photo_manual.
        notion_writer.update_page(
            fields["page_id"], reel, extraction, "photo_manual", note=note,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Notion update failed for %s", fields["shortcode"])
        return {"status": "error", "detail": f"notion update failed: {exc}"}

    return {
        "status": "recovered",
        "title": extraction.main_point[:80],
        "topics": extraction.topic_tags,
        "priority": extraction.priority,
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


def run_recovery(
    rows: list[dict],
    progress_file: str,
    spacing_seconds: float,
    dry_run: bool = False,
    recover_fn: Callable[[dict], dict] = recover_row,
    sleep_fn: Callable[[float], None] = time.sleep,
    jitter_fn: Callable[[], float] = lambda: random.uniform(0, JITTER_MAX_SECONDS),
    print_fn: Callable[[str], None] = print,
) -> dict:
    """bulk_import-style loop: injectable recover/sleep/jitter/print so tests
    never touch the network or the clock."""
    progress = load_progress(progress_file)

    recovered = no_caption = errors = skipped = 0
    for i, fields in enumerate(rows):
        shortcode = fields["shortcode"]
        existing = progress.get(shortcode)
        if existing and existing.get("status") == "recovered":
            skipped += 1
            continue

        if dry_run:
            print_fn(f"[dry-run] would attempt: {shortcode}  {fields['permalink']}")
            continue

        result = recover_fn(fields)
        status = result["status"]
        progress[shortcode] = {
            **result,
            "date": datetime.now(timezone.utc).date().isoformat(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        save_progress(progress_file, progress)

        if status == "recovered":
            recovered += 1
            print_fn(f"[{i + 1}/{len(rows)}] {shortcode} -> RECOVERED: {result.get('title', '')}")
        elif status == "no_caption":
            no_caption += 1
            print_fn(f"[{i + 1}/{len(rows)}] {shortcode} -> no caption retrievable ({result.get('detail')})")
        else:
            errors += 1
            print_fn(f"[{i + 1}/{len(rows)}] {shortcode} -> ERROR: {result.get('detail')}")

        if i != len(rows) - 1:
            sleep_fn(spacing_seconds + jitter_fn())

    summary = {
        "recovered": recovered,
        "no_caption": no_caption,
        "errors": errors,
        "skipped": skipped,
        "total_rows": len(rows),
    }
    print_fn(
        f"\ndone: {recovered} recovered, {no_caption} without retrievable captions, "
        f"{errors} errors, {skipped} already-recovered skipped, out of {len(rows)} rows"
    )
    return summary


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--progress-file", default="recover_photo_captions_progress.json")
    parser.add_argument("--dry-run", action="store_true", help="list candidate rows, fetch nothing from Instagram")
    args = parser.parse_args()

    from app import store

    store.init_db()  # taxonomy read needs the local DB to at least exist

    print("querying Notion for placeholder photo/carousel rows...")
    rows = find_placeholder_rows()
    print(f"found {len(rows)} placeholder row(s)\n")

    spacing = float(os.environ.get("OG_SCRAPE_SPACING_SECONDS", "10"))
    run_recovery(
        rows=rows,
        progress_file=args.progress_file,
        spacing_seconds=spacing,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
