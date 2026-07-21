"""Flag fully-extracted Saves rows with a 'Processed' checkbox so real rows are
never mistaken for needing re-work. LOCAL-ONLY (writes Notion live).

A row counts as PROCESSED (real AI extraction) only if BOTH:
  - its Title is a synthesized summary (not the "No caption or transcript
    available." placeholder), AND
  - it has at least one REAL topic (excluding the auto-tag "near-duplicate").

That second condition is the strict part: it excludes not just placeholders but
also the raw-caption-title rows (e.g. 'comment AGENTS for the guide') that have
a caption-ish title but NO real topics — those are degraded, not processed.
Placeholders/failures are left UNFLAGGED (unchecked) per instruction — the
script only ever SETS True on genuinely-processed rows, never unchecks anything.

Adds the 'Processed' checkbox property to the Saves data source first
(idempotent — re-declaring an existing property is a no-op).

Usage:
    python scripts/flag_processed.py --dry-run   # report what WOULD be flagged
    python scripts/flag_processed.py             # add property + flag real rows
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

PLACEHOLDER_TITLE = "No caption or transcript available."
PROCESSED_PROP = "Processed"
WRITE_SPACING_SECONDS = 0.35  # gentle on the Notion API


def is_processed_worthy(title: str, topics: list[str]) -> bool:
    real_topics = [t for t in topics if t != "near-duplicate"]
    return bool(title) and title != PLACEHOLDER_TITLE and len(real_topics) > 0


def _fields(page: dict) -> dict:
    from app import notion_writer

    f = notion_writer.extract_digest_fields(page)
    f["processed_now"] = bool((page.get("properties", {}).get(PROCESSED_PROP) or {}).get("checkbox"))
    return f


def ensure_processed_property() -> None:
    from app import notion_writer

    client = notion_writer._client()
    ds_id = notion_writer._resolve_data_source_id(client, notion_writer.NOTION_DB_ID)
    client.data_sources.update(
        data_source_id=ds_id,
        properties={PROCESSED_PROP: {"checkbox": {}}},
    )


def run(dry_run: bool = False, print_fn=print) -> dict:
    from app import notion_writer

    pages = notion_writer.find_saves_pages_since("1970-01-01T00:00:00")
    rows = [(p, _fields(p)) for p in pages]

    worthy = [(p, f) for p, f in rows if is_processed_worthy(f["title"], f["topics"])]
    unworthy = [(p, f) for p, f in rows if not is_processed_worthy(f["title"], f["topics"])]
    to_set = [(p, f) for p, f in worthy if not f["processed_now"]]

    print_fn(f"total rows: {len(rows)}  |  processed-worthy: {len(worthy)}  |  "
             f"left unflagged (placeholders/degraded): {len(unworthy)}")
    print_fn(f"already flagged: {len(worthy) - len(to_set)}  |  to flag now: {len(to_set)}")

    if dry_run:
        print_fn("\n[dry-run] would flag as Processed:")
        for _p, f in to_set:
            print_fn(f"  {f['shortcode']:14} {f['title'][:70]}")
        print_fn("\n[dry-run] would LEAVE UNFLAGGED (sample):")
        for _p, f in unworthy[:15]:
            print_fn(f"  {f['shortcode']:14} {f['title'][:60]}")
        return {"worthy": len(worthy), "to_set": len(to_set), "unworthy": len(unworthy), "flagged": 0}

    ensure_processed_property()
    client = notion_writer._client()
    flagged = 0
    for _p, f in to_set:
        try:
            client.pages.update(page_id=_p["id"], properties={PROCESSED_PROP: {"checkbox": True}})
            flagged += 1
        except Exception as exc:  # noqa: BLE001 - report and continue, never half-fail the batch
            print_fn(f"  FAILED {f['shortcode']}: {exc}")
        time.sleep(WRITE_SPACING_SECONDS)

    print_fn(f"\ndone: flagged {flagged} of {len(to_set)} as Processed; "
             f"{len(unworthy)} left unflagged.")
    return {"worthy": len(worthy), "to_set": len(to_set), "unworthy": len(unworthy), "flagged": flagged}


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
