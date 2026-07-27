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

# A reel's named_entities are the exact tools/products it called out by name.
# Finding one verbatim in a resource is nearly as deliberate a signal as the
# creator's own gate keyword, so it scores just below it. Capped so a reel with
# many entities can't win on volume alone.
NAMED_ENTITY_MATCH_WEIGHT = 4
MAX_NAMED_ENTITY_HITS = 2

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


# A word shared with the resource is only evidence to the extent that it is
# RARE across the candidate pool. "claude" appears in half these reels and says
# nothing; "firecrawl" appears in one and says everything. Without this, scoring
# a resource's FULL text (hundreds of words, not just a meta description) simply
# rewards whichever candidate is most verbose -- measured at 33% top-1 on the
# real 12-pair/92-row fixture before this change.
RARE_WORD_MAX_POOL_FRACTION = 0.15   # in <=15% of candidates == distinctive
RARE_WORD_WEIGHT = 5
COMMON_WORD_WEIGHT = 1
# Overlap is deliberately NOT capped. Capping it was tried and measurably
# HURT: it compressed scores into large ties (top-1 fell 33% -> 8% on the real
# fixture), and ties are exactly where ranking stops carrying information.
# Rarity weighting is what keeps long documents from winning on volume --
# a cap is the wrong tool for that job.
MAX_WORD_OVERLAP_SCORE = 10_000

# Recency tiebreaker: when two candidates land within this many points, prefer
# the more recently created gate. You normally attach a DM'd resource within
# days of commenting, so the newer open gate is the better bet -- but this is
# deliberately a TIEBREAK, never able to overturn a real scoring lead.
RECENCY_TIEBREAK_MAX_GAP = 1


def _document_frequencies(candidates: list[dict]) -> dict[str, int]:
    """How many candidates each meaningful word appears in."""
    frequencies: dict[str, int] = {}
    for candidate in candidates:
        for word in _candidate_words(candidate):
            frequencies[word] = frequencies.get(word, 0) + 1
    return frequencies


def _candidate_words(candidate: dict) -> set[str]:
    """Every meaningful word describing a candidate. named_entities is included
    when present -- it's the most specific signal a reel carries (exact tool
    names) -- but it is NOT persisted as a Notion property today, so in the live
    /attach path this is usually just title+note+topics. See PROGRESS.md."""
    text = " ".join([
        candidate.get("title") or "",
        candidate.get("note") or "",
        " ".join(candidate.get("topics") or []),
        " ".join(candidate.get("named_entities") or []),
        " ".join(candidate.get("supporting_points") or []),
    ])
    return _words(text) - GENERIC_STOPWORDS


def score_candidate(
    resource_title: str,
    resource_description: str,
    candidate: dict,
    doc_frequencies: dict[str, int] | None = None,
    pool_size: int = 1,
) -> int:
    """candidate: {"title", "note", "topics", "gate_keyword", and optionally
    "named_entities"/"supporting_points"}.

    Score =
      GATE_KEYWORD_MATCH_WEIGHT   if the candidate's own gate_keyword appears
                                  verbatim in the resource text, PLUS
      NAMED_ENTITY_MATCH_WEIGHT   per named_entity of the reel appearing
                                  verbatim in the resource (capped), PLUS
      a rarity-weighted word overlap, itself capped at MAX_WORD_OVERLAP_SCORE
      so sheer document length can never out-vote a deliberate keyword.

    doc_frequencies/pool_size come from rank_candidates; scoring a candidate in
    isolation (no pool context) falls back to flat weighting.
    """
    clean_title = _strip_platform_noise(resource_title)
    resource_text = f"{clean_title} {resource_description or ''}"

    score = 0
    if _gate_keyword_matches(candidate.get("gate_keyword") or "", resource_text):
        score += GATE_KEYWORD_MATCH_WEIGHT

    # A named entity is a tool the reel SPECIFICALLY named; finding it verbatim
    # in the resource is nearly as strong as the creator's own gate keyword.
    entity_hits = sum(
        1 for entity in (candidate.get("named_entities") or [])
        if _gate_keyword_matches(entity, resource_text)
    )
    score += min(entity_hits, MAX_NAMED_ENTITY_HITS) * NAMED_ENTITY_MATCH_WEIGHT

    resource_words = _words(resource_text) - GENERIC_STOPWORDS
    if resource_words:
        shared = resource_words & _candidate_words(candidate)
        if doc_frequencies:
            rare_cutoff = max(1, int(pool_size * RARE_WORD_MAX_POOL_FRACTION))
            overlap = sum(
                RARE_WORD_WEIGHT if doc_frequencies.get(word, 0) <= rare_cutoff
                else COMMON_WORD_WEIGHT
                for word in shared
            )
        else:
            overlap = len(shared)
        score += min(overlap, MAX_WORD_OVERLAP_SCORE)

    return score


# Confidence is derived from the GAP between #1 and #2, not from the raw
# score: a top score of 6 means very different things when the runner-up is 1
# versus 5. A clear gap is what actually says "this is the right row".
HIGH_CONFIDENCE_GAP = 4   # >= GATE_KEYWORD_MATCH_WEIGHT - 1: a keyword-level lead
MEDIUM_CONFIDENCE_GAP = 2


def confidence_for(ranked: list[dict]) -> str:
    """"high" | "medium" | "low" for a ranked candidate list.

    A lone candidate is judged on its own score instead of a gap: with nothing
    to compare against, only a genuine gate_keyword hit earns "high".
    """
    if not ranked:
        return "low"
    top = ranked[0]["match_score"]
    if len(ranked) == 1:
        return "high" if top >= GATE_KEYWORD_MATCH_WEIGHT else "medium" if top >= MEDIUM_CONFIDENCE_GAP else "low"
    gap = top - ranked[1]["match_score"]
    if gap >= HIGH_CONFIDENCE_GAP:
        return "high"
    if gap >= MEDIUM_CONFIDENCE_GAP:
        return "medium"
    return "low"


def rank_candidates(resource_title: str, resource_description: str, candidates: list[dict]) -> list[dict]:
    """Top TOP_N_CANDIDATES candidates with score >= MIN_SCORE_THRESHOLD,
    each annotated with its own "match_score" — sorted by score descending,
    ties broken by most-recently-created first. Returns [] when nothing
    clears the threshold; callers must treat that as "unresolved", never
    silently falling back to picking one anyway."""
    doc_frequencies = _document_frequencies(candidates)
    pool_size = len(candidates)

    scored = []
    for candidate in candidates:
        score = score_candidate(
            resource_title, resource_description, candidate,
            doc_frequencies=doc_frequencies, pool_size=pool_size,
        )
        if score >= MIN_SCORE_THRESHOLD:
            scored.append({**candidate, "match_score": score})

    # Primary sort: score. Recency is applied only as a TIEBREAK below, so it
    # can reorder near-ties without ever overturning a real scoring lead.
    scored.sort(key=lambda c: (c["match_score"], c.get("created_at") or ""), reverse=True)
    scored = _apply_recency_tiebreak(scored)
    return scored[:TOP_N_CANDIDATES]


def _apply_recency_tiebreak(scored: list[dict]) -> list[dict]:
    """Within RECENCY_TIEBREAK_MAX_GAP points of each other, prefer the more
    recently created gate — you normally attach a DM'd resource within days of
    commenting. Applied to the leading cluster only, so it decides close calls
    and nothing else."""
    if len(scored) < 2:
        return scored
    top_score = scored[0]["match_score"]
    cluster = [c for c in scored if top_score - c["match_score"] <= RECENCY_TIEBREAK_MAX_GAP]
    rest = scored[len(cluster):]
    cluster.sort(key=lambda c: c.get("created_at") or "", reverse=True)
    return cluster + rest
