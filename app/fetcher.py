"""Free-first reel fetch via yt-dlp, with burner-account safety caps.

CLAUDE.md non-negotiables enforced here:
  - never use the real account's cookies (startup guard)
  - max N fetches/day, >=20s spacing, bounded exponential backoff, then give up
    and say "refresh burner cookies" rather than retry-looping forever
"""
from __future__ import annotations

import html as html_lib
import logging
import os
import re
import time
from typing import Optional

from app import store
from app.models import ReelData

logger = logging.getLogger("reelbrain.fetcher")

MAX_FETCHES_PER_DAY = int(os.environ.get("MAX_FETCHES_PER_DAY", "25"))
MIN_FETCH_SPACING_SECONDS = int(os.environ.get("MIN_FETCH_SPACING_SECONDS", "20"))
BURNER_COOKIES_FILE = os.environ.get("BURNER_COOKIES_FILE", "./cookies.txt").strip()
BURNER_ACCOUNT_USERNAME = os.environ.get("BURNER_ACCOUNT_USERNAME", "").strip()
REAL_ACCOUNT_GUARD = os.environ.get("REAL_ACCOUNT_GUARD", "").strip()

# Where Render mounts Secret Files. Checked as a fallback when the configured
# BURNER_COOKIES_FILE doesn't exist, so the same code works locally (./cookies.txt)
# and on Render (/etc/secrets/cookies.txt) with no env-var juggling.
RENDER_SECRETS_COOKIES_FILE = "/etc/secrets/cookies.txt"

BACKOFF_SECONDS = [20, 40, 80]  # bounded — never an unbounded retry loop

SHORTCODE_RE = re.compile(r"instagram\.com/(?:reel|reels|p|tv)/([A-Za-z0-9_-]+)")

# Two gate styles, in priority order:
#   1. QUOTED keyword, any case — 'Comment "International" for free Guide'.
#      Quoting is the creator's own signal that this is the magic word, so we
#      trust it regardless of capitalization. Curly quotes included (iOS/IG
#      autocorrect them constantly).
#   2. Unquoted ALL-CAPS word — "comment SEND below". Without quotes, the
#      all-caps requirement is what separates a keyword from ordinary prose
#      ("comment your thoughts below" must NOT match).
COMMENT_GATE_RE = re.compile(
    r"(?i:comment)\s+"
    r"(?:[\"'“”‘’]\s*([A-Za-z][A-Za-z0-9 _-]{1,29}?)\s*[\"'“”‘’]"
    r"|([A-Z]{2,12})\b)"
)

# Errors that mean "Instagram is stonewalling this request" and are worth a
# cookie-backed retry, as opposed to a genuine hard error (bad URL, deleted post).
# The bottom four are how a datacenter-IP soft-block actually presents in the wild:
# yt-dlp reports no formats / an empty media response rather than a 429 or an
# explicit login wall, so without them the cookie retry below never fires.
CHALLENGE_MARKERS = (
    "login required",
    "rate-limit",
    "429",
    "challenge",
    "checkpoint",
    "no video formats found",
    "empty media response",
    "use --cookies",
    "requested content is not available",
)

# --- OG-tag fallback (never authenticated) ---
OG_FETCH_TIMEOUT_SECONDS = 10.0
OG_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
OG_PROPERTIES = ("og:title", "og:description", "og:image")
# IG's og:title looks like: 'Jane Doe (@janedoe) on Instagram: "caption..."'
OG_TITLE_USERNAME_RE = re.compile(r"\(@([A-Za-z0-9_.]+)\)")


class FetchDegraded(Exception):
    """Raised when a fetch could not complete; carries whatever partial data exists."""

    def __init__(self, message: str, partial: ReelData):
        super().__init__(message)
        self.partial = partial


class BurnerGuardError(RuntimeError):
    """Raised at startup if the configured burner cookies appear to be the real account."""


def _cookie_candidates() -> list[str]:
    """Configured path first, then Render's Secret Files mount. Deduped, order kept."""
    candidates = [p for p in (BURNER_COOKIES_FILE, RENDER_SECRETS_COOKIES_FILE) if p]
    seen: list[str] = []
    for path in candidates:
        if path not in seen:
            seen.append(path)
    return seen


def resolve_cookies_file() -> Optional[str]:
    """First existing cookie file among the candidates, or None if there isn't one."""
    for path in _cookie_candidates():
        if os.path.isfile(path):
            return path
    return None


def cookies_file_available() -> bool:
    return resolve_cookies_file() is not None


def log_cookie_source() -> None:
    """Called once at app startup so the deploy log says which cookie file (if any)
    this process will fetch with — the single most common cause of prod-only failures."""
    path = resolve_cookies_file()
    if path:
        logger.info("burner cookies: using %s", path)
    else:
        logger.warning(
            "burner cookies: NONE FOUND at %s — fetches will fail fast until one exists",
            " or ".join(_cookie_candidates()),
        )


def normalize_url(url: str) -> str:
    """Extract the shortcode from any of the reel/p/reels URL shapes, share-text included."""
    match = SHORTCODE_RE.search(url)
    if not match:
        raise ValueError(f"could not extract shortcode from url: {url!r}")
    return match.group(1)


def detect_comment_gate(caption: Optional[str]) -> Optional[str]:
    """Returns the gate keyword if the caption regex-matches a comment-gate pattern."""
    if not caption:
        return None
    match = COMMENT_GATE_RE.search(caption)
    if not match:
        return None
    return match.group(1) or match.group(2)


def _check_burner_guard() -> None:
    if not REAL_ACCOUNT_GUARD:
        return
    if BURNER_ACCOUNT_USERNAME and BURNER_ACCOUNT_USERNAME.strip().lower() == (
        REAL_ACCOUNT_GUARD.strip().lower()
    ):
        raise BurnerGuardError(
            "BURNER_ACCOUNT_USERNAME matches REAL_ACCOUNT_GUARD — refusing to fetch. "
            "Never point this pipeline at the real account's cookies."
        )


def _enforce_rate_discipline(shortcode: str, permalink: str) -> None:
    count = store.get_daily_fetch_count()
    if count >= MAX_FETCHES_PER_DAY:
        raise FetchDegraded(
            f"daily fetch cap reached ({count}/{MAX_FETCHES_PER_DAY})",
            partial=ReelData(shortcode=shortcode, permalink=permalink),
        )
    last = store.get_last_fetch_at()
    if last is not None:
        elapsed = time.time() - last
        if elapsed < MIN_FETCH_SPACING_SECONDS:
            time.sleep(MIN_FETCH_SPACING_SECONDS - elapsed)


def _looks_like_challenge(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in CHALLENGE_MARKERS)


def _run_ytdlp(url: str, cookiefile: Optional[str]) -> dict:
    import yt_dlp

    outtmpl = os.path.join("data", "videos", "%(id)s.%(ext)s")
    os.makedirs(os.path.dirname(outtmpl), exist_ok=True)
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "format": "best",
        "outtmpl": outtmpl,
    }
    if cookiefile:
        opts["cookiefile"] = cookiefile
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        info["_video_path"] = ydl.prepare_filename(info)
    return info


def _info_to_reel_data(shortcode: str, permalink: str, info: dict) -> ReelData:
    taken_at = None
    if info.get("timestamp"):
        from datetime import datetime, timezone

        taken_at = datetime.fromtimestamp(info["timestamp"], tz=timezone.utc).isoformat()
    return ReelData(
        shortcode=shortcode,
        permalink=permalink,
        video_path=info.get("_video_path"),
        caption=info.get("description") or info.get("title"),
        creator_username=info.get("uploader_id") or info.get("uploader"),
        creator_fullname=info.get("uploader"),
        taken_at=taken_at,
        like_count=info.get("like_count"),
    )


def _parse_og_tags(html_text: str) -> dict[str, str]:
    """Pull og:* meta tags out of raw HTML. Regex rather than a parser dependency —
    we want three known tags off a page we don't control, not a DOM."""
    found: dict[str, str] = {}
    for prop in OG_PROPERTIES:
        escaped = re.escape(prop)
        patterns = (
            # property=... content=...
            rf'<meta[^>]+property=["\']{escaped}["\'][^>]*?content=["\']([^"\']*)["\']',
            # content=... property=...  (attribute order isn't guaranteed)
            rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]*?property=["\']{escaped}["\']',
        )
        for pattern in patterns:
            match = re.search(pattern, html_text, re.IGNORECASE)
            if match:
                found[prop] = html_lib.unescape(match.group(1))
                break
    return found


def fetch_og_metadata(permalink: str) -> Optional[dict[str, str]]:
    """ONE anonymous GET of the public reel page, short timeout, NEVER cookies.

    This is a last-ditch read of the page's Open Graph tags after yt-dlp has given
    up. It's deliberately unauthenticated: it must not risk the burner session, and
    OG tags are public metadata. Returns None on any failure — it's a bonus, not a
    dependency.
    """
    try:
        import httpx

        response = httpx.get(
            permalink,
            headers={"User-Agent": OG_USER_AGENT},
            timeout=OG_FETCH_TIMEOUT_SECONDS,
            follow_redirects=True,
        )
        response.raise_for_status()
        tags = _parse_og_tags(response.text)
    except Exception:  # noqa: BLE001 - fallback only; never escalate
        logger.warning("OG-tag fallback failed for %s", permalink, exc_info=True)
        return None
    return tags or None


def _og_reel_data(shortcode: str, permalink: str, tags: dict[str, str]) -> Optional[ReelData]:
    """Build a caption-only ReelData from OG tags, or None if there's no caption
    worth continuing with (no caption = nothing for the gate regex or the LLM to read)."""
    caption = tags.get("og:description") or tags.get("og:title")
    if not caption:
        return None
    username_match = OG_TITLE_USERNAME_RE.search(tags.get("og:title", ""))
    return ReelData(
        shortcode=shortcode,
        permalink=permalink,
        video_path=None,  # no media — downstream degrades to caption-only, honestly
        caption=caption,
        creator_username=username_match.group(1) if username_match else None,
        thumbnail_url=tags.get("og:image"),
    )


def _og_fallback_or_degrade(
    shortcode: str, permalink: str, reason: str, cause: Optional[Exception]
) -> ReelData:
    """Terminal path when yt-dlp has failed: try the OG-tag read once, and if it
    yields a caption, continue the pipeline caption-only rather than failing the
    row outright. Otherwise raise FetchDegraded as before."""
    tags = fetch_og_metadata(permalink)
    if tags:
        reel = _og_reel_data(shortcode, permalink, tags)
        if reel:
            logger.warning(
                "yt-dlp failed for %s (%s) — continuing caption-only from OG tags",
                shortcode, reason,
            )
            return reel
    raise FetchDegraded(reason, partial=ReelData(shortcode=shortcode, permalink=permalink)) from cause


def fetch_reel(shortcode: str, permalink: str) -> ReelData:
    """BUILD_SPEC 1.2: logged-out first, one cookie-backed retry, bounded backoff.

    Raises FetchDegraded (carrying a permalink-only ReelData) on total failure —
    callers should still write a Notion entry in that case, never drop the capture.
    """
    _check_burner_guard()

    # Fail fast, before any network call or rate-limit sleep, if there are no
    # cookies to fall back on. An anonymous-only fetch from a datacenter IP gets
    # soft-blocked by Instagram and burns a slot against the daily cap to produce
    # an inscrutable "no formats found"; a missing cookie file is an actionable
    # deploy problem and should say so on the Notion row.
    cookies_file = resolve_cookies_file()
    if not cookies_file:
        raise FetchDegraded(
            "cookies file not found at " + " or ".join(_cookie_candidates())
            + " — upload the burner cookies.txt (Render: Secret Files) and redeploy",
            partial=ReelData(shortcode=shortcode, permalink=permalink),
        )

    _enforce_rate_discipline(shortcode, permalink)

    try:
        store.record_fetch()
        info = _run_ytdlp(permalink, cookiefile=None)
        return _info_to_reel_data(shortcode, permalink, info)
    except Exception as first_exc:  # noqa: BLE001 - yt-dlp raises assorted errors
        if not _looks_like_challenge(first_exc):
            return _og_fallback_or_degrade(
                shortcode, permalink, f"fetch failed: {first_exc}", first_exc
            )

    last_exc: Optional[Exception] = None
    for delay in BACKOFF_SECONDS:
        time.sleep(delay)
        try:
            store.record_fetch()
            info = _run_ytdlp(permalink, cookiefile=cookies_file)
            return _info_to_reel_data(shortcode, permalink, info)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if not _looks_like_challenge(exc):
                return _og_fallback_or_degrade(shortcode, permalink, f"fetch failed: {exc}", exc)

    return _og_fallback_or_degrade(
        shortcode,
        permalink,
        "repeated challenges from Instagram — refresh burner cookies",
        last_exc,
    )
