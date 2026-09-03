"""Mycelium — the READ-ONLY public API behind the marketing/showcase frontend.

NAMING (2026-08-16): the product's public name is **Mycelium**. "reelbrain"
survives as the internal repo/package codename only -- renaming the Python
package, the Render service, the SQLite file and every import path would touch
almost every file in the repo for zero user-visible benefit, and this project
has been bitten before by large mechanical renames (see PROGRESS.md). Every
string a visitor can actually SEE says Mycelium; the plumbing keeps its old
name. `app/main.py`'s FastAPI title is the one internal exception -- it shows
up in the public /docs page, so it was renamed too.

THREE HARD RULES for everything in this module:

  1. READ-ONLY. Nothing here writes to Notion, SQLite, or the vault. The
     public internet gets no path to mutate state, so the worst a bug here can
     do is show something it shouldn't -- never corrupt something.
  2. REDACTED BY DEFAULT. `_public_reel` is an allow-list, not a deny-list:
     it names the exact fields that go out, so a NEW private property added to
     the Notion schema later cannot leak by being forgotten. The genuinely
     private fields are the comment-gate keyword and the attached gate
     resource URL (the whole *point* of a comment gate is that the link is
     earned, and re-publishing it would undercut the creators this corpus is
     built from), the raw transcript (a full verbatim copy of someone else's
     content), and the user's own private capture notes.
  3. CACHED. A Notion full-corpus query is ~3 paginated round trips and takes
     seconds. A public URL can be hit arbitrarily often, so every endpoint is
     served from one shared in-process TTL cache -- same pattern as
     notion_writer.get_live_taxonomy, and for the same reason.
"""
from __future__ import annotations

import logging
import os
import re
import time
from collections import Counter, defaultdict, deque
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Query, Request

from app import taxonomy

logger = logging.getLogger("reelbrain.public_api")

router = APIRouter(prefix="/api/public", tags=["public"])

# --- what the public never sees ---------------------------------------------------
#
# Documented here as data (not just prose) so the redaction test can assert
# against the same list the code is written from.
PRIVATE_NOTION_PROPERTIES = (
    "Gate keyword",      # the comment-gate magic word -- earned, not published
    "Gate resource",     # the DM'd link the gate protects
    "My note",           # the user's own private capture-time note
)
# Body blocks that must never be serialized outward. The transcript is a full
# verbatim copy of a creator's audio; "Raw caption" is likewise their text.
# Named here for the record, but the detail endpoint below (_reel_body_detail)
# enforces this structurally rather than by matching these names: it reads
# ONLY the page's TOP-LEVEL block children and never descends into a toggle at
# all, of any title. Transcript, Raw caption and Research Context are all
# written as toggle children (see notion_writer._build_children) precisely so
# a scan that stops at the top level can never reach them, title-matching
# or not.
PRIVATE_BODY_TOGGLES = ("Transcript", "Raw caption")

# Rows whose whole point is that they are NOT presentable. Excluding these is
# curation, not redaction -- a showcase of a knowledge base should show the
# knowledge, not the junk drawer.
HIDDEN_STATUS_LABELS = frozenset({"🗄 Archived", "🗑 Low signal", "⚠️ Failed — retry"})
PLACEHOLDER_TITLE = "No caption or transcript available."

# --- category presentation ---------------------------------------------------------
#
# The 12 parents come from app/taxonomy.py (the single source of truth for the
# taxonomy itself). Only the DISPLAY concerns -- human label and colour -- live
# here. Deliberately vibrant and maximally distinct rather than a pastel/muted
# ramp: on the landing-page graph these are read at a glance, as ~12 competing
# points of colour on white, so hue separation matters more than harmony.
CATEGORY_COLORS: dict[str, str] = {
    "claude-ecosystem":       "#FF5A1F",  # vivid orange
    "ai-agents-automation":   "#2563EB",  # strong blue
    "ai-tools":               "#7C3AED",  # violet
    "developer-tools":        "#059669",  # emerald
    "web-and-design":         "#DB2777",  # magenta
    "content-and-media":      "#F59E0B",  # amber
    "sales-and-leads":        "#DC2626",  # red
    "business-building":      "#0891B2",  # cyan
    "marketing-and-brand":    "#9333EA",  # purple
    "productivity-knowledge": "#16A34A",  # green
    "career":                 "#4F46E5",  # indigo
    "income-and-products":    "#D97706",  # dark amber
}
OTHER_CATEGORY = "other"
OTHER_COLOR = "#64748B"  # slate -- deliberately the least saturated: it's the
                         # "didn't fit a parent" bucket and should recede.

CATEGORY_LABELS: dict[str, str] = {
    "claude-ecosystem":       "Claude Ecosystem",
    "ai-agents-automation":   "AI Agents & Automation",
    "ai-tools":               "AI Tools",
    "developer-tools":        "Developer Tools",
    "web-and-design":         "Web & Design",
    "content-and-media":      "Content & Media",
    "sales-and-leads":        "Sales & Leads",
    "business-building":      "Business Building",
    "marketing-and-brand":    "Marketing & Brand",
    "productivity-knowledge": "Productivity & Knowledge",
    "career":                 "Career",
    "income-and-products":    "Income & Products",
    OTHER_CATEGORY:           "Other",
}


def category_color(slug: str) -> str:
    return CATEGORY_COLORS.get(slug, OTHER_COLOR)


def category_label(slug: str) -> str:
    return CATEGORY_LABELS.get(slug, slug.replace("-", " ").title())


# --- rate limiting -----------------------------------------------------------------
#
# Deliberately its OWN bucket, separate from main.py's write-path limiter: a
# public read endpoint backing a graph UI legitimately gets bursts (one page
# load can fire several calls), while /capture should stay tight. Kept in this
# module rather than imported from main.py to avoid a circular import -- main
# imports this router.
PUBLIC_RATE_LIMIT_MAX_PER_MINUTE = int(os.environ.get("PUBLIC_RATE_LIMIT_PER_MINUTE", "120"))
PUBLIC_RATE_LIMIT_WINDOW_SECONDS = 60
_public_rate_buckets: dict[str, deque[float]] = defaultdict(deque)


def check_public_rate_limit(request: Request) -> None:
    ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    bucket = _public_rate_buckets[ip]
    while bucket and now - bucket[0] > PUBLIC_RATE_LIMIT_WINDOW_SECONDS:
        bucket.popleft()
    if len(bucket) >= PUBLIC_RATE_LIMIT_MAX_PER_MINUTE:
        raise HTTPException(status_code=429, detail="rate limit exceeded — try again in a minute")
    bucket.append(now)


# --- the corpus, cached ------------------------------------------------------------

PUBLIC_CACHE_TTL_SECONDS = int(os.environ.get("PUBLIC_CACHE_TTL_SECONDS", "300"))
_CORPUS_CACHE: dict = {"reels": [], "fetched_at": 0.0}


def _select_name(page: dict, prop: str) -> str:
    return (((page.get("properties", {}).get(prop) or {}).get("select")) or {}).get("name", "")


def _public_reel(page: dict) -> Optional[dict]:
    """ONE Notion page -> the redacted public shape, or None if it should not
    be published at all.

    Allow-list by construction: every field that goes out is named right here.
    A new private property added to the Notion schema later is invisible to
    this function by default, which is the whole point -- see rule 2 in the
    module docstring."""
    from app import notion_writer

    digest = notion_writer.extract_digest_fields(page)
    shortcode = digest["shortcode"]
    if not shortcode:
        return None
    if digest["status_label"] in HIDDEN_STATUS_LABELS:
        return None

    title = (digest["title"] or "").strip()
    if not title or title == PLACEHOLDER_TITLE or title.startswith("http"):
        # A placeholder or bare-permalink title means extraction never
        # succeeded -- there is genuinely nothing to show a visitor.
        return None

    props = page.get("properties", {})
    plain_summary = notion_writer._rt_text((props.get("Plain summary") or {}).get("rich_text"))
    suggested_action = notion_writer._rt_text((props.get("Suggested action") or {}).get("rich_text"))
    posted = ((props.get("Posted at") or {}).get("date") or {}).get("start", "")

    topics = taxonomy.canonicalize_plurals(taxonomy.apply_merges(list(digest["topics"])))
    real_topics = [t for t in topics if t not in taxonomy.NON_TOPIC_TAGS]
    category = _primary_category(real_topics)

    try:
        value_score = int(digest["value_score"])
    except (TypeError, ValueError):
        value_score = 3

    return {
        "shortcode": shortcode,
        "title": title,
        "plain_summary": plain_summary,
        "suggested_action": suggested_action,
        "topics": real_topics,
        "category": category,
        "category_label": category_label(category),
        "color": category_color(category),
        "value_score": value_score,
        "priority": digest["priority"],
        "content_type": _select_name(page, "Content type"),
        "named_entities": _safe_entities(digest["named_entities"]),
        # The Instagram permalink is the creator's own public post -- linking
        # back to it is attribution, not disclosure. The gate RESOURCE (the
        # DM'd payoff) is what stays private, and it is simply never read here.
        "permalink": digest["permalink"],
        "posted_at": posted,
    }


def _safe_entities(entities: list[str]) -> list[str]:
    """named_entities, minus anything URL-shaped.

    Two reasons, one of them defensive:
      1. QUALITY -- an entity is supposed to be the NAME of a tool or method
         ("Firecrawl", "Playwright CLI"). A bare "github.com" is not a name;
         it is noise that renders as a meaningless chip in the UI. Observed
         live on a real row.
      2. SAFETY -- named_entities is the only public field whose contents are
         free-form model output rather than a controlled vocabulary, which
         makes it the one plausible route by which a URL could ever reach a
         public response. There is no known path for a gate resource to land
         here (it arrives by DM long after extraction reads the reel), but
         this endpoint is on the open internet and the check costs nothing.
    """
    return [
        e for e in (entities or [])
        if not any(marker in e.lower() for marker in ("http://", "https://", "www.", ".com", ".io", ".ai/"))
    ]


def _primary_category(topics: list[str]) -> str:
    """The ONE category a reel is coloured and counted under. First topic with
    a known parent wins (topic order is meaningful -- the extraction prompt
    asks for the most relevant tag first); falls back to `other`.

    Distinct from categories_of() below on purpose: `primary` PARTITIONS the
    corpus (every reel has exactly one, so the stats page's category counts
    sum to the total and can be read as percentages), while `categories_of`
    is the MEMBERSHIP set used by the graph (a reel about Claude MCP servers
    for web designers genuinely belongs to both parents, and hiding that would
    throw away exactly the cross-links the graph exists to show)."""
    for topic in topics:
        parent = taxonomy.parent_for_topic(topic)
        if parent:
            return parent
    return OTHER_CATEGORY


def categories_of(reel: dict) -> set[str]:
    """EVERY category a reel touches, via any of its topics -- see the
    contrast with _primary_category above. Always includes the primary, so a
    reel is never absent from the category it is coloured as."""
    parents = {taxonomy.parent_for_topic(t) for t in reel["topics"]}
    parents = {p for p in parents if p}
    parents.add(reel["category"])
    return parents


def load_public_reels(force_refresh: bool = False) -> list[dict]:
    """The whole publishable corpus, redacted and cached.

    On a Notion failure this serves the last good snapshot rather than 500ing:
    a marketing site showing slightly stale numbers is strictly better than a
    marketing site showing an error page. Returns an empty list only on a cold
    cache with no prior successful fetch."""
    from app import notion_writer

    now = time.time()
    if not force_refresh and _CORPUS_CACHE["reels"] and (
        now - _CORPUS_CACHE["fetched_at"] < PUBLIC_CACHE_TTL_SECONDS
    ):
        return _CORPUS_CACHE["reels"]

    try:
        pages = notion_writer.find_saves_pages_since("1970-01-01T00:00:00")
    except Exception:  # noqa: BLE001 - a Notion hiccup must not take the site down
        logger.warning("public_api: Notion query failed — serving last cached corpus", exc_info=True)
        return _CORPUS_CACHE["reels"]

    reels = [r for r in (_public_reel(p) for p in pages) if r is not None]
    _CORPUS_CACHE["reels"] = reels
    _CORPUS_CACHE["fetched_at"] = now
    return reels


# --- per-reel detail (2026-09-04) ---------------------------------------------------
#
# main_point (== title), plain_summary, suggested_action, topics, value_score
# etc. all live in Notion PAGE PROPERTIES, which load_public_reels already
# fetches in one query for the whole corpus. supporting_points,
# steps_or_framework, resources_mentioned and quotable_lines are different in
# kind: notion_writer._build_children writes them as page BODY BLOCKS, which
# is a SEPARATE Notion API call per page (blocks.children.list). Folding that
# into load_public_reels would turn one corpus query into 1 + N page-block
# queries on every cache refresh -- for ~180 reels, a real risk of Notion rate
# limits, paid for on every visitor even though most reels in a paginated list
# are never opened.
#
# So this stays a SEPARATE, PER-SHORTCODE, ON-DEMAND fetch: only pays the
# extra Notion call for a reel a visitor actually expands, with its own short
# cache so re-opening the same reel doesn't re-fetch. The list endpoint
# (/reels) and its cache are completely unaffected by any of this.
_DETAIL_CACHE: dict[str, dict] = {}
DETAIL_CACHE_TTL_SECONDS = PUBLIC_CACHE_TTL_SECONDS

# Written by notion_writer._build_children as
# f"{resource.name} ({resource.type})" for a resource with NO stated URL (one
# WITH a URL is written as a bookmark block instead, which carries the URL but
# -- a real, pre-existing gap in the write path, not something introduced
# here -- no name or type; see the bookmark branch below). Read back with the
# exact inverse pattern.
_RESOURCE_PARAGRAPH_RE = re.compile(
    r"^(?P<name>.+) \((?P<type>tool|book|site|person|course|other)\)$"
)


def _reel_body_detail(page_id: str) -> dict:
    """The four block-derived sections for ONE reel, redacted by construction.

    Reads ONLY the page's top-level block children -- never recurses into a
    toggle's own children, which is where Transcript/Raw caption/Research
    Context live. That is what keeps this safe: there is no name-matching
    to get wrong, because the private content is structurally unreachable
    from a top-level-only scan. See the note on PRIVATE_BODY_TOGGLES.

    resources_mentioned here is never the comment-gate's DM'd payoff link --
    that lives in the entirely separate "Gate resource" PAGE PROPERTY
    (PRIVATE_NOTION_PROPERTIES), which no code path in this function reads.
    A resource here is something the creator stated OPENLY in the reel's own
    content (a tool/book/site/person/course they named), which is public
    information the moment it airs -- fundamentally different from a gate
    resource, which is deliberately unpublished until earned by a comment.
    """
    from app import notion_writer

    client = notion_writer._client()
    blocks: list[dict] = []
    cursor: Optional[str] = None
    while True:
        kwargs: dict = {"block_id": page_id}
        if cursor:
            kwargs["start_cursor"] = cursor
        response = client.blocks.children.list(**kwargs)
        blocks.extend(response["results"])
        if not response.get("has_more"):
            break
        cursor = response.get("next_cursor")

    supporting_points: list[str] = []
    steps_or_framework: list[str] = []
    resources_mentioned: list[dict] = []
    quotable_lines: list[str] = []

    for block in blocks:
        kind = block.get("type")
        if kind == "toggle":
            continue  # never descended into -- see the module docstring above
        if kind == "bulleted_list_item":
            text = notion_writer._rt_text(block["bulleted_list_item"].get("rich_text"))
            if text:
                supporting_points.append(text)
        elif kind == "numbered_list_item":
            text = notion_writer._rt_text(block["numbered_list_item"].get("rich_text"))
            if text:
                steps_or_framework.append(text)
        elif kind == "bookmark":
            url = block["bookmark"].get("url")
            if url:
                # Name/type were not preserved when this was written as a
                # bookmark (see _RESOURCE_PARAGRAPH_RE's comment) -- the URL
                # itself is the best available stand-in for a display name.
                #
                # Domain alone is not enough: caught on a real reel citing
                # four different github.com repos, which all collapsed to the
                # literal label "github.com" repeated four times -- correct
                # per-item but unreadable as a group, since nothing visually
                # distinguished one from another. The first path segment (a
                # GitHub org/user, an npm scope, a docs section) is usually
                # what actually identifies a specific link on a host that
                # hosts many unrelated things, so it's kept when present.
                parsed = urlparse(url)
                domain = parsed.netloc.removeprefix("www.") or url
                first_segment = next((s for s in parsed.path.split("/") if s), "")
                name = f"{domain}/{first_segment}" if first_segment else domain
                resources_mentioned.append({"name": name, "type": "site", "url": url})
        elif kind == "paragraph":
            text = notion_writer._rt_text(block["paragraph"].get("rich_text"))
            match = _RESOURCE_PARAGRAPH_RE.match(text)
            if match:
                resources_mentioned.append({
                    "name": match.group("name"),
                    "type": match.group("type"),
                    "url": None,
                })
            # A non-matching top-level paragraph is not expected (see the
            # module docstring on what _build_children writes at top level),
            # so it is skipped rather than shown -- better an omission than
            # surfacing something unvetted.
        elif kind == "quote":
            text = notion_writer._rt_text(block["quote"].get("rich_text"))
            if text:
                quotable_lines.append(text)
        # callout (main_point) and anything else: no public use for it here.

    return {
        "supporting_points": supporting_points,
        "steps_or_framework": steps_or_framework,
        "resources_mentioned": resources_mentioned,
        "quotable_lines": quotable_lines,
    }


def load_reel_detail(shortcode: str) -> Optional[dict]:
    """The cached, per-shortcode detail fetch behind GET /reels/{shortcode}/detail.

    Returns None only when Notion has no page for this shortcode at all
    (the route turns that into a 404). A Notion hiccup WHILE fetching blocks
    for a page that does exist degrades to empty sections rather than an
    error -- consistent with load_public_reels' own "never take the site
    down over a Notion wobble" rule; the base card the visitor already sees
    (title, summary, topics) does not depend on this call succeeding."""
    from app import notion_writer

    now = time.time()
    cached = _DETAIL_CACHE.get(shortcode)
    if cached and now - cached["fetched_at"] < DETAIL_CACHE_TTL_SECONDS:
        return cached["data"]

    try:
        page = notion_writer.find_page_by_shortcode(shortcode)
    except Exception:  # noqa: BLE001 - see load_public_reels
        logger.warning("public_api: shortcode lookup failed for %s", shortcode, exc_info=True)
        return cached["data"] if cached else None
    if not page:
        return None

    try:
        detail = _reel_body_detail(page["id"])
    except Exception:  # noqa: BLE001 - degrade, don't 500 an otherwise-fine reel
        logger.warning("public_api: block fetch failed for %s", shortcode, exc_info=True)
        detail = {
            "supporting_points": [], "steps_or_framework": [],
            "resources_mentioned": [], "quotable_lines": [],
        }

    detail["shortcode"] = shortcode
    _DETAIL_CACHE[shortcode] = {"data": detail, "fetched_at": now}
    return detail


# --- graph shaping -----------------------------------------------------------------

def build_graph(reels: list[dict], expand: Optional[str] = None) -> dict:
    """react-force-graph-2d shaped {nodes, links}.

    DEFAULTS TO THE CATEGORY LEVEL, deliberately: rendering 200+ reel nodes at
    once produces an unreadable hairball, which is the single most common way
    a graph visualisation fails to communicate anything. The default view is
    ~12 category nodes sized by how much sits under each; passing `expand=<slug>`
    adds only THAT category's reels, so the graph stays legible while still
    being explorable.

    Category<->category links are co-occurrence: two categories are linked when
    at least one reel carries topics from both, weighted by how often. That is
    a real signal from the corpus (which subjects the user actually saves
    together), not decorative edges.

    Membership here is categories_of (every category a reel touches), NOT the
    single primary category -- a reel tagged both `claude-ai` and `web-design`
    is genuinely in both, and counting it only once would both understate the
    smaller category and erase the very cross-link the graph exists to draw."""
    counts: Counter = Counter()
    for reel in reels:
        counts.update(categories_of(reel))
    # Every category with at least one reel, biggest first so the layout seeds
    # the important nodes near the centre.
    ordered = [slug for slug, _ in counts.most_common()]

    nodes: list[dict] = []
    for slug in ordered:
        nodes.append({
            "id": f"cat:{slug}",
            "label": category_label(slug),
            "type": "category",
            "category": slug,
            "color": category_color(slug),
            "count": counts[slug],
            # react-force-graph uses `val` for node size. sqrt keeps a
            # 60-reel category from dwarfing a 5-reel one into invisibility.
            "val": round(4 + (counts[slug] ** 0.5) * 2, 2),
        })

    links: list[dict] = []
    co_occurrence: Counter = Counter()
    for reel in reels:
        parents = sorted(categories_of(reel))
        for i, a in enumerate(parents):
            for b in parents[i + 1:]:
                co_occurrence[(a, b)] += 1
    for (a, b), weight in co_occurrence.items():
        links.append({
            "source": f"cat:{a}",
            "target": f"cat:{b}",
            "value": weight,
            "type": "co-occurrence",
        })

    expanded: list[str] = []
    if expand:
        expand = expand.strip().lower()
        # expand="all" (2026-09-01): every reel as a node at once, for the
        # frontend's dense default view -- distinct from a single expanded
        # category, which stays the exact behaviour it always was (target is
        # a one-element set, so a reel's categories_of() intersects it in at
        # most the one membership link it used to get).
        expand_all = expand == "all"
        if not expand_all and expand not in counts:
            raise HTTPException(status_code=404, detail=f"unknown category: {expand}")
        target = set(counts.keys()) if expand_all else {expand}
        expanded.extend(sorted(target) if expand_all else [expand])

        seen_reels: set[str] = set()
        for reel in reels:
            # A reel can belong to several categories (categories_of, not
            # just the primary) -- in "all" mode it gets one membership link
            # PER category it touches (the real cross-category signal this
            # graph exists to draw) but only ONE node, added the first time
            # it's encountered.
            reel_categories = categories_of(reel) & target
            if not reel_categories:
                continue
            if reel["shortcode"] not in seen_reels:
                seen_reels.add(reel["shortcode"])
                nodes.append({
                    "id": f"reel:{reel['shortcode']}",
                    # The SHORT readable label the graph draws next to the dot --
                    # plain_summary is written to be understandable cold, so it
                    # beats the (often jargon-heavy) title when present.
                    "label": _short_label(reel),
                    "type": "reel",
                    "category": reel["category"],
                    "color": reel["color"],
                    "value_score": reel["value_score"],
                    "shortcode": reel["shortcode"],
                    "val": round(1.5 + reel["value_score"] * 0.9, 2),
                })
            for cat in reel_categories:
                links.append({
                    "source": f"cat:{cat}",
                    "target": f"reel:{reel['shortcode']}",
                    "value": 1,
                    "type": "membership",
                })

    return {
        "nodes": nodes,
        "links": links,
        "level": "category" if not expanded else "expanded",
        "expanded": expanded,
        "categories": [
            {"slug": s, "label": category_label(s), "color": category_color(s), "count": counts[s]}
            for s in ordered
        ],
        "total_reels": len(reels),
    }


GRAPH_LABEL_MAX_CHARS = 68


def _short_label(reel: dict) -> str:
    """A label that fits next to a dot. Prefers plain_summary's first sentence
    (written for a cold reader), else the title. Truncated on a word boundary
    so the graph never renders a word cut in half."""
    text = (reel.get("plain_summary") or "").strip() or (reel.get("title") or "").strip()
    if not text:
        return reel.get("shortcode", "")
    first = text.split(". ")[0].strip().rstrip(".")
    if len(first) <= GRAPH_LABEL_MAX_CHARS:
        return first
    clipped = first[:GRAPH_LABEL_MAX_CHARS].rsplit(" ", 1)[0]
    return f"{clipped}…"


# --- stats -------------------------------------------------------------------------

def build_stats(reels: list[dict]) -> dict:
    """Aggregate counters for the landing page. Every number here is derived
    from the live corpus -- there are no hardcoded marketing figures, so the
    page can never claim something the data doesn't support."""
    counts = Counter(r["category"] for r in reels)
    topic_counts: Counter = Counter()
    for reel in reels:
        topic_counts.update(reel["topics"])
    entity_counts: Counter = Counter()
    for reel in reels:
        entity_counts.update(reel["named_entities"])

    actionable = sum(
        1 for r in reels
        if r["suggested_action"] and r["suggested_action"] != "none — informational"
    )
    return {
        "total_reels": len(reels),
        "total_categories": len(counts),
        "total_topics": len(topic_counts),
        "total_entities": len(entity_counts),
        "actionable_items": actionable,
        "high_priority": sum(1 for r in reels if r["priority"] == "High"),
        "top_categories": [
            {"slug": s, "label": category_label(s), "color": category_color(s), "count": n}
            for s, n in counts.most_common(6)
        ],
        "top_topics": [{"topic": t, "count": n} for t, n in topic_counts.most_common(15)],
        "top_entities": [{"entity": e, "count": n} for e, n in entity_counts.most_common(10)],
    }


# --- scout queue -------------------------------------------------------------------

SCOUT_QUEUE_MIN_VALUE_SCORE = 4


def build_scout_queue(reels: list[dict], limit: int = 25) -> list[dict]:
    """The Implementation Queue: saves that are genuinely worth ACTING on.

    Derived live from Notion rather than parsed out of the vault's
    IMPLEMENTATION_QUEUE.md, because the vault is a local-only artefact that
    does not exist on Render -- the same "Notion is the durable source, the
    local mirror is not" lesson that already forced fixes to the digests
    (FIX 2) and the taxonomy (see PROGRESS.md). A queue that renders as empty
    in production because its data source only exists on one laptop would be a
    repeat of a bug this project has already paid for twice.

    Criteria: a real suggested_action (not the "none — informational" marker)
    AND value_score >= 4. Ranked by value_score, then High-priority first."""
    candidates = [
        r for r in reels
        if r["suggested_action"]
        and r["suggested_action"] != "none — informational"
        and r["value_score"] >= SCOUT_QUEUE_MIN_VALUE_SCORE
    ]
    candidates.sort(
        key=lambda r: (r["value_score"], r["priority"] == "High", r["shortcode"]),
        reverse=True,
    )
    return [
        {
            "shortcode": r["shortcode"],
            "title": r["title"],
            "plain_summary": r["plain_summary"],
            "suggested_action": r["suggested_action"],
            "category": r["category"],
            "category_label": r["category_label"],
            "color": r["color"],
            "value_score": r["value_score"],
            "priority": r["priority"],
            "named_entities": r["named_entities"],
            "permalink": r["permalink"],
        }
        for r in candidates[:limit]
    ]


# --- endpoints ---------------------------------------------------------------------

@router.get("/graph")
def public_graph(
    request: Request,
    expand: Optional[str] = Query(
        None, description="category slug to expand into its reels, or \"all\" for every reel at once",
    ),
) -> dict:
    check_public_rate_limit(request)
    return build_graph(load_public_reels(), expand=expand)


@router.get("/stats")
def public_stats(request: Request) -> dict:
    check_public_rate_limit(request)
    return build_stats(load_public_reels())


@router.get("/reels")
def public_reels(
    request: Request,
    q: Optional[str] = Query(None, description="free-text search over title/summary/topics"),
    category: Optional[str] = Query(None),
    topic: Optional[str] = Query(None),
    min_value: int = Query(1, ge=1, le=5),
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=100),
) -> dict:
    check_public_rate_limit(request)
    reels = load_public_reels()

    if category:
        reels = [r for r in reels if r["category"] == category]
    if topic:
        reels = [r for r in reels if topic in r["topics"]]
    if min_value > 1:
        reels = [r for r in reels if r["value_score"] >= min_value]
    if q:
        needle = q.strip().lower()
        reels = [
            r for r in reels
            if needle in r["title"].lower()
            or needle in (r["plain_summary"] or "").lower()
            or any(needle in t for t in r["topics"])
            or any(needle in e.lower() for e in r["named_entities"])
        ]

    reels = sorted(reels, key=lambda r: (r["value_score"], r["posted_at"]), reverse=True)
    total = len(reels)
    start = (page - 1) * page_size
    return {
        "items": reels[start:start + page_size],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, -(-total // page_size)),
    }


@router.get("/reels/{shortcode}/detail")
def public_reel_detail(request: Request, shortcode: str) -> dict:
    """The block-derived sections /reels doesn't carry -- see load_reel_detail.

    404s for a shortcode /reels itself would never have shown (hidden status,
    placeholder title, or simply not found): checked against the SAME
    load_public_reels() list every other endpoint uses, so this can't be used
    to probe for a reel that the corpus-level redaction already hid."""
    check_public_rate_limit(request)
    if not any(r["shortcode"] == shortcode for r in load_public_reels()):
        raise HTTPException(status_code=404, detail="unknown reel")
    detail = load_reel_detail(shortcode)
    if detail is None:
        raise HTTPException(status_code=404, detail="unknown reel")
    return detail


@router.get("/scout-queue")
def public_scout_queue(
    request: Request,
    limit: int = Query(25, ge=1, le=100),
) -> dict:
    check_public_rate_limit(request)
    reels = load_public_reels()
    return {"items": build_scout_queue(reels, limit=limit), "total_reels": len(reels)}


@router.get("/categories")
def public_categories(request: Request) -> dict:
    """The colour/label legend, so the frontend never hardcodes a palette that
    could drift out of sync with what the graph endpoint actually emits."""
    check_public_rate_limit(request)
    counts = Counter(r["category"] for r in load_public_reels())
    return {
        "categories": [
            {"slug": s, "label": category_label(s), "color": category_color(s), "count": n}
            for s, n in counts.most_common()
        ]
    }
