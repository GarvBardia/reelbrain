"""Carousel slide reading: fetch EVERY slide image of a multi-image Instagram
post so Gemini can actually read the text on them.

WHY THIS EXISTS: carousels were the pipeline's worst blind spot. yt-dlp is
video-only, so a carousel yielded no media at all and we fell back to the
caption alone. But carousels are very often step-by-step guides where the
caption is just a hook ("Comment MCP for the link!") and ALL the value is
rendered as text on slides 2..N. Gemini is multimodal — given the images it
can read them directly.

HOW THE SLIDE URLS ARE OBTAINED (established empirically, see PROGRESS.md):
  - yt-dlp: knows a carousel has N children but refuses the whole post with
    "No video formats found". With extract_flat+ignoreerrors it returns N
    entries that are all None — so it gives a COUNT and nothing else.
  - the post page's own HTML: Instagram now renders it entirely in JS —
    zero display_url, zero image URLs, anonymous or cookied.
  - the EMBED endpoint (/p/{shortcode}/embed/captioned/): still serves a
    server-rendered payload containing a `display_url` per slide, in
    double-escaped JSON. This is the one that works, anonymously, and is
    what this module parses. Verified live: 10/10 and 7/7 slides on two real
    carousels.

LOCAL-ONLY in practice: like the rest of the recovery path, this needs a
residential IP — Instagram serves the datacenter ranges differently. The
module itself is dependency-light (httpx + regex) so importing it on Render
is harmless, it just won't get results there.
"""
from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import unquote

logger = logging.getLogger("reelbrain.carousel")

EMBED_URL_TEMPLATE = "https://www.instagram.com/p/{shortcode}/embed/captioned/"
FETCH_TIMEOUT_SECONDS = 25.0
IMAGE_TIMEOUT_SECONDS = 30.0
# Gemini multimodal calls cost far more than text; a 20-slide carousel would
# be wasteful and slides that deep are rare. Cap what we actually upload.
MAX_SLIDES = 12
# Below this, a "slide" is almost certainly a tracking pixel or an avatar,
# not real content.
MIN_IMAGE_BYTES = 5_000

BOT_USER_AGENT = (
    "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)"
)

# Each slide appears as  \"display_url\":\"https:\\/\\/scontent...\"  in the
# embed payload's double-escaped JSON.
_DISPLAY_URL_RE = re.compile(r'display_url\\?"\s*:\s*\\?"(.*?)\\?"\s*,')
# The post owner's avatar is served from a different path prefix than slide
# media and must never be mistaken for a slide.
_AVATAR_PATH_MARKER = "/t51.2885-19/"


def _unescape_url(raw: str) -> str:
    """The embed payload escapes twice: \\\\/ -> \\/ -> / and \\u00253D -> %3D -> =."""
    url = raw.replace("\\\\/", "/").replace("\\/", "/")
    url = url.replace("\\u00253D", "%3D").replace("\\u0026", "&")
    return unquote(url)


def extract_slide_urls(embed_html: str) -> list[str]:
    """Ordered, de-duplicated slide image URLs from an embed page's HTML.

    Order matters enormously: carousels are sequential, and "slide 3 is the
    actual value" only means anything if the order is faithful. First
    occurrence wins, which is the post's own slide order.
    """
    urls: list[str] = []
    seen: set[str] = set()
    for raw in _DISPLAY_URL_RE.findall(embed_html):
        url = _unescape_url(raw)
        if not url.startswith("http"):
            continue
        if _AVATAR_PATH_MARKER in url:  # profile picture, not a slide
            continue
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def fetch_slide_urls(shortcode: str) -> tuple[list[str], Optional[str]]:
    """(slide_urls, error). error is None on success. Never raises."""
    import httpx

    try:
        response = httpx.get(
            EMBED_URL_TEMPLATE.format(shortcode=shortcode),
            headers={"User-Agent": BOT_USER_AGENT},
            timeout=FETCH_TIMEOUT_SECONDS,
            follow_redirects=True,
        )
    except Exception as exc:  # noqa: BLE001 - a failed fetch is an expected outcome
        logger.warning("carousel: embed fetch failed for %s", shortcode, exc_info=True)
        return [], f"embed request failed: {type(exc).__name__}: {exc}"

    if response.status_code != 200:
        return [], f"embed returned HTTP {response.status_code}"

    urls = extract_slide_urls(response.text)
    if not urls:
        return [], "embed page had no display_url entries (post may be private, deleted, or video-only)"
    return urls[:MAX_SLIDES], None


def download_slides(urls: list[str]) -> tuple[list[bytes], Optional[str]]:
    """Download each slide's bytes, in order. Returns ([], error) if NOTHING
    could be downloaded; a partial download is still a success (with whatever
    slides did arrive) since a 6-of-7 read still beats caption-only."""
    import httpx

    images: list[bytes] = []
    for index, url in enumerate(urls):
        try:
            response = httpx.get(
                url, headers={"User-Agent": BOT_USER_AGENT},
                timeout=IMAGE_TIMEOUT_SECONDS, follow_redirects=True,
            )
            response.raise_for_status()
        except Exception:  # noqa: BLE001
            logger.warning("carousel: slide %d download failed (%s)", index, url[:80], exc_info=True)
            continue
        if len(response.content) < MIN_IMAGE_BYTES:
            logger.info("carousel: slide %d too small (%d bytes), skipping", index, len(response.content))
            continue
        images.append(response.content)

    if not images:
        return [], "every slide image failed to download"
    return images, None


def fetch_carousel_images(shortcode: str) -> tuple[list[bytes], int, Optional[str]]:
    """The whole path: (images, slide_count_seen, error).

    slide_count_seen is how many slides the post ADVERTISES, which can exceed
    len(images) when some downloads fail — the caller logs both so
    "no images available" stays distinguishable from "images available but
    unread", per the explicit requirement.
    """
    urls, error = fetch_slide_urls(shortcode)
    if error:
        return [], 0, error
    images, download_error = download_slides(urls)
    if download_error:
        return [], len(urls), download_error
    return images, len(urls), None
