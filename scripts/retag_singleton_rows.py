"""Repair the taxonomy collapse (PROGRESS.md, 2026-08-09 incident): re-tag every row
whose topic-tag combination is unique across the whole corpus.

Root cause (see PROGRESS.md): store.get_taxonomy() read only local SQLite, which was
wiped repeatedly and never had a real candidate list -- every extraction call got a
near-empty taxonomy prompt, so almost every row invented its own one-off tags instead
of converging onto a shared set. That's fixed now (notion_writer.get_live_taxonomy),
so a NEW extraction won't repeat this. This script repairs the EXISTING damage:
already-written rows still carry the singleton/hallucinated tags from before the fix.

LOCAL-ONLY, text-only, no re-fetch: re-derives topic_tags from content ALREADY STORED
in Notion (Title = main_point, Supporting points block, Named entities, Content type)
via one small Gemini call per row (app.gemini_pipe.run_topic_retag), against the
now-healthy live taxonomy. Marker-only rows (uncategorized/near-duplicate/
pending-extraction) are OUT OF SCOPE -- they have no real content to re-tag from; that
gap is recover_placeholders'/classify_uncategorized's job, not this script's.

A row counts as "singleton" if its (merge+plural-canonicalized) topic combination is
carried by exactly one row in the whole corpus -- i.e. nothing else shares it.

Quota-safe (routes through generate_content_tracked's spacing), resumable via a
progress file, --dry-run, --limit.

Usage:
    python scripts/retag_singleton_rows.py --dry-run
    python scripts/retag_singleton_rows.py --limit 10
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from app.taxonomy import NON_TOPIC_TAGS, apply_merges, canonicalize_plurals

DEFAULT_PROGRESS_FILE = "retag_singleton_rows_progress.json"


class QuotaExhausted(Exception):
    pass


def _supporting_points_text(page_id: str) -> list[str]:
    """The row's stored supporting_points, read back from the bulleted_list_item
    blocks _build_children wrote them as -- see app/notion_writer.py."""
    from app import notion_writer

    client = notion_writer._client()
    points: list[str] = []
    try:
        for block in client.blocks.children.list(block_id=page_id).get("results", []):
            if block.get("type") == "bulleted_list_item":
                text = notion_writer._rt_text(block["bulleted_list_item"].get("rich_text"))
                if text:
                    points.append(text)
    except Exception:  # noqa: BLE001 - supporting points are a bonus signal, not required
        pass
    return points


def _content_type(page: dict) -> str:
    props = page.get("properties", {})
    return (((props.get("Content type") or {}).get("select")) or {}).get("name", "")


def find_singleton_rows(pages: list[dict]) -> list[dict]:
    """Every row whose canonicalized topic combination is unique in the corpus,
    excluding marker-only rows (nothing real to re-tag from)."""
    from app import notion_writer

    rows = []
    for page in pages:
        digest = notion_writer.extract_digest_fields(page)
        if not digest["shortcode"]:
            continue
        topics = list(digest["topics"])
        canonical_topics = canonicalize_plurals(apply_merges(topics))
        if not canonical_topics or all(t in NON_TOPIC_TAGS for t in canonical_topics):
            continue  # marker-only -- out of scope, see module docstring
        rows.append({
            "shortcode": digest["shortcode"],
            "page_id": page.get("id", ""),
            "main_point": digest["title"],
            "named_entities": digest["named_entities"],
            "content_type": _content_type(page),
            "current_topics": topics,
            "canonical_key": tuple(sorted(canonical_topics)),
        })

    counts = Counter(r["canonical_key"] for r in rows)
    return [r for r in rows if counts[r["canonical_key"]] == 1]


def write_topics(page_id: str, topics: list[str]) -> None:
    from app import notion_writer

    notion_writer._client().pages.update(
        page_id=page_id,
        properties={"Topics": {"multi_select": [{"name": t} for t in topics]}},
    )


def _load(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save(path: str, data: dict) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    os.replace(tmp, path)


def run_retag(
    rows: list[dict],
    taxonomy: list[str],
    progress_file: str,
    dry_run: bool = False,
    retag_fn=None,
    body_fn: Callable[[str], list[str]] = _supporting_points_text,
    write_fn: Callable[[str, list], None] = write_topics,
    print_fn: Callable[..., None] = print,
) -> dict:
    if retag_fn is None:
        from app import gemini_pipe
        retag_fn = gemini_pipe.run_topic_retag

    progress = _load(progress_file)
    written = errors = skipped = 0
    quota_stopped = False

    for i, row in enumerate(rows):
        shortcode = row["shortcode"]
        if progress.get(shortcode, {}).get("status") == "written":
            skipped += 1
            continue
        if dry_run:
            print_fn(f"[dry-run] would re-tag {shortcode}: {row['current_topics']}  "
                      f"({row['main_point'][:60]})")
            continue
        try:
            supporting_points = body_fn(row["page_id"])
            new_topics = retag_fn(
                row["main_point"], supporting_points, row["named_entities"],
                row["content_type"], taxonomy,
            )
        except QuotaExhausted as exc:
            print_fn(f"[{i + 1}/{len(rows)}] {shortcode} -> QUOTA STOP ({str(exc)[:100]}); resume later")
            quota_stopped = True
            break
        except Exception as exc:  # noqa: BLE001 - retried next run, from is_quota_error re-raise or otherwise
            from app.gemini_pipe import is_quota_error
            if is_quota_error(exc):
                print_fn(f"[{i + 1}/{len(rows)}] {shortcode} -> QUOTA STOP ({str(exc)[:100]}); resume later")
                quota_stopped = True
                break
            errors += 1
            print_fn(f"[{i + 1}/{len(rows)}] {shortcode} -> ERROR (retryable): {exc}")
            continue

        if new_topics is None:
            errors += 1
            print_fn(f"[{i + 1}/{len(rows)}] {shortcode} -> DEGRADED (Gemini) — will retry later")
            continue

        try:
            write_fn(row["page_id"], new_topics)
        except Exception as exc:  # noqa: BLE001 - a bad write must not sink the batch
            errors += 1
            print_fn(f"[{i + 1}/{len(rows)}] {shortcode} -> WRITE ERROR (retryable): {exc}")
            continue

        written += 1
        progress[shortcode] = {
            "status": "written", "before": row["current_topics"], "after": new_topics,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        _save(progress_file, progress)
        print_fn(f"[{i + 1}/{len(rows)}] {shortcode}: {row['current_topics']} -> {new_topics}")

    summary = {"written": written, "errors": errors, "skipped": skipped,
               "quota_stopped": quota_stopped, "total_rows": len(rows)}
    print_fn(f"\ndone: {written} written, {errors} errors, {skipped} skipped, "
             f"quota_stopped={quota_stopped}, of {len(rows)} rows")
    return summary


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--progress-file", default=DEFAULT_PROGRESS_FILE)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    from app import notion_writer

    pages = notion_writer.find_saves_pages_since("1970-01-01T00:00:00")
    rows = find_singleton_rows(pages)
    print(f"{len(pages)} total rows; {len(rows)} have a singleton (unique) topic combination")
    taxonomy = notion_writer.get_live_taxonomy(limit=40, force_refresh=True)
    print(f"taxonomy candidates ({len(taxonomy)}): {', '.join(taxonomy)}")

    if args.limit is not None:
        rows = rows[: args.limit]

    run_retag(rows, taxonomy, args.progress_file, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
