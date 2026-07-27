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


def derive_fallback_tags(named_entities: list[str], content_type: str) -> list[str]:
    """Tags from what the row genuinely carries. Entity-derived tags first
    (most specific), then the content-type tag, then the honest marker."""
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
        tags = [UNCATEGORIZED_TAG]
    return tags[:MAX_TOPICS]


def ensure_topics(extraction) -> list[str]:
    """The value that should actually be written. Returns extraction.topic_tags
    untouched when non-empty; otherwise the derived fallback."""
    if extraction.topic_tags:
        return extraction.topic_tags
    fallback = derive_fallback_tags(
        getattr(extraction, "named_entities", []) or [],
        getattr(extraction, "content_type", "") or "",
    )
    logger.warning(
        "topic guarantee: extraction had ZERO topic_tags — applying fallback %s "
        "(derived from named_entities + content_type, never invented)", fallback,
    )
    return fallback
