"""Candidate scoring for /attach's no-exact-shortcode resolution path (see
PROGRESS.md). Deliberately NEVER auto-commits — this only ranks candidates
for a human to pick from via POST /attach/confirm. Replaces the old
"most-recently-updated Awaiting DM row" auto-pick fallback, which caused a
real cross-attachment (a resource meant for one reel landing on a different,
coincidentally-similar-sounding one with full "success" and no error).
"""
from __future__ import annotations

import re

_WORD_RE = re.compile(r"[a-z0-9]+")
MIN_WORD_LEN = 4  # ignore short/common words (the, for, and, ...) to reduce noise
TOP_N_CANDIDATES = 3
MIN_SCORE_THRESHOLD = 1  # at least one genuinely shared meaningful word


def _words(text: str) -> set[str]:
    return {w for w in _WORD_RE.findall((text or "").lower()) if len(w) >= MIN_WORD_LEN}


def score_candidate(resource_title: str, resource_description: str, candidate: dict) -> int:
    """candidate: {"title", "note", "topics" (list[str]), "gate_keyword"}.
    Score = count of distinct meaningful words shared between the resource's
    fetched title+description and the candidate's own text fields — the same
    substring-overlap idea the old note/title matching used, just turned into
    a rankable score instead of a binary auto-commit trigger."""
    resource_words = _words(resource_title) | _words(resource_description)
    if not resource_words:
        return 0
    candidate_text = " ".join([
        candidate.get("title") or "",
        candidate.get("note") or "",
        " ".join(candidate.get("topics") or []),
        candidate.get("gate_keyword") or "",
    ])
    candidate_words = _words(candidate_text)
    return len(resource_words & candidate_words)


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
