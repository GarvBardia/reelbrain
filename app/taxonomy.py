"""Canonical taxonomy: the tag merge map and the 12 parent categories.

Single source of truth shared by three callers so they can never drift apart:
  * scripts/apply_taxonomy.py  — rewrites Notion Topics using MERGES.
  * app/obsidian_sync.py       — groups the vault index under PARENTS.
  * scripts/vault_librarian.py — flags NEW tags that look like near-duplicates
    of a canonical tag (drift since the merge), reusing normalized_similarity.

Everything here is PURE (no Notion, no filesystem) so the transform and its
priority-invariance guarantee are testable in isolation. Derived from
TAXONOMY_PROPOSAL.md (approved 2026-07-28); see that file for the row-by-row
impact analysis behind the deliberate non-merges below.
"""
from __future__ import annotations

from difflib import SequenceMatcher

# --- the merge map (TAXONOMY_PROPOSAL.md §2) --------------------------------------
#
# Left tag is REPLACED by the right tag on every row that carries it. These are
# true spelling/concept duplicates, NOT the parent grouping (that is PARENTS,
# and stays Obsidian-only -- Notion keeps the flat child tags).
#
# DELIBERATELY ABSENT (proposal §3, and re-verified before writing): mcp-servers,
# fable-5, agentic-os. Merging mcp-servers into a non-"mcp" tag would DEMOTE its
# rows from High (compute_priority substring-matches "mcp"); merging fable-5 /
# agentic-os into claude-ai would PROMOTE their rows to High. All three are
# grouped under a parent but never tag-merged.
MERGES: dict[str, str] = {
    "claude": "claude-ai",
    "claude-code": "claude-ai",
    "claude-mcp": "claude-ai",
    "ai-workflow": "ai-workflows",
    "ai-automation": "automation",
    "workflow-automation": "automation",
    "business-automation": "automation",
    "artificial-intelligence": "ai-tools",
    "ai-generation": "generative-ai",
    "3d-motion-design": "motion-graphics",
    "animation-workflow": "ai-animation",
    "software-development": "developer-tools",
    "software-engineering": "developer-tools",
    "github-repositories": "open-source",
    "social-media-strategy": "social-media-growth",
    "sales-strategies": "sales-automation",
    "outreach-automation": "cold-outreach",
    "career-advice": "career-growth",
    "saas-builder": "saas-ideas",
    "saas-growth": "saas-ideas",
}

# Tags that must never be merged even if a future proposal is careless -- the
# apply step asserts none of these appears as a MERGES key. See §3.
PROTECTED_TAGS: frozenset[str] = frozenset({"mcp-servers", "fable-5", "agentic-os"})

# --- the 12 parent categories (TAXONOMY_PROPOSAL.md §1) ---------------------------
#
# Obsidian-index grouping ONLY. Child tags stay individual Notion tags; the
# parent just groups them in the vault index. A tag may be listed here under its
# POST-merge canonical name. `near-duplicate` is an automation tag, excluded.
PARENTS: dict[str, list[str]] = {
    "claude-ecosystem": [
        "claude-ai", "claude-skills", "mcp-servers", "fable-5", "agentic-os",
    ],
    "ai-tools": [
        "ai-tools", "ai-plugins", "gemini-ai", "midjourney", "luma-dream-machine",
        "arcads-ai", "emergent-ai", "notebooklm", "local-ai",
    ],
    "ai-agents-automation": [
        "ai-agents", "automation", "agentic-ai", "ai-workflows",
    ],
    "developer-tools": [
        "developer-tools", "open-source", "ai-coding", "vibe-coding",
        "free-tier", "privacy-first", "cybersecurity",
    ],
    "web-and-design": [
        "web-design", "web-development", "ui-design", "product-design",
        "interactive-design", "portfolio-design", "3d-design",
        "scroll-animation", "no-code", "design-systems",
    ],
    "content-and-media": [
        "content-creation", "ai-video", "ai-animation", "video-editing",
        "motion-graphics", "image-generation", "generative-ai", "content-strategy",
    ],
    "sales-and-leads": [
        "lead-generation", "cold-outreach", "cold-calling", "email-outreach",
        "sales-automation", "sales-pipeline", "client-acquisition", "crm",
    ],
    "business-building": [
        "startups", "entrepreneurship", "solopreneurship", "business-launch",
        "saas-ideas", "app-ideas", "funding", "yc", "investment", "finance",
    ],
    "marketing-and-brand": [
        "personal-branding", "social-media-growth", "instagram", "meta-ads",
        "ai-marketing", "digital-advertising", "seo-optimization",
        "creator-economy", "agency-growth",
    ],
    "productivity-knowledge": [
        "productivity-hacks", "prompt-engineering", "ai-prompts", "obsidian",
        "second-brain", "life-hacks", "ai-tutorials", "creative-framework",
        "ai-research",
    ],
    "career": [
        "career-advice", "career-growth", "job-hunting", "interview-prep",
        "resource-sharing",
    ],
    "income-and-products": [
        "passive-income", "digital-products", "side-hustle", "airbnb",
        "property-management", "cryptocurrency",
    ],
}

# The automation tag that is explicitly NOT a topic (proposal preamble).
# Pipeline-state markers, not subjects: they describe our processing status,
# so drift detection and parent grouping must both ignore them.
NON_TOPIC_TAGS: frozenset[str] = frozenset({
    "near-duplicate", "uncategorized", "pending-extraction",
})

# Intentionally-loose one-offs kept as their own tags (proposal §1): too
# specific to force under a parent, but still canonical — NOT drift.
LOOSE_TAGS: frozenset[str] = frozenset({
    "looksmaxxing", "style-ai", "gamification", "wildlife-conservation",
    "tech-news", "mobile-development", "react-native", "app-development",
})

# Similarity at/above which a NEW tag is flagged as a possible near-duplicate of
# a canonical one. Tuned so "ai-workflow"/"ai-workflows" (~0.95) trips it but
# genuinely distinct tags do not. Report-only — never an auto-merge threshold.
DRIFT_SIMILARITY_THRESHOLD = 0.85


def canonical_tags() -> set[str]:
    """The known-good post-merge taxonomy: every parent's children, every merge
    TARGET, and the loose one-offs. A live tag outside this set is 'new' and a
    candidate for the drift check."""
    tags: set[str] = set(LOOSE_TAGS) | set(MERGES.values()) | set(NON_TOPIC_TAGS)
    for children in PARENTS.values():
        tags.update(children)
    return tags


def apply_merges(topics: list[str], merges: dict[str, str] = MERGES) -> list[str]:
    """Rewrite a row's topics through the merge map: each merged-away tag becomes
    its target, de-duplicated, ORDER PRESERVED (first occurrence wins). Adding a
    target that a row already carries collapses to one — that is the whole point,
    and it is why row tag-counts can legitimately shrink while row COUNT cannot.
    """
    out: list[str] = []
    seen: set[str] = set()
    for tag in topics:
        canonical = merges.get(tag, tag)
        if canonical not in seen:
            seen.add(canonical)
            out.append(canonical)
    return out


def _parent_of() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for parent, children in PARENTS.items():
        for child in children:
            mapping[child] = parent
    return mapping


_PARENT_OF = _parent_of()


def parent_for_topic(topic: str) -> str | None:
    """The parent category a (post-merge) topic belongs to, or None if it's one
    of the intentionally-loose one-off tags."""
    return _PARENT_OF.get(topic)


def group_topics_under_parents(topics: list[str]) -> tuple[dict[str, list[str]], list[str]]:
    """Split topics into {parent: [topics...]} in PARENTS order plus a list of
    leftover loose topics (no parent). Only parents that actually have a topic
    present are returned, so an empty category never prints an empty header."""
    grouped: dict[str, list[str]] = {}
    loose: list[str] = []
    known = set(topics)
    for parent, children in PARENTS.items():
        present = [c for c in children if c in known]
        if present:
            grouped[parent] = present
    placed = {t for members in grouped.values() for t in members}
    for topic in topics:
        if topic not in placed and topic not in NON_TOPIC_TAGS:
            loose.append(topic)
    return grouped, loose


def find_tag_drift(
    live_tags: list[str],
    threshold: float = DRIFT_SIMILARITY_THRESHOLD,
) -> list[dict]:
    """Flag live tags that look like near-duplicates of a CANONICAL tag but
    aren't one — i.e. taxonomy drift since the last merge. Report only: returns
    [{new_tag, nearest_canonical, score}] sorted worst-first, NEVER merges.

    A tag already in the canonical set is fine by definition and skipped. For
    each new tag we report only its single closest canonical match above the
    threshold (a suggestion for a human to confirm, not a decision)."""
    canonical = canonical_tags()
    findings: list[dict] = []
    for tag in sorted(set(live_tags)):
        if tag in canonical or tag in NON_TOPIC_TAGS:
            continue
        best_tag, best_score = "", 0.0
        for cand in canonical:
            score = normalized_similarity(tag, cand)
            if score > best_score:
                best_tag, best_score = cand, score
        if best_score >= threshold:
            findings.append({"new_tag": tag, "nearest_canonical": best_tag,
                             "score": round(best_score, 3)})
    return sorted(findings, key=lambda f: -f["score"])


def normalized_similarity(a: str, b: str) -> float:
    """0..1 similarity between two tag strings, hyphen/space-insensitive. Used by
    the Phase J2 drift check to flag a NEW tag that looks like a near-duplicate
    of an existing canonical one — never to auto-merge, only to report."""
    na = a.lower().replace("_", "-").replace(" ", "-")
    nb = b.lower().replace("_", "-").replace(" ", "-")
    return SequenceMatcher(None, na, nb).ratio()
