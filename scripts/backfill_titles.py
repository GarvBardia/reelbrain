"""Backfill titles truncated by the old 100-char cap, from each page's body callout.

Until Phase A (2026-08-20), notion_writer stored the Notion Title as
`main_point[:100]`, so ~110 rows ended mid-word ("...cheaper version of") with
the full sentence surviving only in the page-body 💡 callout (which is written
from the SAME main_point, uncapped). The cap is now 200, but existing rows keep
their truncated title until this runs.

This is a pure Notion read/write -- **ZERO Gemini calls**. It never re-extracts;
it copies the fuller text that already exists in the callout into the Title.

A row is a candidate only when its callout text is LONGER than its title AND the
title is a prefix of the callout -- i.e. the callout is provably the same
main_point, just un-truncated. That prefix check is the guard against clobbering
a title someone edited by hand to something the callout doesn't start with.

Print-and-stop by default (same convention as notion_deep_clean); pass --apply
to actually write. Idempotent: a second run finds fewer candidates because the
titles it fixed are now full-length.

Usage:
    python scripts/backfill_titles.py           # dry run: count + examples, no writes
    python scripts/backfill_titles.py --apply    # write the new titles
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

# The old cap. Only titles at or above it can have been truncated; anything
# shorter is a complete main_point and is skipped without even reading blocks.
OLD_CAP = 100
NEW_CAP = 200
# Notion's write limit is ~3 req/s; each applied row is one block read + one
# page update. A small gap keeps a 200-row run comfortably under it.
SLEEP_BETWEEN_WRITES = 0.35


def is_truncated_prefix(title: str, callout: str) -> bool:
    """True when `callout` is the same main_point as `title`, only longer --
    the exact condition for a safe backfill. Longer AND a prefix match: the
    prefix guard is what stops a hand-edited title (one the callout does not
    begin with) from being overwritten. Pure so it can be unit-tested without
    Notion."""
    if not title or not callout:
        return False
    return len(callout) > len(title) and callout.startswith(title)


def _callout_text(client, page_id: str):
    """The 💡 callout's plain text for a page, or None if it has no callout."""
    from app import notion_writer

    blocks = client.blocks.children.list(block_id=page_id)["results"]
    callout = next((b for b in blocks if b.get("type") == "callout"), None)
    if not callout:
        return None
    return notion_writer._rt_text(callout["callout"]["rich_text"])


def find_candidates(client, pages: list[dict]) -> list[dict]:
    """Rows whose title is a truncated prefix of a longer callout. Returns
    {shortcode, page_id, old_title, new_title}."""
    from app import notion_writer

    out = []
    for page in pages:
        digest = notion_writer.extract_digest_fields(page)
        title = (digest["title"] or "").strip()
        # Cheap filter first: a title under the old cap was never truncated.
        if len(title) < OLD_CAP:
            continue
        full = _callout_text(client, page["id"])
        if not full:
            continue
        full = full.strip()
        # Same main_point, just longer, and not a hand-edited divergent title.
        if is_truncated_prefix(title, full):
            out.append({
                "shortcode": digest["shortcode"],
                "page_id": page["id"],
                "old_title": title,
                "new_title": full[:NEW_CAP],
            })
    return out


def apply_one(client, cand: dict) -> None:
    client.pages.update(
        page_id=cand["page_id"],
        properties={"Title": {"title": [{"text": {"content": cand["new_title"]}}]}},
    )


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true",
                        help="write the new titles (default is a dry run: count + examples only)")
    args = parser.parse_args()

    from app import notion_writer

    client = notion_writer._client()
    pages = notion_writer.find_saves_pages_since("1970-01-01T00:00:00")
    candidates = find_candidates(client, pages)

    print(f"Scanned {len(pages)} rows. Truncated-title candidates: {len(candidates)}")
    for c in candidates[:3]:
        print(f"\n  {c['shortcode']}")
        print(f"    BEFORE ({len(c['old_title'])}): ...{c['old_title'][-45:]!r}")
        print(f"    AFTER  ({len(c['new_title'])}): ...{c['new_title'][-45:]!r}")

    if not args.apply:
        print(f"\nDRY RUN — nothing written. Re-run with --apply to rewrite {len(candidates)} title(s).")
        return

    written = 0
    errors = 0
    for c in candidates:
        try:
            apply_one(client, c)
            written += 1
            time.sleep(SLEEP_BETWEEN_WRITES)
        except Exception as exc:  # noqa: BLE001 - one bad row must not sink the batch
            errors += 1
            print(f"  ! {c['shortcode']} failed: {exc}")
    print(f"\nApplied: {written} rewritten, {errors} error(s).")


if __name__ == "__main__":
    main()
