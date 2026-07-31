"""Relabel bucket-A rows: `uncategorized` -> `pending-extraction`.

These are rows carrying a real caption (25-295 words, live) whose extraction
degraded, leaving no entities and no topics. The old fallback labelled them
`uncategorized`, which asserts "we looked and there is nothing here" — untrue,
and it hides the actual state (extraction never succeeded) behind a verdict that
looks final.

This is a FREE correction: zero Gemini calls, it only rewrites the Topics
property. It does not attempt extraction — that is quota-bound work the daily
runner does. What it buys is an honest, queryable state now, so the moment
Gemini is available those rows are findable as "needs another attempt" rather
than sitting indistinguishable from genuinely-empty ones.

Safe by default: dry-run unless --apply.

Usage:
    python scripts/relabel_pending_extraction.py
    python scripts/relabel_pending_extraction.py --apply
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

from app.topic_guarantee import PENDING_EXTRACTION_TAG, UNCATEGORIZED_TAG

logger = logging.getLogger("reelbrain.relabel_pending_extraction")


def write_topics(page_id: str, topics: list[str]) -> None:
    from app import notion_writer

    notion_writer._client().pages.update(
        page_id=page_id,
        properties={"Topics": {"multi_select": [{"name": t} for t in topics]}},
    )


def run(dry_run: bool = True, write_fn: Callable[[str, list], None] = write_topics,
        print_fn: Callable[[str], None] = print) -> dict:
    from app import notion_writer
    from scripts import classify_uncategorized as cu

    pages = notion_writer.find_saves_pages_since("1970-01-01T00:00:00")
    rows = cu.find_uncategorized_rows(pages, with_bodies=True)
    buckets = cu.classify_all(rows)
    targets = buckets["A"]

    print_fn(f"{len(rows)} '{UNCATEGORIZED_TAG}' rows; {len(targets)} are bucket A "
             f"(real content, extraction never succeeded).")

    relabelled = errors = 0
    for i, row in enumerate(targets):
        label = (f"[{i + 1}/{len(targets)}] {row['shortcode']}: "
                 f"{UNCATEGORIZED_TAG} -> {PENDING_EXTRACTION_TAG}")
        if dry_run:
            print_fn(f"[dry-run] {label}")
            continue
        try:
            write_fn(row["page_id"], [PENDING_EXTRACTION_TAG])
            relabelled += 1
            print_fn(label)
        except Exception as exc:  # noqa: BLE001 - one bad row must not sink the batch
            errors += 1
            print_fn(f"{label} -> ERROR: {exc}")

    return {"total_uncategorized": len(rows), "bucket_a": len(targets),
            "relabelled": relabelled, "errors": errors, "dry_run": dry_run}


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    logging.basicConfig(level=logging.WARNING)

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="actually write to Notion")
    args = parser.parse_args()

    result = run(dry_run=not args.apply)
    print(f"\n{'APPLIED' if args.apply else 'DRY-RUN'}: "
          f"{result['relabelled']} relabelled, {result['errors']} errors, "
          f"of {result['bucket_a']} bucket-A rows.")


if __name__ == "__main__":
    main()
