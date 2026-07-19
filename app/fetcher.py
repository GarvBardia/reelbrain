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

# Third gate style: an EMOJI drop instead of a text keyword — "Drop your 🔥
# emoji to grab all in ur dms" — never contains the literal word "comment", so
# COMMENT_GATE_RE can't catch it (see Dap3IoNo4Kt, missed on the success path).
#
# Deliberately narrow: requires ALL of "drop" + an actual non-alphanumeric
# (emoji-shaped) token + the literal word "emoji" + "dm"/"dms" within the same
# ~40-char clause. Ordinary reaction-bait captions like "Drop a 🔥 if you agree"
# are common and unrelated to a DM gate — they're excluded by requiring the
# word "emoji" to immediately follow the token. "Drop your comment below,
# I'll dm you" is excluded by requiring the token itself to be non-alphanumeric
# (a plain word can't match). Scoped this tight on purpose per BUG 2's
# instruction to weigh false-positive risk — broaden only if real missed
# examples show this is too narrow.
EMOJI_DM_GATE_RE = re.compile(
    r"(?i:drop)\s+(?:your|a|the)?\s*"
    r"([^\sA-Za-z0-9\"'“”‘’]{1,4})"
    r"\s*(?i:emoji)\b"
    r"(?=[^.!?\n]{0,40}(?i:\bdms?\b))"
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

# Subset of CHALLENGE_MARKERS that specifically mean the COOKIES are the problem
# (expired/logged-out) rather than something generic — a rate limit, or a normal
# private/deleted/missing video. Only checked against COOKIE-BACKED attempts (see
# fetch_reel's retry loop): an anonymous attempt hitting "login required" is
# expected and tells us nothing about whether our own cookies are still good.
# "no video formats found" / "429" / "rate-limit" / "requested content is not
# available" are deliberately excluded — those happen for reasons unrelated to
# cookie validity and would make this counter noisy/false-positive-prone.
AUTH_FAILURE_MARKERS = (
    "login required",
    "empty media response",
    "challenge",
    "checkpoint",
    "use --cookies",
)

# Consecutive cookie-backed auth failures before /health reports cookie_health
# as "degraded". Override via COOKIE_HEALTH_THRESHOLD if 3 is too twitchy/lax.
AUTH_FAILURE_THRESHOLD = int(os.environ.get("COOKIE_HEALTH_THRESHOLD", "3"))
_CONSECUTIVE_AUTH_FAILURES_KEY = "consecutive_cookie_auth_failures"

# yt-dlp's signature for "this post has no video" — a photo or carousel post,
# which yt-dlp (video-only) structurally cannot fetch regardless of cookies or
# retries. Distinct from CHALLENGE_MARKERS' broader "no video formats found"
# entry: that one drives the cookie-backed retry loop (worth trying once with
# auth in case it's actually a soft-block), this one decides what to do once
# every fetch attempt — including the OG-tag scrape — has already failed.
PHOTO_OR_CAROUSEL_MARKERS = ("no video formats found",)

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
    """Returns the gate keyword if the caption regex-matches a comment-gate
    pattern — either a text keyword (COMMENT_GATE_RE) or an emoji-drop-for-DM
    style (EMOJI_DM_GATE_RE)."""
    if not caption:
        return None
    match = COMMENT_GATE_RE.search(caption)
    if match:
        return match.group(1) or match.group(2)
    emoji_match = EMOJI_DM_GATE_RE.search(caption)
    if emoji_match:
        return emoji_match.group(1)
    return None


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


def _is_cookie_auth_failure(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in AUTH_FAILURE_MARKERS)


def get_consecutive_auth_failures() -> int:
    raw = store.get_state(_CONSECUTIVE_AUTH_FAILURES_KEY, "0")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def record_cookie_auth_failure() -> int:
    """Called only on a COOKIE-BACKED fetch attempt failing with an auth-type
    marker. Returns the new consecutive count."""
    count = get_consecutive_auth_failures() + 1
    store.set_state(_CONSECUTIVE_AUTH_FAILURES_KEY, str(count))
    logger.warning("cookie auth failure recorded (%d consecutive)", count)
    return count


def record_cookie_auth_success() -> None:
    """Called on any successful cookie-backed fetch — resets the streak."""
    if get_consecutive_auth_failures():
        logger.info("cookie-backed fetch succeeded — resetting auth-failure counter")
    store.set_state(_CONSECUTIVE_AUTH_FAILURES_KEY, "0")


def cookie_health_status() -> str:
    """"ok" or "degraded" — the /health field. Detection only; nothing here ever
    attempts to log in or refresh cookies (that risks the burner account)."""
    return "degraded" if get_consecutive_auth_failures() >= AUTH_FAILURE_THRESHOLD else "ok"


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


def _is_photo_or_carousel(reason: str) -> bool:
    text = reason.lower()
    return any(marker in text for marker in PHOTO_OR_CAROUSEL_MARKERS)


def _og_fallback_or_degrade(
    shortcode: str, permalink: str, reason: str, cause: Optional[Exception]
) -> ReelData:
    """Terminal path when yt-dlp has failed: try the OG-tag read once, and if it
    yields a caption, continue the pipeline caption-only rather than failing the
    row outright.

    If the original failure was yt-dlp's "no video formats found" signature,
    this is almost certainly a photo/carousel post — not a technical failure at
    all, just a kind of post yt-dlp (video-only) can never fetch. Retrying that
    can never succeed, so it must not land as "Failed — retry" regardless of
    whether the OG-tag scrape recovers a caption or not:
      - OG succeeds: is_photo_or_carousel is tagged here too (previously only
        the OG-also-fails branch below tagged it), so downstream a caption-only
        Gemini extraction runs instead of silently treating this as an ordinary
        capture with no distinguishing note.
      - OG also fails: return a URL-only ReelData instead so the pipeline still
        produces a captured row (📷 Photo — manual), never a dropped capture.
    Any OTHER kind of total failure still raises FetchDegraded as before —
    those may genuinely be worth a human retry.
    """
    tags = fetch_og_metadata(permalink)
    if tags:
        reel = _og_reel_data(shortcode, permalink, tags)
        if reel:
            logger.warning(
                "yt-dlp failed for %s (%s) — continuing caption-only from OG tags",
                shortcode, reason,
            )
            if _is_photo_or_carousel(reason):
                reel = reel.model_copy(update={
                    "is_photo_or_carousel": True,
                    "fetch_note": (
                        "photo/carousel post — summarized from caption only, "
                        "no video transcript available"
                    ),
                })
            return reel

    if _is_photo_or_carousel(reason):
        logger.warning(
            "yt-dlp reports no video formats for %s (OG-tag scrape also failed, "
            "likely login-walled from this IP) — treating as a photo/carousel "
            "post and capturing URL-only instead of dropping it",
            shortcode,
        )
        return ReelData(
            shortcode=shortcode,
            permalink=permalink,
            caption=None,
            is_photo_or_carousel=True,
            fetch_note="photo/carousel post — no auto-transcript, open the reel URL to view",
        )

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
            record_cookie_auth_success()
            return _info_to_reel_data(shortcode, permalink, info)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if _is_cookie_auth_failure(exc):
                record_cookie_auth_failure()
            if not _looks_like_challenge(exc):
                return _og_fallback_or_degrade(shortcode, permalink, f"fetch failed: {exc}", exc)

    # Include last_exc's own text, not just the generic framing: a photo/carousel
    # post keeps reporting "no video formats found" on every retry regardless of
    # cookies (retrying can't turn a photo into a video), and that signature has
    # to survive into the reason string for _is_photo_or_carousel to catch it —
    # otherwise every such post would get misclassified as a genuine soft-block.
    return _og_fallback_or_degrade(
        shortcode,
        permalink,
        f"repeated challenges from Instagram — refresh burner cookies (last error: {last_exc})",
        last_exc,
    )
