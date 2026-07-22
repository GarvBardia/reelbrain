"""Lightweight, Render-safe title/description fetch for a DM'd resource URL —
used only by /attach's candidate-scoring resolution path (see PROGRESS.md).

Deliberately NOT scripts/ingest_resources.py's fetchers: those need
beautifulsoup4/pypdf (requirements-local.txt, LOCAL-ONLY, never installed on
Render). This only needs enough text to score against pending rows, not a
full resource summary, so plain regex over the raw HTML (httpx only, already
in the deployed requirements.txt) is enough.
"""
from __future__ import annotations

import html
import logging
import re

logger = logging.getLogger("reelbrain.resource_lookup")

FETCH_TIMEOUT_SECONDS = 8.0
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)

_TITLE_TAG_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_META_RE_TEMPLATE = r'<meta[^>]+(?:name|property)=["\']{name}["\'][^>]+content=["\'](.*?)["\']'


def _unescape(text: str) -> str:
    return html.unescape(re.sub(r"\s+", " ", text)).strip()


def _extract_meta(html_text: str, name: str) -> str:
    match = re.search(_META_RE_TEMPLATE.format(name=re.escape(name)), html_text, re.IGNORECASE | re.DOTALL)
    return _unescape(match.group(1)) if match else ""


def fetch_resource_title_and_description(url: str) -> tuple[str, str]:
    """Best-effort (title, description) for a resource URL. Never raises —
    returns ("", "") on any failure (network error, non-200, no <title>/meta
    tags at all); this is a scoring aid, not a required step."""
    import httpx

    try:
        resp = httpx.get(
            url, headers={"User-Agent": BROWSER_UA},
            timeout=FETCH_TIMEOUT_SECONDS, follow_redirects=True,
        )
        resp.raise_for_status()
    except Exception:  # noqa: BLE001 - best-effort, /attach must still be able to proceed
        logger.warning("fetch_resource_title_and_description: request failed for %s", url, exc_info=True)
        return "", ""

    html_text = resp.text
    title_match = _TITLE_TAG_RE.search(html_text)
    title = _unescape(title_match.group(1)) if title_match else ""
    description = _extract_meta(html_text, "og:description") or _extract_meta(html_text, "description")
    return title, description
