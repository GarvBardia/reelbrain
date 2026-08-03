"""Backfill "Plain summary" for existing Saves rows (Phase C).

LOCAL-ONLY. NOT run automatically — trigger it yourself, or let
scripts/daily_runner.py spend a slice of the daily quota on it.

For each row with a REAL main_point but no Plain summary yet, one small
text-only Gemini call produces 1-2 zero-context sentences, written back to
just that one property (pages.update — body and every other property
untouched).

Skips, for the same reason the topic-tagging pass does (learned the hard way,
twice — see PROGRESS.md): a row whose "title" is the placeholder or its own
bare permalink has no real content, so Gemini would end up describing the
placeholder text or the URL. Those belong to recover_placeholders.py.

Quota safety: MIN_GEMINI_CALL_SPACING_SECONDS is enforced inside the call; a
429 stops the whole run cleanly and the progress file makes it resumable.

Usage:
    python scripts/backfill_plain_summary.py --dry-run
    python scripts/backfill_plain_summary.py --limit 10
    python scripts/backfill_plain_summary.py
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

from scripts.notion_deep_clean import PLACEHOLDER_TITLE, title_is_bare_permalink

DEFAULT_PROGRESS_FILE = "backfill_plain_summary_progress.json"
# Single source of truth lives in app.gemini_pipe -- these were 7 copies that
# each had their own idea of "quota error", which is how a billing 429 hid.
from app.gemini_pipe import QUOTA_MARKERS  # noqa: E402  (re-exported)

_PROMPT = """Rewrite this saved item's summary so a curious 12-year-old could follow it, \
assuming they have never heard of ANY tool or service named in it.

Rules:
- 1-2 sentences, everyday words.
- Explain what it actually lets someone DO, and why that's useful.
- If you must keep a product name, add a short plain-language gloss for it.
- Do not just reword the title with the same jargon.
- Answer with the sentences only -- no preamble, no quotes, no markdown.

Title: {title}
Topics: {topics}
"""


class QuotaExhausted(Exception):
    pass


def find_rows_needing_plain_summary(pages: list[dict]) -> list[dict]:
    from app import notion_writer

    rows = []
    for page in pages:
        fields = notion_writer.extract_saves_fields(page)
        shortcode, title = fields["shortcode"], fields["title"]
        if not shortcode or not title:
            continue
        if title == PLACEHOLDER_TITLE or title_is_bare_permalink(title, shortcode):
            continue  # no real content to explain -- recovery worker's job
        if fields.get("plain_summary"):
            continue
        digest = notion_writer.extract_digest_fields(page)
        rows.append({
            "shortcode": shortcode, "page_id": fields["page_id"],
            "title": title, "topics": digest["topics"],
        })
    return rows


def summarize_plainly(title: str, topics: list[str]) -> str:
    from google import genai

    from app import gemini_pipe

    client = genai.Client(api_key=gemini_pipe.GEMINI_API_KEY)
    try:
        # gemini_pipe.generate_content_tracked, not client.models.generate_content
        # directly: it records quota under the RESOLVED model version Google
        # actually served, not the requested string (see its docstring). A raw
        # generate_content call here would silently go untracked.
        response = gemini_pipe.generate_content_tracked(
            client, gemini_pipe.GEMINI_MODEL,
            contents=[_PROMPT.format(title=title, topics=", ".join(topics) or "(none)")],
        )
    except Exception as exc:  # noqa: BLE001
        gemini_pipe.note_gemini_failure(exc)
        if any(m in str(exc) for m in QUOTA_MARKERS):
            raise QuotaExhausted(str(exc)) from exc
        raise
    return " ".join((response.text or "").strip().split())[:600]


def write_plain_summary(page_id: str, text: str) -> None:
    from app import notion_writer

    notion_writer._client().pages.update(
        page_id=page_id,
        properties={"Plain summary": {"rich_text": notion_writer._rich_text(text)}},
    )


def _load_progress(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_progress(path: str, progress: dict) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(progress, f, indent=2, sort_keys=True)
    os.replace(tmp, path)


def run_backfill(
    rows: list[dict],
    progress_file: str,
    dry_run: bool = False,
    summarize_fn: Callable[[str, list], str] = summarize_plainly,
    write_fn: Callable[[str, str], None] = write_plain_summary,
    print_fn: Callable[[str], None] = print,
) -> dict:
    progress = _load_progress(progress_file)
    written = errors = skipped = 0
    quota_stopped = False

    for i, row in enumerate(rows):
        shortcode = row["shortcode"]
        if progress.get(shortcode, {}).get("status") == "written":
            skipped += 1
            continue
        if dry_run:
            print_fn(f"[dry-run] would summarize: {shortcode}  {row['title'][:70]}")
            continue
        try:
            text = summarize_fn(row["title"], row["topics"])
            if not text:
                raise ValueError("empty summary returned")
            write_fn(row["page_id"], text)
        except QuotaExhausted as exc:
            print_fn(f"[{i + 1}/{len(rows)}] {shortcode} -> QUOTA STOP ({str(exc)[:110]}); resume later")
            quota_stopped = True
            break
        except Exception as exc:  # noqa: BLE001 - retried on the next run
            errors += 1
            print_fn(f"[{i + 1}/{len(rows)}] {shortcode} -> ERROR (retryable): {exc}")
            continue

        written += 1
        progress[shortcode] = {"status": "written", "plain_summary": text,
                               "timestamp": datetime.now(timezone.utc).isoformat()}
        _save_progress(progress_file, progress)
        print_fn(f"[{i + 1}/{len(rows)}] {shortcode} -> {text[:90]}")

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
    rows = find_rows_needing_plain_summary(pages)
    print(f"{len(rows)} row(s) need a Plain summary")
    if args.limit is not None:
        rows = rows[: args.limit]

    run_backfill(rows, args.progress_file, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
