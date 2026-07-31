"""Notion deep clean (Task 1 of the cleanup pass, see PROGRESS.md).

LOCAL-ONLY. Two independent jobs:

JOB A — find + archive noise rows (never hard-deletes; sets Status to
"🗄 Archived", same as the nightly auto-archive):
  1. Permanent placeholder failures: Title = the caption/transcript placeholder
     AND Status = Photo-manual AND created > 7 days ago AND the placeholder-
     recovery worker's own progress file (recover_placeholders_progress.json)
     shows MAX_RECOVERY_ATTEMPTS exhausted for that shortcode. A row the
     worker hasn't touched yet, or hasn't exhausted its attempts, is NEVER
     archived here — "the recovery worker will still get to it" beats
     "presumed dead."
  2. No Topics AND no Gate keyword AND no Gate resource AND no real
     extraction (Content type "unknown" — the marker
     gemini_pipe.degraded_extraction sets when the model call failed or the
     caption was too thin, so main_point is just a raw caption dump).
  2b. DUPLICATE SHORTCODES: rows sharing a shortcode, keeping the RICHEST one
     (see richness_score — Gate resource first, then a real extraction, then
     topic count, then a real title, then recency) and archiving the rest.
  3. Stale low-signal: Status = Low signal AND created > 30 days ago (the
     nightly auto-archive's own job, but only acts on updated_at in local
     SQLite, which is ephemeral on Render — this is a Notion-side backstop
     using created_time so it also catches rows the nightly job never saw).
  4. Near-duplicate-only, no real content: Topics is EXACTLY ["near-duplicate"]
     AND the page body has no supporting_points AND its Transcript toggle (if
     present) is empty/placeholder — i.e. nothing beyond the auto-added
     near-dup tag and a bare caption.

  Default mode PRINTS every candidate (shortcode, title, reason(s)) and exits
  --  never archives without an explicit --apply flag PLUS your own
  confirmation of the printed list (this script will not decide that for
  you). --apply archives every printed candidate in one pass.

JOB B — --fix-topics: for rows with empty Topics but a real (non-placeholder)
main_point, run ONE lightweight Gemini text call (tags + priority only, no
re-transcription) to assign topic_tags, then recompute Priority the normal
way. Quota-safe (MIN_GEMINI_CALL_SPACING_SECONDS enforced, stops cleanly and
resumably on a 429), never touches placeholder rows.

Usage:
    python scripts/notion_deep_clean.py                     # print candidates only
    python scripts/notion_deep_clean.py --apply              # archive them
    python scripts/notion_deep_clean.py --fix-topics         # Job B only
    python scripts/notion_deep_clean.py --fix-topics --limit 5
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from app.topic_guarantee import UNCATEGORIZED_TAG  # noqa: E402 (after sys.path bootstrap)

logger = logging.getLogger("reelbrain.notion_deep_clean")

PLACEHOLDER_TITLE = "No caption or transcript available."
PHOTO_MANUAL_LABEL = "📷 Photo — manual"
LOW_SIGNAL_LABEL = "🗑 Low signal"
RECOVERY_PROGRESS_FILE = "recover_placeholders_progress.json"
MAX_RECOVERY_ATTEMPTS = 3  # must match scripts/recover_placeholders.py's MAX_ATTEMPTS

PLACEHOLDER_AGE_DAYS = 7
LOW_SIGNAL_AGE_DAYS = 30

# Statuses that mean the pipeline still has work planned for this row. The
# noise conditions must NEVER archive these: "⚠️ Failed — retry" means the
# fetch never succeeded at all, so "main_point is a raw caption dump" really
# means "we never got any content" — archiving would discard a saved reel the
# system never genuinely tried. Photo — manual rows belong to the recovery
# worker (condition 1 owns them, with its own attempts-tracking).
# Live case that forced this: 4 Failed—retry rows matched condition 2
# literally and would have been silently discarded.
PENDING_WORK_STATUSES = {"⚠️ Failed — retry", PHOTO_MANUAL_LABEL, "processing"}

_EMPTY_TRANSCRIPT_MARKERS = {"(no speech detected)", "(unavailable)", ""}

# Single source of truth lives in app.gemini_pipe -- these were 7 copies that
# each had their own idea of "quota error", which is how a billing 429 hid.
from app.gemini_pipe import QUOTA_MARKERS  # noqa: E402  (re-exported)


def _created_days_ago(page: dict) -> float:
    created = page.get("created_time", "")
    try:
        dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return (datetime.now(timezone.utc) - dt).total_seconds() / 86400


def _load_recovery_progress() -> dict:
    path = Path(RECOVERY_PROGRESS_FILE)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _has_exhausted_recovery_attempts(shortcode: str, progress: dict) -> bool:
    entry = progress.get(shortcode)
    if not entry:
        return False  # never attempted -- NOT a permanent failure, don't archive
    if entry.get("status") == "recovered":
        return False
    return entry.get("attempts", 0) >= MAX_RECOVERY_ATTEMPTS


def _page_body_is_thin(client, page_id: str) -> bool:
    """True if the page has no supporting-point bullets and its Transcript
    toggle (if any) is empty/placeholder -- i.e. nothing beyond a bare caption
    plus the auto-added near-duplicate tag."""
    try:
        blocks = client.blocks.children.list(block_id=page_id)["results"]
    except Exception:  # noqa: BLE001 - best-effort; don't let one page's fetch fail the whole scan
        logger.warning("could not fetch blocks for %s", page_id, exc_info=True)
        return False

    from app import notion_writer

    for block in blocks:
        if block.get("type") == "bulleted_list_item":
            return False  # a real supporting_points entry exists
        if block.get("type") == "toggle":
            title = notion_writer._rt_text(block["toggle"].get("rich_text"))
            if title == "Transcript":
                children = client.blocks.children.list(block_id=block["id"])["results"]
                text = ""
                for child in children:
                    if child.get("type") == "paragraph":
                        text = notion_writer._rt_text(child["paragraph"].get("rich_text"))
                        break
                if text not in _EMPTY_TRANSCRIPT_MARKERS:
                    return False  # a real transcript exists
    return True


def richness_score(page: dict) -> tuple:
    """How much real content a row carries, for picking the survivor among
    duplicate shortcodes. Ordered by what's most expensive to recreate:
    an attached Gate resource (a DM'd link you'd have to re-fetch) outranks
    everything, then a real extraction, then topics, then a real title, then
    recency as the final tiebreak."""
    from app import notion_writer

    props = page.get("properties", {})
    fields = notion_writer.extract_saves_fields(page)
    digest = notion_writer.extract_digest_fields(page)
    content_type = ((props.get("Content type") or {}).get("select") or {}).get("name", "")
    return (
        1 if (props.get("Gate resource") or {}).get("url") else 0,
        1 if content_type not in ("unknown", "") else 0,
        len(digest["topics"]),
        1 if fields["title"] and fields["title"] != PLACEHOLDER_TITLE else 0,
        1 if fields["gate_keyword"] else 0,
        page.get("last_edited_time", ""),
    )


def find_duplicate_shortcode_losers(pages: list[dict]) -> list[tuple[str, dict, dict]]:
    """Rows sharing a shortcode, minus the richest one per shortcode.
    Returns [(shortcode, loser_page, winner_page), ...] so the caller can
    report exactly what's being kept in each case."""
    from app import notion_writer

    by_shortcode: dict[str, list[dict]] = defaultdict(list)
    for page in pages:
        shortcode = notion_writer.extract_saves_fields(page)["shortcode"]
        if shortcode:
            by_shortcode[shortcode].append(page)

    losers = []
    for shortcode, group in by_shortcode.items():
        if len(group) < 2:
            continue
        ranked = sorted(group, key=richness_score, reverse=True)
        winner = ranked[0]
        for loser in ranked[1:]:
            losers.append((shortcode, loser, winner))
    return losers


def find_archive_candidates(
    pages: list[dict], recovery_progress: dict, *, now_check_body_fn=_page_body_is_thin, client=None,
) -> list[dict]:
    """Returns [{"shortcode", "title", "reasons": [...], "page_id": ...}], one
    entry per row that matches at least one of the four conditions (a row
    matching more than one lists every reason, never duplicated).

    Keyed by PAGE ID, not shortcode -- duplicate-shortcode rows are themselves
    one of the conditions, so two different pages can share a shortcode and
    must stay distinguishable."""
    from app import notion_writer

    candidates: dict[str, dict] = {}

    def _add(page_id: str, shortcode: str, title: str, reason: str) -> None:
        entry = candidates.setdefault(
            page_id, {"page_id": page_id, "shortcode": shortcode, "title": title, "reasons": []})
        if reason not in entry["reasons"]:
            entry["reasons"].append(reason)

    # Condition 3 (NEW): duplicate shortcodes -- keep the richest, archive the rest.
    for shortcode, loser, winner in find_duplicate_shortcode_losers(pages):
        loser_fields = notion_writer.extract_saves_fields(loser)
        winner_fields = notion_writer.extract_saves_fields(winner)
        _add(loser.get("id", ""), shortcode, loser_fields["title"],
             f"duplicate shortcode -- keeping the richer row "
             f"(kept page {winner_fields['page_id'][:8]}, title {winner_fields['title'][:40]!r})")

    for page in pages:
        fields = notion_writer.extract_saves_fields(page)
        shortcode = fields["shortcode"]
        if not shortcode:
            continue
        digest = notion_writer.extract_digest_fields(page)
        props = page.get("properties", {})
        gate_resource = (props.get("Gate resource") or {}).get("url")
        title = fields["title"] or digest["title"]
        topics = digest["topics"]
        status_label = fields["status_label"]
        value_score = digest["value_score"]
        gate_keyword = fields["gate_keyword"]
        content_type = ((props.get("Content type") or {}).get("select") or {}).get("name", "")
        age_days = _created_days_ago(page)

        # Condition 1: permanent placeholder failure
        if (
            title == PLACEHOLDER_TITLE
            and status_label == PHOTO_MANUAL_LABEL
            and age_days > PLACEHOLDER_AGE_DAYS
            and _has_exhausted_recovery_attempts(shortcode, recovery_progress)
        ):
            _add(fields["page_id"], shortcode, title, f"permanent placeholder failure ({MAX_RECOVERY_ATTEMPTS} recovery "
                                    f"attempts exhausted, {age_days:.0f}d old)")

        # Condition 2: no topics, no gate of any kind, and no REAL extraction --
        # i.e. main_point is just a raw caption dump. content_type "unknown" is
        # the authoritative marker for that: gemini_pipe.degraded_extraction
        # sets it whenever the extraction call failed or the caption was too
        # thin, and a genuine extraction always picks one of the 6 real types.
        #
        # DELIBERATELY excludes placeholder rows: every unrecovered placeholder
        # has topics=[] AND content_type "unknown" by construction, so a literal
        # reading would catch EVERY one immediately -- defeating condition 1's
        # explicit "skip rows with recovery attempts remaining" carve-out.
        # Condition 1 is authoritative for placeholder rows.
        if (
            title != PLACEHOLDER_TITLE
            and status_label not in PENDING_WORK_STATUSES
            and not topics
            and not gate_keyword
            and not gate_resource
            and content_type in ("unknown", "")
        ):
            _add(fields["page_id"], shortcode, title, "no topics, no gate, raw caption dump (content_type unknown)")

        # Condition 3: stale low-signal
        if status_label == LOW_SIGNAL_LABEL and age_days > LOW_SIGNAL_AGE_DAYS:
            _add(fields["page_id"], shortcode, title, f"stale low-signal ({age_days:.0f}d old)")

        # Condition 4: near-duplicate-only, no real content -- also excludes
        # placeholder rows, same reasoning as condition 2. In practice several
        # placeholder rows embed as near-duplicates of EACH OTHER (they're all
        # the same generic fallback text), so a literal reading would catch
        # them here too and, again, defeat condition 1's attempts-tracking.
        if title != PLACEHOLDER_TITLE and topics == ["near-duplicate"] and client is not None:
            if now_check_body_fn(client, fields["page_id"]):
                _add(fields["page_id"], shortcode, title, "near-duplicate-only, no real content")

    return sorted(candidates.values(), key=lambda c: (c["shortcode"], c["page_id"]))


def apply_archive(candidates: list[dict]) -> int:
    """Archives by PAGE ID, never by shortcode lookup.

    This matters specifically because of the duplicate-shortcode condition:
    find_page_by_shortcode returns the FIRST match, which for a duplicate pair
    could be the row we decided to KEEP -- archiving the winner and leaving the
    loser. Targeting the page id we actually classified removes that whole
    class of error.

    The local-SQLite mirror is only updated for non-duplicate archives: when a
    duplicate loser is archived its shortcode still has a live winner row, so
    marking that shortcode 'archived' locally would misrepresent the survivor."""
    from app import notion_writer, store

    count = 0
    for c in candidates:
        page_id = c.get("page_id")
        if not page_id:
            logger.warning("archive: no page_id for %s, skipping", c["shortcode"])
            continue
        is_duplicate_loser = any(r.startswith("duplicate shortcode") for r in c["reasons"])
        try:
            notion_writer.set_status(page_id, "archived")
        except Exception:  # noqa: BLE001 - report and continue; one bad page must not sink the pass
            logger.exception("archive: Notion write failed for page %s (%s)", page_id, c["shortcode"])
            continue
        if not is_duplicate_loser:
            try:
                store.update_save(c["shortcode"], status="archived")
            except Exception:  # noqa: BLE001 - local SQLite mirror is best-effort
                pass
        count += 1
    return count


# --- Job B: assign topics to empty-topic, real-content rows ---------------------


class _QuotaWatcher(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.quota_hit = False

    def emit(self, record: logging.LogRecord) -> None:
        text = record.getMessage()
        if record.exc_info and record.exc_info[1] is not None:
            text += " " + str(record.exc_info[1])
        gemini_pipe.note_gemini_failure(text)
        if any(m in text for m in QUOTA_MARKERS):
            self.quota_hit = True


_TAG_ONLY_PROMPT = """Assign 3-6 lowercase-kebab-case topic tags for this saved item, based only on \
its title below. Prefer these existing tags when they genuinely fit: {taxonomy}. Only introduce a new \
tag if none fit. Return ONLY the tags as a comma-separated list, nothing else.

Title: {title}
"""


def suggest_tags(title: str, taxonomy: list[str]) -> list[str]:
    from google import genai

    from app import gemini_pipe

    client = genai.Client(api_key=gemini_pipe.GEMINI_API_KEY)
    gemini_pipe._enforce_gemini_call_spacing()
    response = client.models.generate_content(
        model=gemini_pipe.GEMINI_MODEL,
        contents=[_TAG_ONLY_PROMPT.format(title=title, taxonomy=", ".join(taxonomy) or "(none yet)")],
    )
    raw = (response.text or "").strip()
    tags = [re.sub(r"[^a-z0-9-]", "", t.strip().lower()) for t in raw.split(",")]
    return [t for t in tags if t][:6]


def title_is_bare_permalink(title: str, shortcode: str) -> bool:
    """A row whose Title is still its own Instagram permalink never got a real
    extraction — the fetch failed before anything could be summarized."""
    return bool(title) and title.startswith("http") and shortcode in title


def find_topicless_rows(pages: list[dict]) -> list[dict]:
    """Rows with empty Topics that have a REAL main_point worth tagging.

    Excludes every row whose "title" isn't genuine extracted prose:
      - the literal placeholder, and
      - a bare permalink (Failed—retry rows never got extracted at all).
    REGRESSION (live, twice now): tagging such a row makes Gemini describe the
    PLACEHOLDER or the URL rather than the reel — it produced junk tags like
    "captions"/"transcripts" for placeholders and
    "instagram"/"reels"/"short-form-video" for a permalink. Both had to be
    reverted by hand. Those rows belong to recover_placeholders.py, which can
    fetch real content first."""
    from app import notion_writer

    rows = []
    for page in pages:
        fields = notion_writer.extract_saves_fields(page)
        if not fields["shortcode"] or fields["title"] == PLACEHOLDER_TITLE:
            continue
        if title_is_bare_permalink(fields["title"], fields["shortcode"]):
            continue
        digest = notion_writer.extract_digest_fields(page)
        # A row whose ONLY topic is the Phase-H "uncategorized" placeholder
        # still needs real topics. Without this, enforce_topics' fallback would
        # permanently hide those rows from this pass -- the sweep that makes
        # them non-orphans would also be the thing that stops them ever being
        # properly tagged.
        meaningful = [t for t in digest["topics"] if t != UNCATEGORIZED_TAG]
        if meaningful:
            continue
        if not fields["title"]:
            continue
        rows.append({"shortcode": fields["shortcode"], "page_id": fields["page_id"], "title": fields["title"]})
    return rows


def fix_topics(rows: list[dict], taxonomy: list[str], limit: Optional[int] = None,
               suggest_fn=suggest_tags, print_fn=print) -> dict:
    from app import gemini_pipe, notion_writer

    if limit:
        rows = rows[:limit]

    watcher = _QuotaWatcher()
    gemini_logger = logging.getLogger("reelbrain.gemini")
    gemini_logger.addHandler(watcher)
    fixed = []
    quota_stopped = False
    try:
        for row in rows:
            try:
                tags = suggest_fn(row["title"], taxonomy)
            except Exception as exc:  # noqa: BLE001
                if any(m in str(exc) for m in QUOTA_MARKERS) or watcher.quota_hit:
                    print_fn(f"QUOTA STOP at {row['shortcode']} — re-run to resume")
                    quota_stopped = True
                    break
                print_fn(f"ERROR (skip, retryable): {row['shortcode']} — {exc}")
                continue
            if not tags:
                print_fn(f"NO TAGS returned for {row['shortcode']} — skipping")
                continue
            priority = gemini_pipe.compute_priority(tags, 3)  # value_score already "3" for these rows
            notion_writer._client().pages.update(
                page_id=row["page_id"],
                properties={
                    "Topics": {"multi_select": [{"name": t} for t in tags]},
                    "Priority": {"select": {"name": priority}},
                },
            )
            fixed.append(row["shortcode"])
            print_fn(f"TAGGED: {row['shortcode']} -> {tags} (priority={priority})")
    finally:
        gemini_logger.removeHandler(watcher)

    return {"fixed": fixed, "quota_stopped": quota_stopped, "total_candidates": len(rows)}


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="actually archive the printed candidates")
    parser.add_argument("--fix-topics", action="store_true", help="Job B only: assign tags to topic-less rows")
    parser.add_argument("--limit", type=int, default=None, help="--fix-topics: cap how many rows to process")
    args = parser.parse_args()

    from app import notion_writer

    pages = notion_writer.find_saves_pages_since("1970-01-01T00:00:00")
    print(f"scanned {len(pages)} Saves rows\n")

    if args.fix_topics:
        rows = find_topicless_rows(pages)
        from app import store
        taxonomy = store.get_taxonomy()
        print(f"found {len(rows)} topic-less row(s) with a real main_point\n")
        result = fix_topics(rows, taxonomy, limit=args.limit)
        print(f"\ndone: {len(result['fixed'])}/{result['total_candidates']} fixed, "
              f"quota_stopped={result['quota_stopped']}")
        return

    client = notion_writer._client()
    recovery_progress = _load_recovery_progress()
    candidates = find_archive_candidates(pages, recovery_progress, client=client)

    if not candidates:
        print("no archive candidates found.")
        return

    print(f"{len(candidates)} archive candidate(s):\n")
    for c in candidates:
        print(f"  {c['shortcode']:14} {c['title'][:80]}")
        for reason in c["reasons"]:
            print(f"      - {reason}")

    if not args.apply:
        print(f"\n{len(candidates)} candidate(s) listed above -- NOT archived. "
              "Re-run with --apply to archive them.")
        return

    count = apply_archive(candidates)
    print(f"\narchived {count}/{len(candidates)} row(s).")


if __name__ == "__main__":
    main()
