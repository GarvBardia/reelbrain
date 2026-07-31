"""Backfill "Suggested action" for existing REAL (non-placeholder) Saves rows.

LOCAL-ONLY. LIVE when run without --dry-run: hits Notion + Gemini for real.
Do NOT run as part of a deploy — it's a one-off, quota-safe, resumable batch.

For each row that (a) is not a placeholder ("No caption or transcript
available." title / 📷 Photo — manual status rows are the recovery worker's
job, not this script's), and (b) has no "Suggested action" yet: one small
text-only Gemini call over the row's Title + Raw caption produces the single
imperative line, written back to just that one property (pages.update — the
body and every other property are untouched).

Quota safety: MIN_GEMINI_CALL_SPACING_SECONDS is enforced inside the Gemini
call; a 429/RESOURCE_EXHAUSTED stops the whole run cleanly (resumable).

Usage:
    python scripts/backfill_suggested_action.py --dry-run   # list rows only
    python scripts/backfill_suggested_action.py             # actually backfill
    python scripts/backfill_suggested_action.py --limit 5
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

PLACEHOLDER_TITLE = "No caption or transcript available."
PHOTO_MANUAL_LABEL = "📷 Photo — manual"
DEFAULT_PROGRESS_FILE = "backfill_suggested_action_progress.json"
# Single source of truth lives in app.gemini_pipe -- these were 7 copies that
# each had their own idea of "quota error", which is how a billing 429 hid.
from app.gemini_pipe import QUOTA_MARKERS  # noqa: E402  (re-exported)

_PROMPT = """A personal knowledge base entry was saved from an Instagram reel. \
Based on its title and caption below, write ONE imperative line stating the single \
most direct next step the saver could take (e.g. "Install X and test on one clip", \
"Clone the repo and run the demo"). If it is purely informational with nothing to \
act on, answer exactly: none — informational
Answer with the single line only — no quotes, no markdown, no explanation.

Title: {title}
Caption: {caption}
"""


class QuotaExhausted(Exception):
    pass


def find_backfill_rows() -> list[dict]:
    """Real rows (not placeholders, not photo-manual) with no Suggested action yet."""
    from app import notion_writer

    pages = notion_writer.find_saves_pages_since("1970-01-01T00:00:00")
    rows = []
    for page in pages:
        fields = notion_writer.extract_saves_fields(page)
        if not fields["shortcode"]:
            continue
        if fields["title"] == PLACEHOLDER_TITLE or fields["status_label"] == PHOTO_MANUAL_LABEL:
            continue
        if fields["suggested_action"]:
            continue
        rows.append(fields)
    return rows


def _fetch_raw_caption(page_id: str) -> str:
    """Best-effort: the 'Raw caption' toggle's first paragraph, else ''."""
    from app import notion_writer

    client = notion_writer._client()
    try:
        blocks = client.blocks.children.list(block_id=page_id)["results"]
        for block in blocks:
            if block.get("type") != "toggle":
                continue
            title = notion_writer._rt_text(block["toggle"].get("rich_text"))
            if title != "Raw caption":
                continue
            children = client.blocks.children.list(block_id=block["id"])["results"]
            for child in children:
                if child.get("type") == "paragraph":
                    return notion_writer._rt_text(child["paragraph"].get("rich_text"))
    except Exception:  # noqa: BLE001 - caption is an enhancement, not a requirement
        pass
    return ""


def suggest_action(title: str, caption: str) -> str:
    """One plain-text Gemini call. Raises QuotaExhausted on a 429 so the run
    stops cleanly instead of burning through remaining rows."""
    from google import genai

    from app import gemini_pipe

    client = genai.Client(api_key=gemini_pipe.GEMINI_API_KEY)
    gemini_pipe._enforce_gemini_call_spacing()
    try:
        response = client.models.generate_content(
            model=gemini_pipe.GEMINI_MODEL,
            contents=[_PROMPT.format(title=title, caption=caption or "(none)")],
        )
    except Exception as exc:  # noqa: BLE001
        gemini_pipe.note_gemini_failure(exc)
        if any(m in str(exc) for m in QUOTA_MARKERS):
            raise QuotaExhausted(str(exc)) from exc
        raise
    return (response.text or "").strip().splitlines()[0][:500]


def write_action(page_id: str, action: str) -> None:
    from app import notion_writer

    client = notion_writer._client()
    client.pages.update(
        page_id=page_id,
        properties={"Suggested action": {"rich_text": notion_writer._rich_text(action)}},
    )


def load_progress(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_progress(path: str, progress: dict) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(progress, f, indent=2, sort_keys=True)
    os.replace(tmp, path)


def run_backfill(
    rows: list[dict],
    progress_file: str,
    dry_run: bool = False,
    suggest_fn: Callable[[str, str], str] = suggest_action,
    caption_fn: Callable[[str], str] = _fetch_raw_caption,
    write_fn: Callable[[str, str], None] = write_action,
    print_fn: Callable[[str], None] = print,
) -> dict:
    progress = load_progress(progress_file)
    written = errors = skipped = 0
    quota_stopped = False

    for i, fields in enumerate(rows):
        shortcode = fields["shortcode"]
        if progress.get(shortcode, {}).get("status") == "written":
            skipped += 1
            continue
        if dry_run:
            print_fn(f"[dry-run] would backfill: {shortcode}  {fields['title'][:70]}")
            continue

        try:
            caption = caption_fn(fields["page_id"])
            action = suggest_fn(fields["title"], caption)
            if not action:
                raise ValueError("empty suggestion returned")
            write_fn(fields["page_id"], action)
        except QuotaExhausted as exc:
            print_fn(f"[{i + 1}/{len(rows)}] {shortcode} -> QUOTA STOP ({str(exc)[:120]}); "
                     f"stopping cleanly, re-run later to resume")
            quota_stopped = True
            break
        except Exception as exc:  # noqa: BLE001 - keep going; retried next run
            errors += 1
            print_fn(f"[{i + 1}/{len(rows)}] {shortcode} -> ERROR (retryable next run): {exc}")
            continue

        written += 1
        progress[shortcode] = {
            "status": "written", "action": action,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        save_progress(progress_file, progress)
        print_fn(f"[{i + 1}/{len(rows)}] {shortcode} -> {action}")

    summary = {"written": written, "errors": errors, "skipped": skipped,
               "quota_stopped": quota_stopped, "total_rows": len(rows)}
    print_fn(f"\ndone: {written} written, {errors} errors, {skipped} skipped, "
             f"quota_stopped={quota_stopped}, out of {len(rows)} rows")
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

    print("querying Notion for rows needing a Suggested action...")
    rows = find_backfill_rows()
    print(f"found {len(rows)} row(s)")
    if args.limit is not None:
        rows = rows[:args.limit]

    run_backfill(rows=rows, progress_file=args.progress_file, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
