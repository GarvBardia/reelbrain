"""Self-healing sweep: no reel may exist without topics (Phase H).

Scans Notion for ANY row with empty Topics and fixes each through the same
fallback chain the write path uses:
    named_entities (slugified) + content_type  ->  else "uncategorized"

Deliberately does NOT call Gemini. app/topic_guarantee.derive_fallback_tags
works purely from data already on the row, so this sweep is free, instant, and
runs even when the daily quota is exhausted — which matters, because quota
exhaustion is exactly when topic-less rows pile up. Rows that deserve *better*
topics than the fallback are separately handled by
notion_deep_clean.py --fix-topics, which does spend quota.

Safe to run repeatedly; a row it has already fixed no longer matches.

Usage:
    python scripts/enforce_topics.py --dry-run
    python scripts/enforce_topics.py
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from app.topic_guarantee import derive_fallback_tags

logger = logging.getLogger("reelbrain.enforce_topics")


def find_topicless_rows(pages: list[dict]) -> list[dict]:
    """Every row with zero Topics, with the raw material the fallback needs."""
    from app import notion_writer

    rows = []
    for page in pages:
        fields = notion_writer.extract_saves_fields(page)
        if not fields["shortcode"]:
            continue
        digest = notion_writer.extract_digest_fields(page)
        if digest["topics"]:
            continue
        props = page.get("properties", {})
        rows.append({
            "shortcode": fields["shortcode"],
            "page_id": fields["page_id"],
            "title": fields["title"],
            "named_entities": digest.get("named_entities") or [],
            "content_type": ((props.get("Content type") or {}).get("select") or {}).get("name", ""),
        })
    return rows


def write_topics(page_id: str, topics: list[str]) -> None:
    from app import notion_writer

    notion_writer._client().pages.update(
        page_id=page_id,
        properties={"Topics": {"multi_select": [{"name": t} for t in topics]}},
    )


def run_enforce(
    rows: list[dict],
    dry_run: bool = False,
    write_fn: Callable[[str, list], None] = write_topics,
    print_fn: Callable[[str], None] = print,
) -> dict:
    fixed = errors = 0
    uncategorized = 0
    for i, row in enumerate(rows):
        topics = derive_fallback_tags(row["named_entities"], row["content_type"])
        if dry_run:
            print_fn(f"[dry-run] {row['shortcode']} -> {topics}")
            continue
        try:
            write_fn(row["page_id"], topics)
        except Exception as exc:  # noqa: BLE001 - one bad row must not sink the sweep
            errors += 1
            print_fn(f"[{i + 1}/{len(rows)}] {row['shortcode']} -> ERROR: {exc}")
            continue
        fixed += 1
        if topics == ["uncategorized"]:
            uncategorized += 1
        print_fn(f"[{i + 1}/{len(rows)}] {row['shortcode']} -> {topics}")

    summary = {"fixed": fixed, "errors": errors, "uncategorized": uncategorized,
               "total_rows": len(rows)}
    print_fn(f"\ndone: {fixed} fixed ({uncategorized} could only be 'uncategorized'), "
             f"{errors} errors, of {len(rows)} topic-less rows")
    if uncategorized:
        print_fn("NOTE: 'uncategorized' rows have no named_entities and no content_type — "
                 "they need real extraction (recover_placeholders / --fix-topics), not a sweep.")
    return summary


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from app import notion_writer

    pages = notion_writer.find_saves_pages_since("1970-01-01T00:00:00")
    rows = find_topicless_rows(pages)
    print(f"scanned {len(pages)} rows; {len(rows)} have empty Topics\n")
    if not rows:
        print("nothing to fix — every row has topics.")
        return
    run_enforce(rows, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
