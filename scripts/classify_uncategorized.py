"""Classify every `uncategorized` row into the three buckets that need different fixes.

The `uncategorized` tag is the topic-guarantee fallback of last resort: it means
enforce_topics found a row with no topics AND no named_entities to derive any
from. But rows arrive there for three very different reasons, and lumping them
together hides the only one that represents LOST WORK:

  A) Real content, never extracted. A genuine, substantial caption is sitting in
     the row, but named_entities/topics are empty — so the fallback had nothing
     to work with despite the row having plenty to say. These are the rows where
     information we already fetched is going unused. Only these need new
     extraction.
  B) True placeholders ("No caption or transcript available.") — there is
     genuinely nothing to categorize. Correctly `uncategorized`. They need
     RECOVERY (re-fetch), not re-extraction, and recover_placeholders owns them.
  C) "⚠️ Failed — retry" rows whose title is still a bare Instagram URL — the
     fetch never succeeded at all. Also recover_placeholders' job.

For B and C this script only VERIFIES queue membership and reports; it never
touches their content. Read-only by design — it emits the worklist, and the
re-extraction is a separate, quota-spending step.

Usage:
    python scripts/classify_uncategorized.py            # human-readable report
    python scripts/classify_uncategorized.py --json     # machine-readable
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from app.topic_guarantee import UNCATEGORIZED_TAG

logger = logging.getLogger("reelbrain.classify_uncategorized")

PLACEHOLDER_TITLE = "No caption or transcript available."
FAILED_RETRY_STATUS = "⚠️ Failed — retry"
# A caption needs real prose to be worth re-extracting. Tuned to match the
# extraction path's own floor: CHEAP_MODEL_GUIDE.md says don't attempt
# extraction under ~10 words, so anything at or above that has something to say.
MIN_SUBSTANTIAL_WORDS = 10

_PERMALINK_RE = re.compile(r"^https?://(www\.)?instagram\.com/\S*$", re.I)


def title_is_bare_permalink(title: str) -> bool:
    return bool(_PERMALINK_RE.match((title or "").strip()))


def is_placeholder(title: str) -> bool:
    return (title or "").strip() == PLACEHOLDER_TITLE


def caption_word_count(text: str) -> int:
    """Words of genuine prose, ignoring the markers the pipeline writes when it
    has nothing (so '(no caption)' never reads as content)."""
    cleaned = (text or "")
    for marker in ("(no caption)", "(no speech detected)", "(unavailable)",
                   PLACEHOLDER_TITLE):
        cleaned = cleaned.replace(marker, " ")
    cleaned = re.sub(r"https?://\S+", " ", cleaned)  # a bare URL is not prose
    return len([w for w in cleaned.split() if w.strip()])


def classify_row(row: dict) -> str:
    """'A' | 'B' | 'C' — see module docstring. Order matters: a placeholder title
    is bucket B even if it happens to sit in Failed-retry, because the recovery
    path keys off the placeholder, and a bare permalink in Failed-retry is C."""
    title = row.get("title", "")
    status = row.get("status_label", "")
    if is_placeholder(title):
        return "B"
    if status == FAILED_RETRY_STATUS and title_is_bare_permalink(title):
        return "C"
    if title_is_bare_permalink(title):
        return "C"
    # Anything left with real prose (in the title or the body) is bucket A: there
    # IS content here, it just never became topics/entities.
    if caption_word_count(row.get("body_text") or title) >= MIN_SUBSTANTIAL_WORDS:
        return "A"
    return "B"  # too thin to extract from — genuinely nothing to categorize


def _body_text(page_id: str) -> str:
    """The row's page body, where the real caption lives (Raw caption/Transcript
    toggles). Title alone is truncated to 100 chars, so classifying on it would
    misjudge rows whose caption is long but whose title is a stub."""
    from app import notion_writer

    try:
        client = notion_writer._client()
        blocks = client.blocks.children.list(block_id=page_id).get("results", [])
        out = []
        for b in blocks:
            btype = b.get("type", "")
            payload = b.get(btype) or {}
            for rt in payload.get("rich_text", []) or []:
                out.append(rt.get("plain_text", ""))
            if btype == "toggle":
                try:
                    for child in client.blocks.children.list(block_id=b["id"]).get("results", []):
                        ctype = child.get("type", "")
                        for rt in (child.get(ctype) or {}).get("rich_text", []) or []:
                            out.append(rt.get("plain_text", ""))
                except Exception:  # noqa: BLE001 - a toggle we can't read isn't fatal
                    pass
        return " ".join(out)
    except Exception:  # noqa: BLE001 - body is best-effort; title still classifies
        logger.debug("could not read body for %s", page_id, exc_info=True)
        return ""


def find_uncategorized_rows(pages: list[dict], with_bodies: bool = True) -> list[dict]:
    from app import notion_writer

    rows = []
    for page in pages:
        digest = notion_writer.extract_digest_fields(page)
        if not digest["shortcode"]:
            continue
        if list(digest["topics"]) != [UNCATEGORIZED_TAG]:
            continue
        row = {
            "shortcode": digest["shortcode"],
            "page_id": page.get("id", ""),
            "title": digest["title"],
            "status_label": digest["status_label"],
            "named_entities": digest["named_entities"],
            "value_score": digest["value_score"],
            "body_text": "",
        }
        if with_bodies:
            row["body_text"] = _body_text(row["page_id"])
        rows.append(row)
    return rows


def classify_all(rows: list[dict]) -> dict[str, list[dict]]:
    buckets: dict[str, list[dict]] = {"A": [], "B": [], "C": []}
    for row in rows:
        buckets[classify_row(row)].append(row)
    return buckets


def verify_queue_membership(buckets: dict[str, list[dict]]) -> dict:
    """B and C must already be in recover_placeholders' queue. Anything missing
    is a real gap — a row nothing will ever pick up."""
    from scripts import recover_placeholders

    queued = {r["shortcode"] for r in recover_placeholders.find_placeholder_rows()}
    missing = {}
    for bucket in ("B", "C"):
        absent = [r["shortcode"] for r in buckets[bucket] if r["shortcode"] not in queued]
        missing[bucket] = absent
    return {"queue_size": len(queued), "missing": missing}


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    logging.basicConfig(level=logging.WARNING)

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--no-bodies", action="store_true",
                        help="classify on title only (fast; less accurate)")
    args = parser.parse_args()

    from app import notion_writer

    pages = notion_writer.find_saves_pages_since("1970-01-01T00:00:00")
    rows = find_uncategorized_rows(pages, with_bodies=not args.no_bodies)
    buckets = classify_all(rows)
    queues = verify_queue_membership(buckets)

    if args.json:
        print(json.dumps({
            "total": len(rows),
            "buckets": {k: [r["shortcode"] for r in v] for k, v in buckets.items()},
            "queue": queues,
        }, indent=2))
        return

    print(f"{len(rows)} rows tagged only '{UNCATEGORIZED_TAG}'\n")
    print(f"  A — real content, never extracted : {len(buckets['A']):3d}  (needs re-extraction)")
    print(f"  B — true placeholders             : {len(buckets['B']):3d}  (recovery queue)")
    print(f"  C — failed-retry, bare URL title  : {len(buckets['C']):3d}  (recovery queue)")
    print(f"\nrecover_placeholders queue size: {queues['queue_size']}")
    for bucket, absent in queues["missing"].items():
        if absent:
            print(f"  !! bucket {bucket}: {len(absent)} row(s) NOT in the queue: {', '.join(absent)}")
        else:
            print(f"  bucket {bucket}: all rows present in the queue")

    if buckets["A"]:
        print(f"\n--- BUCKET A ({len(buckets['A'])} rows needing re-extraction) ---")
        for r in buckets["A"]:
            words = caption_word_count(r["body_text"] or r["title"])
            print(f"  {r['shortcode']:14s} [{r['status_label'] or '-'}] {words:4d}w  {r['title'][:70]}")


if __name__ == "__main__":
    main()
