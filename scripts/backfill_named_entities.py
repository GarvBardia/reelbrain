"""Backfill "Named entities" for existing Saves rows (Phase G).

LOCAL-ONLY. Re-derives the specific tools/products each reel named, from
content ALREADY STORED in Notion (Title + Supporting points + Transcript) via
one small text-only Gemini call. Never re-fetches the reel.

Why this exists: named_entities has been extracted since the research-context
work but was never persisted, so every pre-existing row has an empty field --
and /attach's most specific matching signal scores against nothing. This fills
in the history so matching improves immediately rather than only for new rows.

Skips placeholder / bare-permalink rows for the reason learned repeatedly (see
PROGRESS.md): there's no real content to extract entities from, so Gemini would
invent them from the placeholder text or the URL.

Quota-safe (MIN_GEMINI_CALL_SPACING_SECONDS enforced), resumable, --dry-run.

Usage:
    python scripts/backfill_named_entities.py --dry-run
    python scripts/backfill_named_entities.py --limit 10
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from scripts.notion_deep_clean import PLACEHOLDER_TITLE, title_is_bare_permalink

DEFAULT_PROGRESS_FILE = "backfill_named_entities_progress.json"
# Single source of truth lives in app.gemini_pipe -- these were 7 copies that
# each had their own idea of "quota error", which is how a billing 429 hid.
from app.gemini_pipe import QUOTA_MARKERS  # noqa: E402  (re-exported)
MAX_ENTITIES = 8

_PROMPT = """List the SPECIFIC, look-up-able things named in this saved item: exact \
tool/product/service names, named techniques or frameworks, named people, specific \
stated claims. These are NOT broad categories -- "developer-tools" is a category and \
must NOT be listed; "Firecrawl", "GSAP", "Claude Code" are entities and should be.

Rules:
- Return ONLY a SEMICOLON-separated list, nothing else. Use semicolons, never
  commas -- entity names themselves contain commas (e.g. "$4,000 a month").
- Prefer proper names of tools/products/services over restated claims.
- Return AT MOST {max_entities}.
- If the text names nothing specific, return exactly: NONE
- Never invent an entity to fill the list out.

Title: {title}
Details: {details}
"""


class QuotaExhausted(Exception):
    pass


def _page_body_text(page_id: str, limit_words: int = 300) -> str:
    """Supporting points + transcript already stored on the page — the raw
    material for re-deriving entities without re-fetching the reel."""
    from app import notion_writer

    client = notion_writer._client()
    parts: list[str] = []
    try:
        for block in client.blocks.children.list(block_id=page_id).get("results", []):
            btype = block.get("type")
            if btype in ("bulleted_list_item", "numbered_list_item", "callout"):
                parts.append(notion_writer._rt_text(block[btype].get("rich_text")))
            elif btype == "toggle":
                title = notion_writer._rt_text(block["toggle"].get("rich_text"))
                if title in ("Transcript", "Raw caption") and block.get("has_children"):
                    for child in client.blocks.children.list(block_id=block["id"]).get("results", []):
                        if child.get("type") == "paragraph":
                            parts.append(notion_writer._rt_text(child["paragraph"].get("rich_text")))
    except Exception:  # noqa: BLE001 - body is a bonus; the title alone still works
        pass
    return " ".join(" ".join(parts).split()[:limit_words])


def find_rows_needing_entities(pages: list[dict]) -> list[dict]:
    from app import notion_writer

    rows = []
    for page in pages:
        fields = notion_writer.extract_saves_fields(page)
        shortcode, title = fields["shortcode"], fields["title"]
        if not shortcode or not title:
            continue
        if title == PLACEHOLDER_TITLE or title_is_bare_permalink(title, shortcode):
            continue
        if fields.get("named_entities"):
            continue
        rows.append({"shortcode": shortcode, "page_id": fields["page_id"], "title": title})
    return rows


def derive_entities(title: str, details: str) -> list[str]:
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
            contents=[_PROMPT.format(title=title, details=details or "(none)",
                                     max_entities=MAX_ENTITIES)],
        )
    except Exception as exc:  # noqa: BLE001
        gemini_pipe.note_gemini_failure(exc)
        if any(m in str(exc) for m in QUOTA_MARKERS):
            raise QuotaExhausted(str(exc)) from exc
        raise
    raw = (response.text or "").strip()
    if raw.upper().startswith("NONE"):
        return []
    # Split on semicolons ONLY: an entity can legitimately contain a comma
    # ("$4,000 a month"), and splitting on commas shredded exactly that live.
    entities = [re.sub(r"\s+", " ", e).strip(" .\"'") for e in raw.split(";")]
    return [e[:100] for e in entities if e and len(e) > 1][:MAX_ENTITIES]


def write_entities(page_id: str, entities: list[str]) -> None:
    from app import notion_writer

    # Sanitize through the same helper the live write path uses -- Notion
    # rejects a comma in ANY multi_select option and fails the whole write.
    names = notion_writer._multi_select_names(entities, limit=MAX_ENTITIES)
    notion_writer._client().pages.update(
        page_id=page_id,
        properties={"Named entities": {"multi_select": [{"name": n} for n in names]}},
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


def run_backfill(
    rows: list[dict],
    progress_file: str,
    dry_run: bool = False,
    derive_fn: Callable[[str, str], list[str]] = derive_entities,
    body_fn: Callable[[str], str] = _page_body_text,
    write_fn: Callable[[str, list], None] = write_entities,
    print_fn: Callable[[str], None] = print,
) -> dict:
    progress = _load(progress_file)
    written = errors = skipped = none_found = 0
    quota_stopped = False

    for i, row in enumerate(rows):
        shortcode = row["shortcode"]
        if progress.get(shortcode, {}).get("status") in ("written", "none"):
            skipped += 1
            continue
        if dry_run:
            print_fn(f"[dry-run] would derive entities for: {shortcode}  {row['title'][:60]}")
            continue
        try:
            entities = derive_fn(row["title"], body_fn(row["page_id"]))
            if entities:
                write_fn(row["page_id"], entities)
        except QuotaExhausted as exc:
            print_fn(f"[{i + 1}/{len(rows)}] {shortcode} -> QUOTA STOP ({str(exc)[:100]}); resume later")
            quota_stopped = True
            break
        except Exception as exc:  # noqa: BLE001 - retried next run
            errors += 1
            print_fn(f"[{i + 1}/{len(rows)}] {shortcode} -> ERROR (retryable): {exc}")
            continue

        status = "written" if entities else "none"
        if entities:
            written += 1
        else:
            none_found += 1
        progress[shortcode] = {"status": status, "entities": entities,
                               "timestamp": datetime.now(timezone.utc).isoformat()}
        _save(progress_file, progress)
        print_fn(f"[{i + 1}/{len(rows)}] {shortcode} -> {entities or '(none named)'}")

    summary = {"written": written, "none_found": none_found, "errors": errors,
               "skipped": skipped, "quota_stopped": quota_stopped, "total_rows": len(rows)}
    print_fn(f"\ndone: {written} written, {none_found} had no named entities, {errors} errors, "
             f"{skipped} skipped, quota_stopped={quota_stopped}, of {len(rows)} rows")
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
    rows = find_rows_needing_entities(pages)
    print(f"{len(rows)} row(s) need Named entities")
    if args.limit is not None:
        rows = rows[: args.limit]

    run_backfill(rows, args.progress_file, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
