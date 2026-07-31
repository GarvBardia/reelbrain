"""No reel may exist without topics (Phase H).

Rows kept landing with empty Topics — which breaks more than it looks like:
a topic-less reel has no topic note to link it, so it becomes an orphan in the
vault graph (33 of them at one point), it's invisible when browsing by subject,
and its Priority can never be raised by a CLAUDE_KEYWORD topic match.

Rather than keep sweeping them up after the fact, this makes an empty Topics
field structurally impossible via a three-step chain:

  1. RETRY  — the extraction path asks Gemini once more, explicitly demanding
              3-6 tags (in gemini_pipe, so it costs at most one extra call and
              only when the first answer came back empty).
  2. DERIVE — if still empty, build tags from what the row DOES have:
              named_entities (slugified) + content_type. These are honest,
              traceable tags, not invented subject matter.
  3. GUARD  — notion_writer refuses to write a Saves row with zero topics: it
              logs loudly and applies the derive step rather than persisting
              an empty field. This is the backstop that makes the guarantee
              structural instead of merely likely.

Never invents a subject. If a row genuinely has nothing — no entities, no
content type — it gets UNCATEGORIZED_TAG, which is a truthful label and is
exactly what scripts/enforce_topics.py looks for later.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger("reelbrain.topic_guarantee")

MIN_TOPICS = 3
MAX_TOPICS = 6
# A truthful "we don't know" marker. Deliberately a real tag rather than an
# empty list: it keeps the row linkable in the vault and makes the gap
# queryable instead of invisible.
UNCATEGORIZED_TAG = "uncategorized"
# A row that HAS real content but whose extraction never succeeded. Distinct
# from UNCATEGORIZED_TAG on purpose — see the note below on why conflating them
# lost 19 rows' worth of already-fetched content.
PENDING_EXTRACTION_TAG = "pending-extraction"
# Words of caption/main_point below which a row genuinely has nothing to
# categorize. Matches CHEAP_MODEL_GUIDE.md's "don't attempt extraction under
# ~10 words" floor, so the two ends of the pipeline agree on what "empty" means.
MIN_CONTENT_WORDS = 10
PLACEHOLDER_MAIN_POINT = "No caption or transcript available."

# content_type -> a defensible topic tag. Not a subject guess: it describes the
# SHAPE of the content, which we do know.
_CONTENT_TYPE_TAGS = {
    "tutorial": "tutorials",
    "insight": "insights",
    "resource_drop": "resource-sharing",
    "motivation": "motivation",
    "news": "tech-news",
    "entertainment": "entertainment",
}


def _slugify_tag(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").strip().lower()).strip("-")
    return slug[:40]


def has_real_content(main_point: str) -> bool:
    """Does this row actually carry something extractable?

    The distinction that matters: an empty named_entities list can mean either
    "we looked and there was nothing" or "extraction never ran". The caption
    itself is what tells them apart."""
    text = (main_point or "").strip()
    if not text or text == PLACEHOLDER_MAIN_POINT:
        return False
    return len(text.split()) >= MIN_CONTENT_WORDS


def derive_fallback_tags(named_entities: list[str], content_type: str,
                         has_content: bool = False) -> list[str]:
    """Tags from what the row genuinely carries. Entity-derived tags first
    (most specific), then the content-type tag, then the honest marker.

    INCIDENT (2026-07-31): `uncategorized` used to be the answer for BOTH
    "nothing to categorize" and "extraction failed, so we have no entities
    yet" — a transient failure recorded as a permanent verdict. 19 rows with
    real captions (up to 295 words) sat labelled `uncategorized`, which reads as
    "we looked and found nothing" and left the actual state — extraction never
    succeeded — recorded nowhere and unqueryable. With has_content=True those
    rows now get PENDING_EXTRACTION_TAG instead, so `uncategorized` means only
    what it says, and "needs another extraction attempt" is a state you can
    query for."""
    tags: list[str] = []
    for entity in named_entities or []:
        slug = _slugify_tag(entity)
        # A multi-word entity slug ("andrej-karpathy-skills") is a fine tag; a
        # bare number or single char is not.
        if len(slug) >= 3 and not slug.isdigit() and slug not in tags:
            tags.append(slug)
        if len(tags) >= MAX_TOPICS - 1:
            break

    content_tag = _CONTENT_TYPE_TAGS.get(content_type or "")
    if content_tag and content_tag not in tags:
        tags.append(content_tag)

    if not tags:
        # Content present but nothing derivable => extraction hasn't succeeded
        # yet, which is a RETRYABLE state, not a verdict about the content.
        tags = [PENDING_EXTRACTION_TAG if has_content else UNCATEGORIZED_TAG]
    return tags[:MAX_TOPICS]


def ensure_topics(extraction) -> list[str]:
    """The value that should actually be written. Returns extraction.topic_tags
    untouched when non-empty; otherwise the derived fallback."""
    if extraction.topic_tags:
        return extraction.topic_tags
    fallback = derive_fallback_tags(
        getattr(extraction, "named_entities", []) or [],
        getattr(extraction, "content_type", "") or "",
        has_content=has_real_content(getattr(extraction, "main_point", "") or ""),
    )
    logger.warning(
        "topic guarantee: extraction had ZERO topic_tags — applying fallback %s "
        "(derived from named_entities + content_type, never invented)", fallback,
    )
    return fallback
