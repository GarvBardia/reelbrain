"""Home-IP probe: does Instagram serve usable content to THIS machine's IP?

LOCAL-ONLY — run from your own machine, never deployed to Render, never called
by the live app. It is a diagnostic + one-off ingest tool, not part of the
pipeline.

Context: the audit confirmed Render's datacenter IP can't fetch these posts
(video download soft-blocked; OG caption scrape login-walled). This script
tests, from your residential IP, BOTH recovery paths for each URL and reports
exactly what came back:

  1. MEDIA (yt-dlp, home IP + local burner cookies) — if a video actually
     downloads, we can run the FULL extraction (transcript + everything).
  2. OG CAPTION (anonymous, link-preview bot UA) — the fallback proven to work
     from home last session for photo/carousel posts (browser UA gets a
     tag-less page; the facebookexternalhit UA gets the caption).

Then it runs the real extraction (full if media, caption-only if only a
caption) and PRINTS the result. By default it does NOT write to Notion — this
is a proof probe. Pass --write to actually persist recovered rows to Notion
(used in the bulk-ingest stage, once the probe has proven the approach).

Usage:
    # probe (no writes) — the Stage 1 checkpoint:
    python scripts/local_fetch.py https://www.instagram.com/p/XXXX/ https://www.instagram.com/p/YYYY/

    # probe every URL in a file:
    python scripts/local_fetch.py --from-file urls.txt

Env vars:
    GEMINI_API_KEY               required for the extraction step
    MIN_FETCH_SPACING_SECONDS    delay between URLs, seconds (default 20)
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

# Reuse the proven bot-UA OG helper + caption cleaner from the recovery script.
from scripts.recover_photo_captions import clean_og_caption, fetch_og_tags_with_bot_ua


def _try_media_fetch(shortcode: str, permalink: str) -> tuple[Optional[dict], str]:
    """Attempt a real yt-dlp media download from THIS machine (home IP + local
    cookies). Returns (info_dict, note). info_dict is None if no video came
    down (photo/carousel, or a genuine block). Never raises."""
    from app import fetcher

    cookies = fetcher.resolve_cookies_file()
    try:
        info = fetcher._run_ytdlp(permalink, cookiefile=cookies)
    except Exception as exc:  # noqa: BLE001 - expected for photo/carousel + blocks
        return None, f"no media: {type(exc).__name__}: {str(exc)[:160]}"
    if not info.get("_video_path") or not os.path.exists(info.get("_video_path", "")):
        return None, "yt-dlp returned info but no video file on disk"
    return info, f"media downloaded: {info['_video_path']}"


def probe_one(url: str, write: bool = False) -> dict:
    """Fetch + extract one URL, printing a definitive per-URL verdict. Returns a
    structured result dict."""
    from app import fetcher, gemini_pipe, store
    from app.models import ReelData

    print(f"\n{'='*70}\nURL: {url}")
    try:
        shortcode = fetcher.normalize_url(url)
    except ValueError as exc:
        print(f"  SKIP — {exc}")
        return {"url": url, "outcome": "bad_url"}
    permalink = f"https://www.instagram.com/reel/{shortcode}/"
    print(f"  shortcode: {shortcode}")

    # --- path 1: media (full extraction if it works) ---
    info, media_note = _try_media_fetch(shortcode, permalink)
    print(f"  MEDIA:   {media_note}")

    reel: Optional[ReelData] = None
    extraction = None
    path = None

    if info is not None:
        reel = fetcher._info_to_reel_data(shortcode, permalink, info)
        extraction = gemini_pipe.run_extraction(reel, note=None, taxonomy=store.get_taxonomy())
        path = "full (video+transcript)"
    else:
        # --- path 2: OG caption (caption-only extraction) ---
        tags = fetch_og_tags_with_bot_ua(permalink)
        if not tags:
            print("  OG:      NO TAGS returned (blocked or unavailable from this IP)")
            return {"url": url, "shortcode": shortcode, "outcome": "blocked"}
        caption, username = clean_og_caption(tags)
        if not caption:
            print("  OG:      tags present but NO caption text extractable")
            return {"url": url, "shortcode": shortcode, "outcome": "no_caption"}
        print(f"  OG:      caption recovered ({len(caption)} chars) | creator: {username}")
        print(f"           \"{caption[:160]}\"")
        reel = ReelData(
            shortcode=shortcode, permalink=permalink, caption=caption,
            creator_username=username, is_photo_or_carousel=True,
        )
        extraction = gemini_pipe.run_caption_only_extraction(
            caption, creator=username, note=None, taxonomy=store.get_taxonomy()
        )
        path = "caption-only (photo/carousel)"

    degraded = extraction.content_type == "unknown" and not extraction.topic_tags
    print(f"  EXTRACT: via {path}")
    print(f"           title:    {extraction.main_point[:120]}")
    print(f"           topics:   {extraction.topic_tags}")
    print(f"           value:    {extraction.value_score}   priority: {extraction.priority}")
    if extraction.comment_gate.detected:
        print(f"           gate:     keyword={extraction.comment_gate.keyword!r}")
    if degraded:
        print("           >>> DEGRADED (Gemini likely unavailable) — not a usable extraction")

    result = {
        "url": url, "shortcode": shortcode,
        "outcome": "degraded" if degraded else "extracted",
        "path": path, "title": extraction.main_point,
        "topics": extraction.topic_tags, "value_score": extraction.value_score,
        "priority": extraction.priority,
    }

    if write and not degraded and reel is not None:
        from app import notion_writer
        status = "photo_manual" if reel.is_photo_or_carousel else (
            "awaiting_dm" if extraction.comment_gate.detected
            else "low_signal" if extraction.value_score <= 2 else "done"
        )
        try:
            page = notion_writer.create_page(reel, extraction, status, note=reel.fetch_note)
            print(f"           WROTE Notion page: {page['url']}")
            result["notion_url"] = page["url"]
            result["written"] = True
        except Exception as exc:  # noqa: BLE001
            print(f"           Notion write FAILED: {exc}")
            result["written"] = False
    return result


def _read_urls(path: str) -> list[str]:
    urls = []
    with open(path, "r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if line and ("instagram.com" in line):
                urls.append(line)
    return urls


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("urls", nargs="*", help="one or more Instagram /p/ or /reel/ URLs")
    parser.add_argument("--from-file", help="read URLs from a file (one per line)")
    parser.add_argument("--write", action="store_true", help="actually write recovered rows to Notion (default: probe only)")
    args = parser.parse_args()

    from app import store
    store.init_db()

    urls = list(args.urls)
    if args.from_file:
        urls.extend(_read_urls(args.from_file))
    if not urls:
        sys.exit("no URLs given (pass URLs or --from-file)")

    spacing = float(os.environ.get("MIN_FETCH_SPACING_SECONDS", "20"))
    results = []
    for i, url in enumerate(urls):
        results.append(probe_one(url, write=args.write))
        if i != len(urls) - 1:
            time.sleep(spacing)

    print(f"\n{'='*70}\nSUMMARY ({len(results)} URLs):")
    for r in results:
        print(f"  {r.get('shortcode', '?'):14} {r['outcome']:10} {r.get('path', '')}")


if __name__ == "__main__":
    main()
