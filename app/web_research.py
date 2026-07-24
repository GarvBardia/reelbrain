"""Free web-fetch research fallback (no API key, Render-safe: httpx + regex only).

Used by gemini_pipe.run_research_context when Google Search grounding is not
available on this API key (429 RESOURCE_EXHAUSTED on every grounded call —
an account entitlement issue, see PROGRESS.md). Instead of silently letting
Gemini answer from training data (forbidden — unverified memory must never be
presented as verified), this fetches REAL text about the entity:

  1. DuckDuckGo HTML endpoint (duckduckgo.com/html/?q=...) — no API key, no
     JS. Browser UA, >= DDG_MIN_SPACING_SECONDS between requests.
  2. If the top result is a GitHub repo, the README via raw.githubusercontent
     (HEAD, then main/master) — far better material than the repo's HTML page.
  3. Otherwise the top result page's visible text (tags stripped, capped).

The fetched material is then handed to Gemini as context material to
summarize — the model only ever restates what was actually fetched, marked
context_source "web-fetch"."""
from __future__ import annotations

import html as _html
import logging
import re
import time
import urllib.parse
from typing import Optional

logger = logging.getLogger("reelbrain.web_research")

DDG_MIN_SPACING_SECONDS = 2.5
DDG_TIMEOUT_SECONDS = 15.0
PAGE_TIMEOUT_SECONDS = 15.0
MAX_MATERIAL_CHARS = 8_000
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

_last_ddg_call: float = 0.0

# First organic result link on the DDG html endpoint. The href is a redirect:
# //duckduckgo.com/l/?uddg=<urlencoded real url>&rut=...
_DDG_RESULT_RE = re.compile(r'class="result__a"[^>]*href="([^"]+)"')
_GITHUB_REPO_RE = re.compile(r"^https?://github\.com/([\w.\-]+)/([\w.\-]+)/?$")
_TAG_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>|<[^>]+>", re.DOTALL | re.IGNORECASE)


def _enforce_ddg_spacing(sleep_fn=time.sleep) -> None:
    global _last_ddg_call
    elapsed = time.time() - _last_ddg_call
    if elapsed < DDG_MIN_SPACING_SECONDS:
        sleep_fn(DDG_MIN_SPACING_SECONDS - elapsed)
    _last_ddg_call = time.time()


def _strip_tags(html_text: str) -> str:
    text = _TAG_RE.sub(" ", html_text)
    text = _html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def ddg_top_result_url(query: str) -> Optional[str]:
    """URL of the first organic DuckDuckGo result, or None. Never raises."""
    import httpx

    _enforce_ddg_spacing()
    try:
        response = httpx.get(
            "https://duckduckgo.com/html/",
            params={"q": query},
            headers={"User-Agent": BROWSER_UA},
            timeout=DDG_TIMEOUT_SECONDS,
            follow_redirects=True,
        )
        response.raise_for_status()
    except Exception:  # noqa: BLE001 - a failed search is an expected outcome
        logger.warning("ddg search failed for %r", query, exc_info=True)
        return None

    match = _DDG_RESULT_RE.search(response.text)
    if not match:
        return None
    href = _html.unescape(match.group(1))
    # unwrap the /l/?uddg= redirect to the real destination
    parsed = urllib.parse.urlparse(href, scheme="https")
    if parsed.path.startswith("/l/"):
        uddg = urllib.parse.parse_qs(parsed.query).get("uddg")
        return urllib.parse.unquote(uddg[0]) if uddg else None
    if href.startswith("//"):
        return "https:" + href
    return href


def fetch_github_readme(owner: str, repo: str) -> Optional[str]:
    """README text via raw.githubusercontent — no API, no rate-limit drama."""
    import httpx

    for ref in ("HEAD", "main", "master"):
        url = f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/README.md"
        try:
            response = httpx.get(url, timeout=PAGE_TIMEOUT_SECONDS, follow_redirects=True)
            if response.status_code == 200 and response.text.strip():
                return response.text[:MAX_MATERIAL_CHARS]
        except Exception:  # noqa: BLE001
            continue
    return None


def fetch_page_text(url: str) -> Optional[str]:
    import httpx

    try:
        response = httpx.get(
            url, headers={"User-Agent": BROWSER_UA},
            timeout=PAGE_TIMEOUT_SECONDS, follow_redirects=True,
        )
        response.raise_for_status()
    except Exception:  # noqa: BLE001
        logger.warning("page fetch failed for %s", url, exc_info=True)
        return None
    text = _strip_tags(response.text)
    return text[:MAX_MATERIAL_CHARS] if text else None


def fetch_context_material(entity: str) -> tuple[Optional[str], Optional[str]]:
    """(material_text, source_url) for an entity, or (None, None) when nothing
    real could be fetched. GitHub repos get their raw README; everything else
    gets the DDG top result page's text."""
    url = ddg_top_result_url(entity)
    if not url:
        return None, None
    gh = _GITHUB_REPO_RE.match(url)
    if gh:
        readme = fetch_github_readme(gh.group(1), gh.group(2))
        if readme:
            return readme, url
    text = fetch_page_text(url)
    if text and len(text.split()) >= 20:  # a near-empty page is not real material
        return text, url
    return None, None
