"""Archive the leftover DATED digest pages under NOTION_PARENT_PAGE_ID.

Context (see PROGRESS.md, "PART 1 — digests: single persistent page"): the
digests used to create a brand-new dated page per run
("🌙 Daily reflection — 2026-07-20", "📬 Weekly digest — 2026-07-20", ...).
That was fixed — digest.create_notion_page / create_daily_notion_page now call
notion_writer.upsert_named_page with the FIXED titles "📬 Weekly Digest" and
"🌙 Daily Reflection", replacing the body each run. But the pages created
BEFORE that fix were deliberately left in place ("didn't want to delete
historical pages without asking"). This archives them.

SAFETY — the match is deliberately narrow. Only a title matching exactly
    "🌙 Daily reflection — YYYY-MM-DD"  or  "📬 Weekly digest — YYYY-MM-DD"
is ever touched. The live persistent pages differ in BOTH case and the absence
of a date suffix ("Daily Reflection" vs "Daily reflection — 2026-07-20"), and
"🔍 Attach Audit Log" / "🔭 Scout Pick" match nothing here, so none of them can
be caught by accident. Anything not matching the dated pattern is listed as
"kept" so you can see what was spared.

Notion "archive" = moved to trash (restorable), never a hard delete.

Usage:
    python scripts/archive_dated_digests.py             # list only
    python scripts/archive_dated_digests.py --apply     # archive them
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("reelbrain.archive_dated_digests")

# Exactly the pre-fix titles: the old lowercase wording plus an em-dash date.
DATED_DIGEST_RE = re.compile(r"^(🌙 Daily reflection|📬 Weekly digest)\s+—\s+\d{4}-\d{2}-\d{2}$")


def list_child_pages(client, parent_page_id: str) -> list[dict]:
    """[{"page_id", "title"}] for every direct child PAGE of the parent."""
    pages, cursor = [], None
    while True:
        kwargs = {"block_id": parent_page_id}
        if cursor:
            kwargs["start_cursor"] = cursor
        resp = client.blocks.children.list(**kwargs)
        for block in resp.get("results", []):
            if block.get("type") == "child_page":
                pages.append({"page_id": block["id"], "title": block["child_page"]["title"]})
        if not resp.get("has_more"):
            return pages
        cursor = resp.get("next_cursor")


def split_dated_digests(pages: list[dict]) -> tuple[list[dict], list[dict]]:
    """(dated_digest_artifacts, kept) — kept is everything the pattern spares."""
    dated = [p for p in pages if DATED_DIGEST_RE.match(p["title"])]
    kept = [p for p in pages if not DATED_DIGEST_RE.match(p["title"])]
    return dated, kept


def archive_pages(client, pages: list[dict], print_fn=print) -> int:
    archived = 0
    for page in pages:
        try:
            client.pages.update(page_id=page["page_id"], archived=True)
        except Exception:  # noqa: BLE001 - one failure must not sink the rest
            logger.exception("failed to archive %s", page["title"])
            print_fn(f"  FAILED: {page['title']}")
            continue
        print_fn(f"  archived: {page['title']}")
        archived += 1
    return archived


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="actually archive (default: list only)")
    args = parser.parse_args()

    from app import notion_writer

    parent = os.environ.get("NOTION_PARENT_PAGE_ID", "").strip()
    if not parent:
        sys.exit("NOTION_PARENT_PAGE_ID not set")

    client = notion_writer._client()
    pages = list_child_pages(client, parent)
    dated, kept = split_dated_digests(pages)

    print(f"{len(pages)} child page(s) under NOTION_PARENT_PAGE_ID\n")
    print(f"DATED DIGEST ARTIFACTS to archive ({len(dated)}):")
    for p in dated:
        print(f"   {p['title']}")
    print(f"\nKEPT ({len(kept)}) — live/persistent pages, never touched:")
    for p in kept:
        print(f"   {p['title']}")

    if not args.apply:
        print(f"\n[list only] re-run with --apply to archive the {len(dated)} dated page(s).")
        return

    if not dated:
        print("\nnothing to archive.")
        return

    print()
    count = archive_pages(client, dated)
    print(f"\narchived {count}/{len(dated)} dated digest page(s) (moved to Notion trash, restorable).")


if __name__ == "__main__":
    main()
