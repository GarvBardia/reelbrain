"""Deep resource ingestion into Obsidian.

For every reel that has a "Gate resource" URL attached in Notion (the actual
DM'd resource -- a Drive doc, GitHub repo, web guide, PDF -- not the reel
itself), this fetches the resource's real content, summarizes it via Gemini
(same anti-slop discipline as reel extraction, adapted for longer-form text),
and writes a linked resources/{shortcode}-{slug}.md note in the Obsidian
vault, bidirectionally linked from the parent reel note and relevant topic
notes (the linking itself is rendered by app/obsidian_sync.py on its next
sync, which reads "Gate resource" + these resource notes directly).

LOCAL-ONLY (never deployed to Render, same reasoning as local_fetch.py) --
needs requirements-local.txt (beautifulsoup4, pypdf) installed locally:
    pip install -r requirements-local.txt

Idempotent, progress-tracked, resumable -- same pattern as bulk_ingest_local.py.
A resource that can't be read (private Drive doc, dead link, paywall) is
logged clearly as "unreadable -- manual review needed" and left out of the
vault entirely, never invented or half-guessed.

Usage:
    python scripts/ingest_resources.py --dry-run [--limit N]
    python scripts/ingest_resources.py                    # full batch, writes notes
    python scripts/ingest_resources.py --progress-file X.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

DEFAULT_PROGRESS_FILE = "ingest_resources_progress.json"
FETCH_TIMEOUT_SECONDS = 20.0
FETCH_SPACING_SECONDS = 1.0  # gentle on the resource hosts, not just our own burner
MAX_PDF_PAGES = 30
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)

_SIGN_IN_MARKERS = ("accounts.google.com", "Sign in - Google Accounts", "meet the requirement")


# --- URL classification --------------------------------------------------------


def classify_resource_url(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").lower()
    if "github.com" in host:
        return "github_repo"
    if "docs.google.com" in host and "/document/" in path:
        return "google_doc"
    if "drive.google.com" in host:
        return "google_drive_file"
    if path.endswith(".pdf"):
        return "pdf"
    return "web_article"


# --- per-type fetchers: each returns (content, error) — exactly one is None ----


def fetch_github_readme(url: str) -> tuple[Optional[str], Optional[str]]:
    import httpx

    parts = [p for p in urlparse(url).path.split("/") if p]
    if len(parts) < 2:
        return None, f"couldn't parse owner/repo from {url}"
    owner, repo = parts[0], parts[1]
    api_url = f"https://api.github.com/repos/{owner}/{repo}/readme"
    try:
        resp = httpx.get(
            api_url,
            headers={"Accept": "application/vnd.github.raw", "User-Agent": BROWSER_UA},
            timeout=FETCH_TIMEOUT_SECONDS,
            follow_redirects=True,
        )
    except Exception as exc:  # noqa: BLE001 - report and move on, never crash the batch
        return None, f"request failed: {exc}"
    if resp.status_code == 404:
        return None, "no README found on this repo"
    if resp.status_code == 403:
        return None, "GitHub API rate-limited (unauthenticated, 60 req/hr) — try again later"
    if resp.status_code != 200:
        return None, f"GitHub API returned {resp.status_code}"
    return resp.text, None


def _html_to_text(html: str) -> str:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def fetch_google_doc(url: str) -> tuple[Optional[str], Optional[str]]:
    import httpx

    mobilebasic_url = url if url.rstrip("/").endswith("mobilebasic") else (
        re.sub(r"/(edit|view|preview)?/?(\?.*)?$", "/mobilebasic", url)
    )
    try:
        resp = httpx.get(
            mobilebasic_url, headers={"User-Agent": BROWSER_UA},
            timeout=FETCH_TIMEOUT_SECONDS, follow_redirects=True,
        )
    except Exception as exc:  # noqa: BLE001
        return None, f"request failed: {exc}"
    if resp.status_code != 200:
        return None, f"HTTP {resp.status_code} — likely not publicly shared"
    if any(marker in resp.text for marker in _SIGN_IN_MARKERS) or "accounts.google.com" in str(resp.url):
        return None, "requires Google sign-in — not publicly shared, manual review needed"
    text = _html_to_text(resp.text)
    if len(text.split()) < 15:
        return None, "page loaded but has almost no extractable text (private/empty doc?)"
    return text, None


def fetch_drive_file(url: str) -> tuple[Optional[str], Optional[str]]:
    """Drive /file/d/{id}/view links usually require login unless explicitly
    shared "anyone with the link" — best-effort direct-download attempt, honest
    failure otherwise rather than guessing at content we can't verify."""
    import httpx

    match = re.search(r"/file/d/([\w-]+)", url)
    if not match:
        return None, f"couldn't parse a Drive file id from {url}"
    file_id = match.group(1)
    download_url = f"https://drive.google.com/uc?export=download&id={file_id}"
    try:
        resp = httpx.get(
            download_url, headers={"User-Agent": BROWSER_UA},
            timeout=FETCH_TIMEOUT_SECONDS, follow_redirects=True,
        )
    except Exception as exc:  # noqa: BLE001
        return None, f"request failed: {exc}"
    content_type = resp.headers.get("content-type", "")
    if "text/html" in content_type:
        # Drive serves an HTML page (sign-in wall, or the "can't scan for
        # viruses" interstitial for large files) instead of the actual file —
        # either way, we can't get real content without a logged-in session.
        return None, "Drive served an HTML page, not the file — likely requires login or is too large to auto-download; manual review needed"
    if "application/pdf" in content_type:
        return _extract_pdf_text(resp.content)
    return None, f"unrecognized content-type from Drive: {content_type or '(none)'}"


def _extract_pdf_text(pdf_bytes: bytes) -> tuple[Optional[str], Optional[str]]:
    import io

    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
    except Exception as exc:  # noqa: BLE001 - corrupt/encrypted/non-PDF bytes
        return None, f"couldn't parse as PDF: {exc}"
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception:  # noqa: BLE001
            return None, "PDF is password-protected — manual review needed"
    pages_text = []
    for page in reader.pages[:MAX_PDF_PAGES]:
        try:
            pages_text.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001 - a single bad page shouldn't kill the whole extract
            continue
    text = "\n".join(pages_text).strip()
    if len(text.split()) < 15:
        return None, "PDF parsed but yielded almost no extractable text (scanned/image-only pages?)"
    return text, None


def fetch_pdf(url: str) -> tuple[Optional[str], Optional[str]]:
    import httpx

    try:
        resp = httpx.get(
            url, headers={"User-Agent": BROWSER_UA},
            timeout=FETCH_TIMEOUT_SECONDS, follow_redirects=True,
        )
    except Exception as exc:  # noqa: BLE001
        return None, f"request failed: {exc}"
    if resp.status_code != 200:
        return None, f"HTTP {resp.status_code}"
    return _extract_pdf_text(resp.content)


def fetch_web_article(url: str) -> tuple[Optional[str], Optional[str]]:
    import httpx

    try:
        resp = httpx.get(
            url, headers={"User-Agent": BROWSER_UA},
            timeout=FETCH_TIMEOUT_SECONDS, follow_redirects=True,
        )
    except Exception as exc:  # noqa: BLE001
        return None, f"request failed: {exc}"
    if resp.status_code != 200:
        return None, f"HTTP {resp.status_code}"
    text = _html_to_text(resp.text)
    if len(text.split()) < 15:
        return None, "page loaded but has almost no extractable text (JS-only page, paywall, or dead content?)"
    return text, None


_FETCHERS: dict[str, Callable[[str], tuple[Optional[str], Optional[str]]]] = {
    "github_repo": fetch_github_readme,
    "google_doc": fetch_google_doc,
    "google_drive_file": fetch_drive_file,
    "pdf": fetch_pdf,
    "web_article": fetch_web_article,
}


def fetch_resource_content(url: str, kind: str) -> tuple[Optional[str], Optional[str]]:
    fetcher = _FETCHERS.get(kind, fetch_web_article)
    return fetcher(url)


# --- Notion: find every reel with a Gate resource attached ---------------------


def find_gated_resources() -> list[dict]:
    from app import notion_writer

    pages = notion_writer.find_saves_pages_since("1970-01-01T00:00:00")
    entries = []
    for page in pages:
        props = page.get("properties", {})
        resource_url = (props.get("Gate resource") or {}).get("url")
        if not resource_url:
            continue
        fields = notion_writer.extract_digest_fields(page)
        if not fields["shortcode"]:
            continue
        entries.append({
            "shortcode": fields["shortcode"],
            "reel_title": fields["title"],
            "resource_url": resource_url,
            "topics": fields["topics"],
        })
    return entries


# --- vault note building --------------------------------------------------------


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", (name or "").strip().lower()).strip("-")
    return (slug or "resource")[:60]


def resource_note_path(vault: Path, shortcode: str, reel_title: str) -> Path:
    return vault / "resources" / f"{shortcode}-{_slugify(reel_title or shortcode)}.md"


def build_resource_note(entry: dict, reel_stem: Optional[str], extraction, resource_url: str) -> str:
    lines = ["---"]
    lines.append(f"source_shortcode: {entry['shortcode']}")
    if reel_stem:
        lines.append(f'source_reel: "[[reels/{reel_stem}]]"')
    lines.append(f"resource_url: {resource_url}")
    lines.append(f"resource_kind: {extraction.resource_kind}")
    if extraction.topic_tags:
        lines.append("topics:")
        for topic in extraction.topic_tags:
            lines.append(f'  - "[[topics/{_slugify(topic)}]]"')
        # Plain comma-separated duplicate of the list above -- lets
        # obsidian_sync.existing_resource_notes() fold resources into topic
        # indexes with a one-line regex instead of a full YAML parser.
        lines.append(f"topics_plain: {', '.join(extraction.topic_tags)}")
    lines.append("---")
    lines.append("")
    lines.append(f"# {entry['reel_title'] or entry['shortcode']} — attached resource")
    lines.append("")
    lines.append(f"Source: <{resource_url}>")
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
    return "\n".join(lines).rstrip() + "\n"


# --- orchestration (bulk_ingest_local.py pattern: injectable, progress-tracked) -


def _load_progress(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_progress(path: Path, progress: dict) -> None:
    path.write_text(json.dumps(progress, indent=2, sort_keys=True), encoding="utf-8")


def run_ingest(
    entries: list[dict],
    vault: Path,
    progress_file: Path,
    dry_run: bool = False,
    fetch_fn: Callable[[str, str], tuple[Optional[str], Optional[str]]] = fetch_resource_content,
    extract_fn=None,
    taxonomy: Optional[list[str]] = None,
    reel_stems: Optional[dict[str, str]] = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    print_fn: Callable[..., None] = print,
) -> dict:
    if extract_fn is None:
        from app import gemini_pipe
        extract_fn = gemini_pipe.run_resource_extraction

    progress = _load_progress(progress_file)
    reel_stems = reel_stems or {}
    taxonomy = taxonomy or []

    written: list[str] = []
    unreadable: list[str] = []
    degraded: list[str] = []
    skipped_done: list[str] = []

    for entry in entries:
        shortcode = entry["shortcode"]
        if progress.get(shortcode, {}).get("status") == "written":
            skipped_done.append(shortcode)
            continue

        kind = classify_resource_url(entry["resource_url"])
        content, error = fetch_fn(entry["resource_url"], kind)
        sleep_fn(FETCH_SPACING_SECONDS)

        if content is None:
            print_fn(f"UNREADABLE — manual review needed: {entry['resource_url']} ({shortcode}) — {error}")
            unreadable.append(shortcode)
            progress[shortcode] = {"status": "unreadable", "url": entry["resource_url"], "error": error}
            if not dry_run:
                _save_progress(progress_file, progress)
            continue

        if dry_run:
            print_fn(f"[dry-run] fetched {len(content)} chars from {entry['resource_url']} (kind={kind})")

        extraction = extract_fn(content, kind, entry["reel_title"], taxonomy)
        if extraction is None:
            print_fn(f"DEGRADED (Gemini) — will retry later: {entry['resource_url']} ({shortcode})")
            degraded.append(shortcode)
            progress[shortcode] = {"status": "degraded", "url": entry["resource_url"]}
            if not dry_run:
                _save_progress(progress_file, progress)
            continue

        if dry_run:
            print_fn(f"[dry-run] would write resources/{shortcode}-*.md")
            print_fn(f"  resource_kind: {extraction.resource_kind}")
            print_fn(f"  summary: {extraction.summary[:200]}")
            print_fn(f"  topics: {extraction.topic_tags}")
            written.append(shortcode)
            continue

        note_path = resource_note_path(vault, shortcode, entry["reel_title"])
        note_path.parent.mkdir(parents=True, exist_ok=True)
        note_path.write_text(
            build_resource_note(entry, reel_stems.get(shortcode), extraction, entry["resource_url"]),
            encoding="utf-8",
        )
        written.append(shortcode)
        progress[shortcode] = {"status": "written", "url": entry["resource_url"], "note": str(note_path)}
        _save_progress(progress_file, progress)

    return {
        "written": written,
        "unreadable": unreadable,
        "degraded": degraded,
        "skipped_done": skipped_done,
    }


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None, help="only process the first N entries")
    parser.add_argument("--progress-file", default=DEFAULT_PROGRESS_FILE)
    args = parser.parse_args()

    from app import obsidian_sync, store

    store.init_db()
    vault = Path(obsidian_sync.VAULT_PATH)
    entries = find_gated_resources()
    if args.limit:
        entries = entries[: args.limit]

    reel_paths = obsidian_sync.existing_notes_by_shortcode(vault)
    reel_stems = {sc: path.stem for sc, path in reel_paths.items()}
    taxonomy = store.get_taxonomy()

    print(f"found {len(entries)} reel(s) with a Gate resource attached"
          + (f" (limited to first {args.limit})" if args.limit else ""))

    result = run_ingest(
        entries, vault, Path(args.progress_file), dry_run=args.dry_run,
        reel_stems=reel_stems, taxonomy=taxonomy,
    )

    print("\n" + "=" * 70)
    print(
        f"done: {len(result['written'])} written, {len(result['unreadable'])} unreadable, "
        f"{len(result['degraded'])} degraded, {len(result['skipped_done'])} already-done skipped, "
        f"of {len(entries)} total"
    )
    if result["unreadable"]:
        print(f"UNREADABLE (manual review needed): {result['unreadable']}")
    if result["degraded"]:
        print(f"DEGRADED (retry later): {result['degraded']}")


if __name__ == "__main__":
    main()
