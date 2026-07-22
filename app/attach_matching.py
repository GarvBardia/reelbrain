"""Candidate scoring for /attach's no-exact-shortcode resolution path (see
PROGRESS.md). Deliberately NEVER auto-commits — this only ranks candidates
for a human to pick from via POST /attach/confirm. Replaces the old
"most-recently-updated Awaiting DM row" auto-pick fallback, which caused a
real cross-attachment (a resource meant for one reel landing on a different,
coincidentally-similar-sounding one with full "success" and no error).

Scoring quality fix (see PROGRESS.md — a real live case: DbAKlYYNEGY, the
correct target, scored 2 via its own gate_keyword "face" but got outranked
and squeezed out of the top-3 by three unrelated rows scoring 3-4 on pure
generic-word overlap, one of them via "google" — an artifact of every
fetched Google Doc's title ending in " - Google Docs", not real content).
Three changes address this directly: platform-suffix stripping, a
much-higher explicit weight for a genuine gate_keyword match, and a small
stopword list for generic connector words that carry no real topical signal.
"""
from __future__ import annotations

import re

_WORD_RE = re.compile(r"[a-z0-9]+")
MIN_WORD_LEN = 4  # ignore short/common words (the, for, and, ...) to reduce noise
TOP_N_CANDIDATES = 3
MIN_SCORE_THRESHOLD = 1  # at least one genuinely shared meaningful word

# A gate_keyword is a word the CREATOR deliberately chose for their own
# comment-gate — its literal presence in the DM'd resource is far stronger,
# far more deliberate evidence than an incidental shared word in free-text
# title/description. Named + tunable rather than a magic number buried in
# the scoring logic: at 5x a single generic word match, one real keyword
# match decisively outranks 1-4 generic-word coincidences (the exact shape
# of the DbAKlYYNEGY case above), without being so large that it can never
# lose to an even-more-specific multi-word overlap on a different candidate.
GATE_KEYWORD_MATCH_WEIGHT = 5

# Platform/site chrome that ends up in a fetched page's own <title> tag —
# never a real content signal, just as much noise as if we searched the
# reel's own database name. Deliberately narrow (known, common hosts this
# project's resources actually come from — see scripts/ingest_resources.py's
# classify_resource_url) rather than a general "strip everything after a
# dash" heuroistic, which would eat real content too eagerly.
_PLATFORM_SUFFIX_PATTERNS = [
    re.compile(r"\s*[-–|]\s*Google Docs\s*$", re.IGNORECASE),
    re.compile(r"\s*[-–|]\s*Google Drive\s*$", re.IGNORECASE),
    re.compile(r"\s*[-–|]\s*Google Sheets\s*$", re.IGNORECASE),
    re.compile(r"\s*[-–|]\s*Google Slides\s*$", re.IGNORECASE),
    re.compile(r"\s*·\s*GitHub\s*$", re.IGNORECASE),
    re.compile(r"^GitHub\s*[-–|]\s*", re.IGNORECASE),
]

# Generic connector/filler words common across nearly any reel's or
# resource's title (see PROGRESS.md's worked example: "using", "with",
# "based", "high", "create", "design" all inflated unrelated candidates'
# scores in the live incident). Deliberately small and hand-picked — not a
# full stopword corpus or NLP pipeline, just the clearly-generic words that
# carry no real topical signal on their own.
GENERIC_STOPWORDS = frozenset({
    "using", "with", "based", "create", "created", "design", "high", "your",
    "from", "this", "that", "have", "will", "into", "here", "more", "also",
    "than", "just", "some", "such", "very", "each", "about", "make", "made",
    "used", "free", "best", "step", "steps", "guide", "tips", "learn", "easy",
})


def _words(text: str) -> set[str]:
    return {w for w in _WORD_RE.findall((text or "").lower()) if len(w) >= MIN_WORD_LEN}


def _strip_platform_noise(title: str) -> str:
    """Removes a known platform-branding suffix/prefix from a fetched page
    title (e.g. "Looksmaxxingprompt - Google Docs" -> "Looksmaxxingprompt")
    so it never contributes false word-overlap matches."""
    cleaned = title or ""
    for pattern in _PLATFORM_SUFFIX_PATTERNS:
        cleaned = pattern.sub("", cleaned)
    return cleaned


def _gate_keyword_matches(gate_keyword: str, resource_text: str) -> bool:
    """Whole-word, case-insensitive check for the candidate's own gate_keyword
    inside the resource's fetched text. Deliberately NOT run through _words()'s
    MIN_WORD_LEN filter — a short, deliberately-chosen keyword (e.g. "AI") is
    still a meaningful, explicit signal regardless of length."""
    keyword = (gate_keyword or "").strip().lower()
    if not keyword:
        return False
    return re.search(r"\b" + re.escape(keyword) + r"\b", resource_text.lower()) is not None


def score_candidate(resource_title: str, resource_description: str, candidate: dict) -> int:
    """candidate: {"title", "note", "topics" (list[str]), "gate_keyword"}.

    Score = GATE_KEYWORD_MATCH_WEIGHT (if the candidate's own gate_keyword
    appears verbatim in the resource's fetched text) + count of distinct
    meaningful, non-generic words shared between the resource's
    title+description and the candidate's own title/note/topics."""
    clean_title = _strip_platform_noise(resource_title)
    resource_text = f"{clean_title} {resource_description or ''}"

    score = 0
    if _gate_keyword_matches(candidate.get("gate_keyword") or "", resource_text):
        score += GATE_KEYWORD_MATCH_WEIGHT

    resource_words = _words(resource_text) - GENERIC_STOPWORDS
    if resource_words:
        candidate_text = " ".join([
            candidate.get("title") or "",
            candidate.get("note") or "",
            " ".join(candidate.get("topics") or []),
        ])
        candidate_words = _words(candidate_text) - GENERIC_STOPWORDS
        score += len(resource_words & candidate_words)

    return score


def rank_candidates(resource_title: str, resource_description: str, candidates: list[dict]) -> list[dict]:
    """Top TOP_N_CANDIDATES candidates with score >= MIN_SCORE_THRESHOLD,
    each annotated with its own "match_score" — sorted by score descending,
    ties broken by most-recently-created first. Returns [] when nothing
    clears the threshold; callers must treat that as "unresolved", never
    silently falling back to picking one anyway."""
    scored = []
    for candidate in candidates:
        score = score_candidate(resource_title, resource_description, candidate)
        if score >= MIN_SCORE_THRESHOLD:
            scored.append({**candidate, "match_score": score})
    scored.sort(key=lambda c: (c["match_score"], c.get("created_at") or ""), reverse=True)
    return scored[:TOP_N_CANDIDATES]
