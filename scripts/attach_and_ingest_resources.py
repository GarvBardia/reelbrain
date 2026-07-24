"""Smart-attach a file of DM'd resource URLs to their reels, then ingest ALL of
them into the Obsidian vault.

LOCAL-ONLY (never deployed to Render) — reuses requirements-local.txt fetchers
(beautifulsoup4/pypdf). Two independent parts per resource URL:

PART 1 — smart attach (reuses the exact /attach machinery, never a blind write):
  1. Fetch the resource's real content via scripts/ingest_resources.py's
     per-type fetchers (GitHub README API, Google Docs mobilebasic, Drive,
     PDF, generic web).
  2. Score it against every currently-open gate row
     (store.get_attach_candidates) with app/attach_matching.py — the same
     gate_keyword weighting / stopwords / platform-suffix stripping /attach
     itself uses.
  3. Attach ONLY on a confident, unambiguous match (see CONFIDENT_ATTACH_SCORE
     below) via store.resolve_attachable_by_shortcode -> app.main._commit_attach
     -> attach_audit.record — the identical Notion-write + audit + status-flip
     path /attach/confirm runs. Never a blind write.
  4. Anything less confident -> UNMATCHED_RESOURCES.md (URL, what the resource
     is, top-3 candidate reels with score + main_point) for a manual decision.
  5. A resource whose URL is already a Gate resource on some row is NEVER
     re-attached -> reported as ALREADY_ATTACHED (and linked to that reel in
     Part 2).

WHY the confident bar is STRICTER than /attach's MIN_SCORE_THRESHOLD (1): this
script AUTO-attaches, whereas /attach only ever returns candidates for a human
to confirm. A single generic shared word (score 1) is exactly the signal that
caused the real cross-attachment incident the /attach redesign fixed (see
PROGRESS.md). So a confident auto-attach requires the candidate's OWN
deliberately-chosen gate_keyword to appear verbatim in the resource
(score >= GATE_KEYWORD_MATCH_WEIGHT) AND a strict win over the runner-up (no
two-keyword ambiguity). Everything else is a human's call.

PART 2 — vault ingestion for EVERY resource (matched or not):
  Fetch + Gemini summary/takeaways/topic_tags/suggested_action ->
  resources/{slug}.md with the requested frontmatter. Matched resources also
  carry source_shortcode/source_reel so a subsequent sync_to_obsidian run
  renders the reel-side "## Attached Resource" link idempotently (the
  established pattern — sync fully regenerates reel notes, so writing that
  section directly would be clobbered). Unmatched resources are listed in a
  delimited "## Unlinked resources" block in _index.md. Genuinely unreadable
  resources are flagged in UNMATCHED_RESOURCES.md, never fabricated.

EXECUTION: MIN_GEMINI_CALL_SPACING_SECONDS is enforced inside every Gemini
call. On a Gemini 429/quota the run stops cleanly, saves progress to
resource_attach_progress.json, and reports done-vs-remaining; re-running
resumes. Dry-run does Part 1 fully (network only, no writes, no attach) and
prints the Part-2 plan WITHOUT calling Gemini (so a preview never burns the
daily quota).

Usage:
    python scripts/attach_and_ingest_resources.py --file resource.txt --dry-run
    python scripts/attach_and_ingest_resources.py --file resource.txt        # live
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from app import attach_matching

logger = logging.getLogger("reelbrain.attach_and_ingest")

DEFAULT_PROGRESS_FILE = "resource_attach_progress.json"
UNMATCHED_REPORT = "UNMATCHED_RESOURCES.md"
FETCH_SPACING_SECONDS = 1.0
QUOTA_MARKERS = ("429", "RESOURCE_EXHAUSTED")

# A confident AUTO-attach requires the candidate's own gate_keyword to appear
# verbatim in the resource (attach_matching scores that at GATE_KEYWORD_MATCH_WEIGHT)
# AND a strict win over the runner-up. See the module docstring for why this is
# stricter than attach_matching.MIN_SCORE_THRESHOLD.
CONFIDENT_ATTACH_SCORE = attach_matching.GATE_KEYWORD_MATCH_WEIGHT

# Gate keywords too generic to trust for an AUTO-attach: content-free marketing
# words that appear on nearly ANY page, so their presence in a resource is not
# real evidence it's the right resource (the dry-run caught "FREE" matching an
# AI-job-search guide to an unrelated "Everything Claude" reel — exactly the
# generic-overlap cross-attachment the /attach redesign fixed). A row whose
# gate_keyword is one of these is never auto-attached — it goes to the manual
# UNMATCHED report instead. Specific keywords ("PROMPTS", "STACK", "VIDEO",
# "agent") still qualify.
GENERIC_GATE_KEYWORDS = frozenset({
    "free", "guide", "guides", "tool", "tools", "link", "links", "get", "info",
    "dm", "download", "access", "now", "new", "best", "tips", "the", "info",
    "yes", "go", "start", "here", "this", "help", "more",
})


def _keyword_in_text(keyword: str, text: str) -> bool:
    """Whole-word, case-insensitive — same contract as attach_matching's own
    gate_keyword check, reproduced locally to avoid depending on a private name."""
    kw = (keyword or "").strip().lower()
    return bool(kw) and re.search(r"\b" + re.escape(kw) + r"\b", (text or "").lower()) is not None

_SOURCE_TYPE = {
    "github_repo": "github",
    "google_doc": "gdoc",
    "google_drive_file": "drive",
    "pdf": "pdf",
    "web_article": "web",
}

_INDEX_START = "<!-- UNLINKED-RESOURCES:START (managed by attach_and_ingest_resources.py) -->"
_INDEX_END = "<!-- UNLINKED-RESOURCES:END -->"


# --- URL parsing / normalization ------------------------------------------------


def read_urls(path: Path) -> list[str]:
    urls = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line.startswith("http"):
            urls.append(line)
    return urls


def normalize_url(url: str) -> str:
    """Identity key for dedup + already-attached comparison: scheme+host+path,
    lowercased, query/fragment dropped (the resource.txt URLs carry per-share
    fbclid/mcp_token junk that differs between otherwise-identical links)."""
    p = urlparse(url)
    host = (p.netloc or "").lower()
    path = (p.path or "").rstrip("/").lower()
    return f"{host}{path}"


def dedupe(urls: list[str]) -> tuple[list[str], dict[str, int]]:
    """First occurrence of each normalized URL wins; returns (unique_urls,
    {normalized: duplicate_count})."""
    seen: dict[str, str] = {}
    dup_counts: dict[str, int] = {}
    for url in urls:
        key = normalize_url(url)
        if key in seen:
            dup_counts[key] = dup_counts.get(key, 0) + 1
        else:
            seen[key] = url
    return list(seen.values()), dup_counts


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").strip().lower()).strip("-")
    return s[:50] or "resource"


def resource_slug(fetched_title: str, url: str) -> str:
    """Stable, collision-resistant filename stem: title slug + short hash of the
    normalized URL (so two different resources sharing a title never collide,
    and re-runs land on the same file)."""
    digest = hashlib.sha1(normalize_url(url).encode("utf-8")).hexdigest()[:6]
    return f"{_slug(fetched_title)}-{digest}"


def derive_title(kind: str, url: str, content: str) -> str:
    if kind == "github_repo":
        parts = [p for p in urlparse(url).path.split("/") if p]
        if len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}"
    for line in (content or "").splitlines():
        line = line.strip().lstrip("#").strip()
        if len(line) >= 8:
            return line[:120]
    host = urlparse(url).netloc
    return host or "resource"


# --- Gemini 429 detection (extraction degrades instead of raising) --------------


class _QuotaWatcher(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.quota_hit = False

    def emit(self, record: logging.LogRecord) -> None:
        text = record.getMessage()
        if record.exc_info and record.exc_info[1] is not None:
            text += " " + str(record.exc_info[1])
        if any(m in text for m in QUOTA_MARKERS):
            self.quota_hit = True


# --- Notion: URLs already attached as a Gate resource ---------------------------


def already_attached_map() -> dict[str, str]:
    """normalized Gate-resource URL -> shortcode it's attached to, across every
    row. Lets us (a) never re-attach and (b) link an already-attached resource
    to its reel in Part 2."""
    from scripts.ingest_resources import find_gated_resources

    mapping: dict[str, str] = {}
    for entry in find_gated_resources():
        mapping[normalize_url(entry["resource_url"])] = entry["shortcode"]
    return mapping


# --- Part 1: matching -----------------------------------------------------------


def classify_match(ranked: list[dict], resource_text: str) -> tuple[str, Optional[dict]]:
    """('attach', top_candidate) ONLY on a confident, unambiguous match:
      - top score >= CONFIDENT_ATTACH_SCORE and a STRICT win over the runner-up
        (no two-keyword ambiguity), AND
      - the top candidate's gate_keyword is SPECIFIC (not in
        GENERIC_GATE_KEYWORDS) and appears verbatim in the resource text.
    Everything else -> ('unmatched', None) for the manual report. The
    specific-keyword requirement is what stops a generic keyword like "FREE"
    from auto-attaching an unrelated resource."""
    if not ranked:
        return "unmatched", None
    top = ranked[0]
    runner_up = ranked[1]["match_score"] if len(ranked) > 1 else -1
    keyword = (top.get("gate_keyword") or "").strip().lower()
    specific_keyword_hit = (
        keyword
        and keyword not in GENERIC_GATE_KEYWORDS
        and _keyword_in_text(keyword, resource_text)
    )
    if (
        specific_keyword_hit
        and top["match_score"] >= CONFIDENT_ATTACH_SCORE
        and top["match_score"] > runner_up
    ):
        return "attach", top
    return "unmatched", None


# --- vault note building --------------------------------------------------------


def _yaml_list(values: list[str]) -> str:
    return "[" + ", ".join(values) + "]"


def build_resource_note(
    *, url: str, fetched_title: str, source_type: str, extraction,
    matched_shortcode: Optional[str], reel_stem: Optional[str], date_ingested: str,
) -> str:
    lines = ["---"]
    lines.append(f"url: {url}")
    lines.append(f'fetched_title: "{fetched_title.replace(chr(34), chr(39))}"')
    lines.append(f"topic_tags: {_yaml_list(extraction.topic_tags)}")
    action = extraction.suggested_action or "none — informational"
    lines.append(f'suggested_action: "{action.replace(chr(34), chr(39))}"')
    lines.append(f"source_type: {source_type}")
    lines.append(f"date_ingested: {date_ingested}")
    if matched_shortcode:
        # source_shortcode/topics_plain make this note first-class to
        # app/obsidian_sync.py, which renders the reel-side "## Attached
        # Resource" link on its next run (idempotent; sync regenerates reel
        # notes, so writing that section here would be overwritten).
        lines.append(f"source_shortcode: {matched_shortcode}")
    if reel_stem:
        lines.append(f'source_reel: "[[reels/{reel_stem}]]"')
    if extraction.topic_tags:
        lines.append(f"topics_plain: {', '.join(extraction.topic_tags)}")
        lines.append("topics:")
        for topic in extraction.topic_tags:
            lines.append(f'  - "[[topics/{_slug(topic)}]]"')
    lines.append("---")
    lines.append("")
    lines.append(f"# {fetched_title}")
    lines.append("")
    lines.append(f"Source: <{url}>")
    if reel_stem:
        lines.append(f"From reel: [[reels/{reel_stem}]]")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(extraction.summary)
    if extraction.key_takeaways:
        lines.append("")
        lines.append("## Key takeaways")
        lines.append("")
        for point in extraction.key_takeaways:
            lines.append(f"- {point}")
    if action and action.lower() != "none — informational":
        lines.append("")
        lines.append("## Do")
        lines.append("")
        lines.append(action)
    return "\n".join(lines).rstrip() + "\n"


def update_index_unlinked(index_path: Path, entries: list[dict]) -> None:
    """Maintain a delimited '## Unlinked resources' block in _index.md, its own
    markers independent of obsidian_sync's AUTO block (so both coexist). Idempotent."""
    block_lines = [_INDEX_START, "", "## Unlinked resources", ""]
    if entries:
        for e in entries:
            block_lines.append(f"- [[resources/{e['stem']}]] — <{e['url']}>")
    else:
        block_lines.append("_(none)_")
    block_lines.append("")
    block_lines.append(_INDEX_END)
    block = "\n".join(block_lines)

    existing = index_path.read_text(encoding="utf-8") if index_path.exists() else ""
    if _INDEX_START in existing and _INDEX_END in existing:
        new = re.sub(
            re.escape(_INDEX_START) + r".*?" + re.escape(_INDEX_END),
            block, existing, flags=re.DOTALL,
        )
    else:
        new = existing.rstrip() + "\n\n" + block + "\n"
    index_path.write_text(new, encoding="utf-8")


# --- report ---------------------------------------------------------------------


def render_unmatched_report(unmatched: list[dict], already: list[dict], unreadable: list[dict]) -> str:
    lines = ["# Unmatched / already-attached / unreadable resources", ""]
    lines.append(f"_Generated {datetime.now(timezone.utc).date().isoformat()} by "
                 "scripts/attach_and_ingest_resources.py_")
    lines.append("")

    lines.append("## Unmatched — decide manually, then attach via the Shortcut")
    lines.append("")
    if not unmatched:
        lines.append("_(none)_")
    for u in unmatched:
        lines.append(f"### {u['fetched_title']}")
        lines.append(f"- URL: <{u['url']}>")
        lines.append(f"- What it is: {u['what']}")
        if u["candidates"]:
            lines.append("- Top candidate reels:")
            for c in u["candidates"]:
                lines.append(f"  - `{c['shortcode']}` (score {c['match_score']}) — {c['title'][:90]}")
        else:
            lines.append("- Top candidate reels: none scored above threshold")
        lines.append("")

    lines.append("## Already attached — skipped (resource URL already on a row)")
    lines.append("")
    if not already:
        lines.append("_(none)_")
    for a in already:
        lines.append(f"- <{a['url']}> — already attached to `{a['shortcode']}`")
    lines.append("")

    lines.append("## Unreadable — no fabrication, manual review needed")
    lines.append("")
    if not unreadable:
        lines.append("_(none)_")
    for r in unreadable:
        lines.append(f"- <{r['url']}> — {r['reason']}")
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# --- orchestration --------------------------------------------------------------


def _load_progress(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_progress(path: Path, progress: dict) -> None:
    path.write_text(json.dumps(progress, indent=2, sort_keys=True), encoding="utf-8")


def run(
    urls: list[str],
    vault: Path,
    progress_file: Path,
    *,
    dry_run: bool,
    candidates: list[dict],
    attached_map: dict[str, str],
    reel_stems: dict[str, str],
    taxonomy: list[str],
    fetch_fn: Callable[[str, str], tuple[Optional[str], Optional[str]]],
    classify_fn: Callable[[str], str],
    extract_fn,
    commit_fn: Callable[[str, str], bool],
    audit_fn: Callable[..., None],
    sleep_fn: Callable[[float], None] = time.sleep,
    print_fn: Callable[..., None] = print,
) -> dict:
    """Injectable core so tests never touch the network, Gemini, Notion, or the
    clock. Returns a structured summary."""
    progress = _load_progress(progress_file)

    attached: list[dict] = []
    unmatched: list[dict] = []
    already: list[dict] = []
    unreadable: list[dict] = []
    ingested: list[dict] = []
    unlinked_index: list[dict] = []
    degraded: list[str] = []
    quota_stopped = False

    watcher = _QuotaWatcher()
    gemini_logger = logging.getLogger("reelbrain.gemini")
    gemini_logger.addHandler(watcher)
    try:
        for url in urls:
            key = normalize_url(url)
            done = progress.get(key, {})
            if done.get("ingest_status") in ("ingested", "unreadable"):
                continue

            kind = classify_fn(url)
            source_type = _SOURCE_TYPE.get(kind, "web")
            content, error = fetch_fn(url, kind)
            sleep_fn(FETCH_SPACING_SECONDS)

            if content is None:
                print_fn(f"UNREADABLE: {url} — {error}")
                unreadable.append({"url": url, "reason": error})
                progress[key] = {**done, "ingest_status": "unreadable", "url": url, "error": error}
                if not dry_run:
                    _save_progress(progress_file, progress)
                continue

            fetched_title = derive_title(kind, url, content)
            description = " ".join(content.split()[:500])

            # --- PART 1: match ---
            matched_shortcode: Optional[str] = None
            if key in attached_map:
                matched_shortcode = attached_map[key]
                already.append({"url": url, "shortcode": matched_shortcode})
                print_fn(f"ALREADY-ATTACHED: {url} -> {matched_shortcode}")
            else:
                ranked = attach_matching.rank_candidates(fetched_title, description, candidates)
                decision, top = classify_match(ranked, f"{fetched_title} {description}")
                if decision == "attach":
                    matched_shortcode = top["shortcode"]
                    if dry_run:
                        print_fn(f"WOULD ATTACH: {url} -> {matched_shortcode} "
                                 f"(score {top['match_score']}) — {top['title'][:70]}")
                        attached.append({"url": url, "shortcode": matched_shortcode, "score": top["match_score"]})
                    else:
                        if commit_fn(matched_shortcode, url):
                            audit_fn(None, url, "confirmed", shortcode=matched_shortcode)
                            attached.append({"url": url, "shortcode": matched_shortcode, "score": top["match_score"]})
                            print_fn(f"ATTACHED: {url} -> {matched_shortcode} (score {top['match_score']})")
                            progress[key] = {**progress.get(key, {}), "attach_status": "attached",
                                             "shortcode": matched_shortcode}
                        else:
                            # Genuine write failure OR the row was no longer an
                            # open target (resolve returned None) — never a
                            # false success. Treat as unmatched for the report.
                            matched_shortcode = None
                            print_fn(f"ATTACH FAILED (write or no-longer-open): {url}")
                            unmatched.append({"url": url, "fetched_title": fetched_title,
                                              "what": description[:200], "candidates": ranked[:3]})
                else:
                    unmatched.append({"url": url, "fetched_title": fetched_title,
                                      "what": description[:200], "candidates": ranked[:3]})
                    print_fn(f"UNMATCHED: {url} ({len(ranked)} candidate(s) scored)")

            reel_stem = reel_stems.get(matched_shortcode) if matched_shortcode else None
            slug = resource_slug(fetched_title, url)

            # --- PART 2: ingest ---
            if dry_run:
                link = f"linked to {matched_shortcode}" if matched_shortcode else "unlinked"
                print_fn(f"  would ingest -> resources/{slug}.md (source_type={source_type}, {link}); "
                         f"Gemini summary skipped in dry-run")
                if not matched_shortcode:
                    unlinked_index.append({"stem": slug, "url": url})
                continue

            extraction = extract_fn(content, kind, fetched_title, taxonomy)
            if extraction is None:
                if watcher.quota_hit:
                    print_fn(f"QUOTA STOP at {url} — saving progress, re-run to resume")
                    quota_stopped = True
                    break
                degraded.append(url)
                progress[key] = {**progress.get(key, {}), "ingest_status": "degraded", "url": url}
                _save_progress(progress_file, progress)
                print_fn(f"DEGRADED (retryable): {url}")
                continue

            note_path = vault / "resources" / f"{slug}.md"
            note_path.parent.mkdir(parents=True, exist_ok=True)
            note_path.write_text(
                build_resource_note(
                    url=url, fetched_title=fetched_title, source_type=source_type,
                    extraction=extraction, matched_shortcode=matched_shortcode,
                    reel_stem=reel_stem, date_ingested=datetime.now(timezone.utc).date().isoformat(),
                ),
                encoding="utf-8",
            )
            ingested.append({"url": url, "stem": slug, "shortcode": matched_shortcode})
            if not matched_shortcode:
                unlinked_index.append({"stem": slug, "url": url})
            progress[key] = {**progress.get(key, {}), "ingest_status": "ingested",
                             "note": str(note_path), "url": url}
            _save_progress(progress_file, progress)
            print_fn(f"INGESTED: {url} -> resources/{slug}.md")
    finally:
        gemini_logger.removeHandler(watcher)

    return {
        "attached": attached, "unmatched": unmatched, "already": already,
        "unreadable": unreadable, "ingested": ingested, "degraded": degraded,
        "unlinked_index": unlinked_index, "quota_stopped": quota_stopped,
    }


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--file", default="resource.txt", help="file of resource URLs (default resource.txt)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--progress-file", default=DEFAULT_PROGRESS_FILE)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    from app import obsidian_sync, store
    from app.main import _commit_attach
    from app import attach_audit
    from app.gemini_pipe import run_resource_extraction
    from scripts.ingest_resources import classify_resource_url, fetch_resource_content

    store.init_db()
    vault = Path(obsidian_sync.VAULT_PATH)

    resource_file = Path(args.file)
    if not resource_file.exists():
        sys.exit(f"resource file not found: {resource_file}")

    raw_urls = read_urls(resource_file)
    urls, dup_counts = dedupe(raw_urls)
    if args.limit:
        urls = urls[: args.limit]

    candidates = store.get_attach_candidates()
    attached_map = already_attached_map()
    reel_stems = {sc: p.stem for sc, p in obsidian_sync.existing_notes_by_shortcode(vault).items()}
    taxonomy = store.get_taxonomy()

    def commit_fn(shortcode: str, resource_url: str) -> bool:
        row = store.resolve_attachable_by_shortcode(shortcode)
        if row is None:  # already attached or no longer an open gate
            return False
        return _commit_attach(row, resource_url)

    print(f"{len(raw_urls)} URLs in {resource_file.name} -> {len(urls)} unique "
          f"({sum(dup_counts.values())} duplicate line(s) collapsed)")
    print(f"open gate candidates: {len(candidates)} | already-attached URLs: {len(attached_map)} | "
          f"mode: {'DRY-RUN (no writes)' if args.dry_run else 'LIVE'}\n")

    result = run(
        urls, vault, Path(args.progress_file), dry_run=args.dry_run,
        candidates=candidates, attached_map=attached_map, reel_stems=reel_stems, taxonomy=taxonomy,
        fetch_fn=fetch_resource_content, classify_fn=classify_resource_url,
        extract_fn=run_resource_extraction, commit_fn=commit_fn, audit_fn=attach_audit.record,
    )

    print("\n" + "=" * 70)
    print(f"PART 1  attach: {len(result['attached'])} | unmatched: {len(result['unmatched'])} | "
          f"already-attached: {len(result['already'])}")
    print(f"PART 2  ingested: {len(result['ingested'])} | unreadable: {len(result['unreadable'])} | "
          f"degraded(retry): {len(result['degraded'])}")
    if result["quota_stopped"]:
        print("STOPPED on Gemini quota — re-run to resume where it left off.")

    if not args.dry_run:
        Path(UNMATCHED_REPORT).write_text(
            render_unmatched_report(
                [{"url": u["url"], "fetched_title": u["fetched_title"], "what": u["what"],
                  "candidates": u["candidates"]} for u in result["unmatched"]],
                result["already"], result["unreadable"],
            ), encoding="utf-8",
        )
        update_index_unlinked(vault / "_index.md", result["unlinked_index"])
        print(f"\nwrote {UNMATCHED_REPORT} and updated {vault / '_index.md'} (Unlinked resources block)")
        print("NEXT: run `python scripts/sync_to_obsidian.py` to render the reel-side "
              "'## Attached Resource' links for the newly-attached rows.")
    else:
        print("\n[dry-run] no writes, no attaches, no reports. Re-run without --dry-run to execute.")


if __name__ == "__main__":
    main()
