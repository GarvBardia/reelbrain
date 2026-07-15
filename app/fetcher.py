"""Free-first reel fetch via yt-dlp, with burner-account safety caps.

CLAUDE.md non-negotiables enforced here:
  - never use the real account's cookies (startup guard)
  - max N fetches/day, >=20s spacing, bounded exponential backoff, then give up
    and say "refresh burner cookies" rather than retry-looping forever
"""
from __future__ import annotations

import os
import re
import time
from typing import Optional

from app import store
from app.models import ReelData

MAX_FETCHES_PER_DAY = int(os.environ.get("MAX_FETCHES_PER_DAY", "25"))
MIN_FETCH_SPACING_SECONDS = int(os.environ.get("MIN_FETCH_SPACING_SECONDS", "20"))
BURNER_COOKIES_FILE = os.environ.get("BURNER_COOKIES_FILE", "./cookies.txt")
BURNER_ACCOUNT_USERNAME = os.environ.get("BURNER_ACCOUNT_USERNAME", "")
REAL_ACCOUNT_GUARD = os.environ.get("REAL_ACCOUNT_GUARD", "")

BACKOFF_SECONDS = [20, 40, 80]  # bounded — never an unbounded retry loop

SHORTCODE_RE = re.compile(r"instagram\.com/(?:reel|reels|p|tv)/([A-Za-z0-9_-]+)")

# CLAUDE.md's own regex, kept literal — the [A-Z]{2,12} deliberately only
# matches an actually-capitalized keyword (e.g. "comment SEND"), the usual
# gate style; the word "comment" itself is matched case-insensitively.
COMMENT_GATE_RE = re.compile(r"(?i:comment)\s+[\"']?([A-Z]{2,12})[\"']?")

CHALLENGE_MARKERS = (
    "login required",
    "rate-limit",
    "429",
    "challenge",
    "checkpoint",
)


class FetchDegraded(Exception):
    """Raised when a fetch could not complete; carries whatever partial data exists."""

    def __init__(self, message: str, partial: ReelData):
        super().__init__(message)
        self.partial = partial


class BurnerGuardError(RuntimeError):
    """Raised at startup if the configured burner cookies appear to be the real account."""


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
    return match.group(1) if match else None


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


def fetch_reel(shortcode: str, permalink: str) -> ReelData:
    """BUILD_SPEC 1.2: logged-out first, one cookie-backed retry, bounded backoff.

    Raises FetchDegraded (carrying a permalink-only ReelData) on total failure —
    callers should still write a Notion entry in that case, never drop the capture.
    """
    _check_burner_guard()
    _enforce_rate_discipline(shortcode, permalink)

    partial = ReelData(shortcode=shortcode, permalink=permalink)

    try:
        store.record_fetch()
        info = _run_ytdlp(permalink, cookiefile=None)
        return _info_to_reel_data(shortcode, permalink, info)
    except Exception as first_exc:  # noqa: BLE001 - yt-dlp raises assorted errors
        if not _looks_like_challenge(first_exc):
            raise FetchDegraded(f"fetch failed: {first_exc}", partial=partial) from first_exc

    last_exc: Optional[Exception] = None
    for delay in BACKOFF_SECONDS:
        time.sleep(delay)
        try:
            store.record_fetch()
            info = _run_ytdlp(permalink, cookiefile=BURNER_COOKIES_FILE)
            return _info_to_reel_data(shortcode, permalink, info)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if not _looks_like_challenge(exc):
                raise FetchDegraded(f"fetch failed: {exc}", partial=partial) from exc

    raise FetchDegraded(
        "repeated challenges from Instagram — refresh burner cookies", partial=partial
    ) from last_exc
