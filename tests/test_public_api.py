"""app/public_api.py — the Mycelium read-only public API.

All mocked (no Notion, no network). The redaction tests are the important
ones: they are what stands between a comment-gate's earned payoff URL and the
open internet, so they assert on the SHAPE of the output (an allow-list), not
just on a handful of known-bad keys.
"""
import pytest

from app import public_api, taxonomy


def _page(shortcode, title, topics=(), value="4", priority="High",
          status="📥 Inbox", plain_summary="", suggested_action="",
          gate_keyword="SECRETWORD", gate_resource="https://private.example/payoff",
          note="my private note", entities=(), content_type="tutorial"):
    """A Notion page carrying BOTH publishable content and every private field,
    so any test rendering it can prove the private ones were dropped."""
    def rt(text):
        return [{"plain_text": text}] if text else []

    return {
        "id": f"pg-{shortcode}",
        "url": f"https://notion.so/pg-{shortcode}",
        "properties": {
            "Shortcode": {"rich_text": rt(shortcode)},
            "Title": {"title": [{"plain_text": title}]},
            "Topics": {"multi_select": [{"name": t} for t in topics]},
            "Priority": {"select": {"name": priority}},
            "Value score": {"select": {"name": value}},
            "Status": {"select": {"name": status}},
            "Content type": {"select": {"name": content_type}},
            "Named entities": {"multi_select": [{"name": e} for e in entities]},
            "Plain summary": {"rich_text": rt(plain_summary)},
            "Suggested action": {"rich_text": rt(suggested_action)},
            "Reel URL": {"url": f"https://www.instagram.com/reel/{shortcode}/"},
            "Posted at": {"date": {"start": "2026-06-01"}},
            # --- the private ones ---
            "Gate keyword": {"rich_text": rt(gate_keyword)},
            "Gate resource": {"url": gate_resource},
            "My note": {"rich_text": rt(note)},
        },
    }


@pytest.fixture(autouse=True)
def _clear_cache():
    """The corpus cache is module-level and TTL-based, so one test's fixture
    would otherwise leak into the next test's assertions. _DETAIL_CACHE is the
    same kind of module-level state for the per-reel detail endpoint below."""
    public_api._CORPUS_CACHE["reels"] = []
    public_api._CORPUS_CACHE["fetched_at"] = 0.0
    public_api._public_rate_buckets.clear()
    public_api._DETAIL_CACHE.clear()
    yield
    public_api._CORPUS_CACHE["reels"] = []
    public_api._CORPUS_CACHE["fetched_at"] = 0.0
    public_api._public_rate_buckets.clear()
    public_api._DETAIL_CACHE.clear()


def _load(monkeypatch, pages):
    from app import notion_writer
    monkeypatch.setattr(notion_writer, "find_saves_pages_since", lambda iso: pages)
    return public_api.load_public_reels(force_refresh=True)


# --- redaction ---------------------------------------------------------------------

def test_public_reel_drops_every_private_field(monkeypatch):
    reels = _load(monkeypatch, [_page("A1", "A real title", topics=("claude-ai",))])
    assert len(reels) == 1
    blob = repr(reels[0])
    assert "SECRETWORD" not in blob
    assert "private.example" not in blob
    assert "my private note" not in blob


def test_public_reel_is_an_allow_list_not_a_deny_list(monkeypatch):
    """The guarantee that matters long-term: a NEW private property added to
    the Notion schema later must not leak just because nobody remembered to
    add it to a deny-list. Pinning the exact key set is what enforces that --
    if this test fails after a schema change, that is the design working."""
    reels = _load(monkeypatch, [_page("A1", "A real title", topics=("claude-ai",))])
    assert set(reels[0]) == {
        "shortcode", "title", "plain_summary", "suggested_action", "topics",
        "category", "category_label", "color", "value_score", "priority",
        "content_type", "named_entities", "permalink", "posted_at",
    }


def test_private_property_names_are_never_read_into_the_public_shape(monkeypatch):
    """Belt-and-braces against the allow-list being widened carelessly."""
    reels = _load(monkeypatch, [_page("A1", "A real title", topics=("claude-ai",))])
    for prop in public_api.PRIVATE_NOTION_PROPERTIES:
        key = prop.lower().replace(" ", "_")
        assert key not in reels[0]


def test_scout_queue_output_carries_no_private_fields(monkeypatch):
    reels = _load(monkeypatch, [
        _page("A1", "A real title", topics=("claude-ai",), value="5",
              suggested_action="Install X and run the demo"),
    ])
    queue = public_api.build_scout_queue(reels)
    assert len(queue) == 1
    blob = repr(queue)
    assert "SECRETWORD" not in blob and "private.example" not in blob and "my private note" not in blob
    assert queue[0]["suggested_action"] == "Install X and run the demo"


def test_url_shaped_named_entities_are_stripped(monkeypatch):
    """named_entities is the only public field carrying free-form model output,
    so it is the one plausible route a URL could ever reach a public response.
    Also a quality fix: a bare "github.com" is not the NAME of anything and
    renders as a meaningless chip (observed live on a real row)."""
    reels = _load(monkeypatch, [
        _page("A1", "A real title", topics=("claude-ai",),
              entities=("Firecrawl", "github.com", "https://example.com/x", "www.foo.io", "Playwright CLI")),
    ])
    assert reels[0]["named_entities"] == ["Firecrawl", "Playwright CLI"]


def test_gate_keyword_appearing_inside_a_title_is_not_a_leak(monkeypatch):
    """Deliberate, and worth pinning so it is not 'fixed' later: the gate
    KEYWORD is not secret -- the creator broadcasts "comment GUIDE" in their
    own public caption, and main_point is derived from that caption. The
    private thing is the RESOURCE the DM delivers, which is asserted absent
    by the tests above."""
    reels = _load(monkeypatch, [
        _page("A1", 'Comment "GUIDE" and I will send the full walkthrough',
              topics=("claude-ai",), gate_keyword="GUIDE"),
    ])
    assert "GUIDE" in reels[0]["title"]
    assert "gate_keyword" not in reels[0]


def test_permalink_is_published_because_it_is_the_creators_own_public_post(monkeypatch):
    """Deliberate, not an oversight: linking back to the source reel is
    attribution. The GATE RESOURCE (the DM'd payoff) is the private one."""
    reels = _load(monkeypatch, [_page("A1", "A real title", topics=("claude-ai",))])
    assert reels[0]["permalink"] == "https://www.instagram.com/reel/A1/"


# --- curation (which rows are publishable at all) ----------------------------------

@pytest.mark.parametrize("status", sorted(public_api.HIDDEN_STATUS_LABELS))
def test_hidden_statuses_are_never_published(monkeypatch, status):
    reels = _load(monkeypatch, [_page("A1", "A real title", topics=("claude-ai",), status=status)])
    assert reels == []


def test_placeholder_title_row_is_not_published(monkeypatch):
    reels = _load(monkeypatch, [_page("A1", public_api.PLACEHOLDER_TITLE, topics=("claude-ai",))])
    assert reels == []


def test_bare_permalink_title_row_is_not_published(monkeypatch):
    """A row whose title is still its own URL never got a real extraction --
    there is nothing to show a visitor."""
    reels = _load(monkeypatch, [_page("A1", "https://www.instagram.com/reel/A1/")])
    assert reels == []


def test_row_without_a_shortcode_is_skipped(monkeypatch):
    assert _load(monkeypatch, [_page("", "A real title")]) == []


# --- graph shape -------------------------------------------------------------------

def test_graph_defaults_to_category_level_only(monkeypatch):
    """The requirement that keeps the landing page legible: 200+ reel nodes at
    once is an unreadable hairball, so the default view is categories."""
    pages = [_page(f"R{i}", f"Title {i}", topics=("claude-ai",)) for i in range(30)]
    graph = public_api.build_graph(_load(monkeypatch, pages))

    assert graph["level"] == "category"
    assert all(n["type"] == "category" for n in graph["nodes"])
    assert len(graph["nodes"]) == 1          # all 30 sit under claude-ecosystem
    assert graph["nodes"][0]["count"] == 30
    assert graph["total_reels"] == 30


def test_graph_nodes_have_the_fields_react_force_graph_needs(monkeypatch):
    graph = public_api.build_graph(_load(monkeypatch, [
        _page("A1", "A real title", topics=("claude-ai",)),
    ]))
    node = graph["nodes"][0]
    assert node["id"] == "cat:claude-ecosystem"
    assert node["label"] == "Claude Ecosystem"
    assert node["color"] == public_api.CATEGORY_COLORS["claude-ecosystem"]
    assert isinstance(node["val"], float)    # force-graph reads `val` for size


def test_graph_expand_adds_only_that_categorys_reels(monkeypatch):
    pages = [
        _page("A1", "Claude one", topics=("claude-ai",)),
        _page("B1", "Design one", topics=("web-design",)),
    ]
    reels = _load(monkeypatch, pages)
    graph = public_api.build_graph(reels, expand="claude-ecosystem")

    assert graph["level"] == "expanded"
    reel_nodes = [n for n in graph["nodes"] if n["type"] == "reel"]
    assert [n["shortcode"] for n in reel_nodes] == ["A1"]      # NOT B1
    membership = [l for l in graph["links"] if l["type"] == "membership"]
    assert membership == [
        {"source": "cat:claude-ecosystem", "target": "reel:A1", "value": 1, "type": "membership"}
    ]


def test_graph_expand_rejects_an_unknown_category(monkeypatch):
    from fastapi import HTTPException

    reels = _load(monkeypatch, [_page("A1", "A real title", topics=("claude-ai",))])
    with pytest.raises(HTTPException) as exc:
        public_api.build_graph(reels, expand="not-a-real-category")
    assert exc.value.status_code == 404


def test_graph_links_categories_that_actually_co_occur(monkeypatch):
    """Edges are a real signal from the corpus -- which subjects get saved
    together -- not decoration."""
    pages = [_page("A1", "Cross-cutting", topics=("claude-ai", "web-design"))]
    graph = public_api.build_graph(_load(monkeypatch, pages))

    co = [l for l in graph["links"] if l["type"] == "co-occurrence"]
    assert len(co) == 1
    assert {co[0]["source"], co[0]["target"]} == {"cat:claude-ecosystem", "cat:web-and-design"}
    assert co[0]["value"] == 1


def test_cross_cutting_reel_is_counted_in_every_category_it_touches(monkeypatch):
    """A reel tagged both claude-ai and web-design genuinely belongs to both
    parents. Counting it only under its primary would understate the smaller
    category and erase the cross-link the graph exists to draw."""
    pages = [_page("A1", "Cross-cutting", topics=("claude-ai", "web-design"))]
    graph = public_api.build_graph(_load(monkeypatch, pages))

    counts = {n["category"]: n["count"] for n in graph["nodes"]}
    assert counts == {"claude-ecosystem": 1, "web-and-design": 1}


def test_expanding_a_secondary_category_still_reveals_the_reel(monkeypatch):
    """web-and-design is only this reel's SECONDARY category (claude-ai comes
    first, so claude-ecosystem is primary) -- expanding it must still show the
    reel, or a category node would advertise a count it cannot display."""
    pages = [_page("A1", "Cross-cutting", topics=("claude-ai", "web-design"))]
    graph = public_api.build_graph(_load(monkeypatch, pages), expand="web-and-design")

    reel_nodes = [n for n in graph["nodes"] if n["type"] == "reel"]
    assert [n["shortcode"] for n in reel_nodes] == ["A1"]
    # ...but it keeps its PRIMARY category's colour, so one reel has one identity.
    assert reel_nodes[0]["color"] == public_api.CATEGORY_COLORS["claude-ecosystem"]


def test_every_category_node_count_matches_what_expanding_it_reveals(monkeypatch):
    """The invariant that keeps the UI honest: a node saying '12' must expand
    into 12 reels."""
    pages = [
        _page("A1", "One", topics=("claude-ai", "web-design")),
        _page("B1", "Two", topics=("web-design",)),
        _page("C1", "Three", topics=("claude-ai",)),
    ]
    reels = _load(monkeypatch, pages)
    graph = public_api.build_graph(reels)

    for node in graph["nodes"]:
        expanded = public_api.build_graph(reels, expand=node["category"])
        revealed = [n for n in expanded["nodes"] if n["type"] == "reel"]
        assert len(revealed) == node["count"], node["category"]


# --- expand="all" (2026-09-01): every reel at once, for the dense default view --------

def test_expand_all_includes_every_reel_regardless_of_category(monkeypatch):
    pages = [
        _page("A1", "Claude one", topics=("claude-ai",)),
        _page("B1", "Design one", topics=("web-design",)),
        _page("C1", "Other one", topics=()),
    ]
    reels = _load(monkeypatch, pages)
    graph = public_api.build_graph(reels, expand="all")

    assert graph["level"] == "expanded"
    reel_nodes = {n["shortcode"] for n in graph["nodes"] if n["type"] == "reel"}
    assert reel_nodes == {"A1", "B1", "C1"}


def test_expand_all_gives_a_multi_category_reel_one_node_and_every_membership_link(monkeypatch):
    """A reel touching two categories still gets exactly one node (never
    duplicated), but a membership link to EACH category -- the same
    cross-category signal a single-category expand already preserves."""
    pages = [_page("A1", "Cross-cutting", topics=("claude-ai", "web-design"))]
    graph = public_api.build_graph(_load(monkeypatch, pages), expand="all")

    reel_nodes = [n for n in graph["nodes"] if n["type"] == "reel"]
    assert len(reel_nodes) == 1
    membership_targets = {
        l["source"] for l in graph["links"] if l["type"] == "membership" and l["target"] == "reel:A1"
    }
    assert membership_targets == {"cat:claude-ecosystem", "cat:web-and-design"}


def test_expand_all_reports_every_category_as_expanded(monkeypatch):
    pages = [
        _page("A1", "Claude one", topics=("claude-ai",)),
        _page("B1", "Design one", topics=("web-design",)),
    ]
    graph = public_api.build_graph(_load(monkeypatch, pages), expand="all")
    assert set(graph["expanded"]) == {"claude-ecosystem", "web-and-design"}


def test_expand_all_reel_count_matches_single_category_expands_summed_with_overlap(monkeypatch):
    """The same invariant test_every_category_node_count_matches_what_expanding_it_reveals
    checks per-category, but for the all-at-once view: total unique reel nodes
    must equal the real corpus size, not double-count a cross-category reel."""
    pages = [
        _page("A1", "One", topics=("claude-ai", "web-design")),
        _page("B1", "Two", topics=("web-design",)),
        _page("C1", "Three", topics=("claude-ai",)),
    ]
    reels = _load(monkeypatch, pages)
    graph = public_api.build_graph(reels, expand="all")
    reel_nodes = [n for n in graph["nodes"] if n["type"] == "reel"]
    assert len(reel_nodes) == len(reels) == 3


def test_expand_all_does_not_require_a_real_category_name(monkeypatch):
    """"all" must never be validated as if it were a category slug -- it isn't
    one, and the 404 branch (test_graph_expand_rejects_an_unknown_category)
    must not fire for it."""
    reels = _load(monkeypatch, [_page("A1", "A real title", topics=("claude-ai",))])
    graph = public_api.build_graph(reels, expand="all")  # must not raise
    assert graph["level"] == "expanded"


def test_graph_has_no_co_occurrence_link_when_categories_never_share_a_reel(monkeypatch):
    pages = [
        _page("A1", "Claude one", topics=("claude-ai",)),
        _page("B1", "Design one", topics=("web-design",)),
    ]
    graph = public_api.build_graph(_load(monkeypatch, pages))
    assert [l for l in graph["links"] if l["type"] == "co-occurrence"] == []


def test_every_category_slug_has_a_colour_and_label():
    """A category rendered without a colour would fall back to slate and
    silently look like the 'other' bucket."""
    for parent in taxonomy.PARENTS:
        assert parent in public_api.CATEGORY_COLORS, parent
        assert parent in public_api.CATEGORY_LABELS, parent


def test_category_colours_are_all_distinct():
    colours = list(public_api.CATEGORY_COLORS.values())
    assert len(colours) == len(set(colours))


def test_topics_with_no_parent_fall_into_other(monkeypatch):
    reels = _load(monkeypatch, [_page("A1", "A real title", topics=("looksmaxxing",))])
    assert reels[0]["category"] == public_api.OTHER_CATEGORY
    assert reels[0]["color"] == public_api.OTHER_COLOR


def test_marker_only_topics_are_stripped_from_public_output(monkeypatch):
    reels = _load(monkeypatch, [
        _page("A1", "A real title", topics=("uncategorized", "near-duplicate", "claude-ai")),
    ])
    assert reels[0]["topics"] == ["claude-ai"]


def test_graph_label_is_short_enough_to_render_next_to_a_dot(monkeypatch):
    long_summary = "This is a very long plain summary " * 10
    reels = _load(monkeypatch, [
        _page("A1", "A real title", topics=("claude-ai",), plain_summary=long_summary),
    ])
    graph = public_api.build_graph(reels, expand="claude-ecosystem")
    label = [n for n in graph["nodes"] if n["type"] == "reel"][0]["label"]
    assert len(label) <= public_api.GRAPH_LABEL_MAX_CHARS + 1   # +1 for the ellipsis
    assert not label.endswith(" …")   # truncated on a word boundary, no dangling space


# --- stats -------------------------------------------------------------------------

def test_stats_counts_are_derived_from_the_live_corpus(monkeypatch):
    pages = [
        _page("A1", "One", topics=("claude-ai",), value="5", priority="High",
              suggested_action="Do the thing", entities=("Claude",)),
        _page("B1", "Two", topics=("web-design",), value="3", priority="Medium",
              suggested_action="none — informational"),
    ]
    stats = public_api.build_stats(_load(monkeypatch, pages))

    assert stats["total_reels"] == 2
    assert stats["total_categories"] == 2
    assert stats["actionable_items"] == 1        # "none — informational" does not count
    assert stats["high_priority"] == 1
    assert stats["total_topics"] == 2


def test_stats_on_an_empty_corpus_are_zero_not_an_error(monkeypatch):
    stats = public_api.build_stats(_load(monkeypatch, []))
    assert stats["total_reels"] == 0
    assert stats["top_categories"] == []


# --- scout queue -------------------------------------------------------------------

def test_scout_queue_requires_a_real_action_and_a_high_value_score(monkeypatch):
    pages = [
        _page("KEEP", "Keeper", topics=("claude-ai",), value="5", suggested_action="Install it"),
        _page("LOWV", "Low value", topics=("claude-ai",), value="2", suggested_action="Install it"),
        _page("NOACT", "No action", topics=("claude-ai",), value="5",
              suggested_action="none — informational"),
        _page("EMPTY", "Empty action", topics=("claude-ai",), value="5", suggested_action=""),
    ]
    queue = public_api.build_scout_queue(_load(monkeypatch, pages))
    assert [i["shortcode"] for i in queue] == ["KEEP"]


def test_scout_queue_ranks_higher_value_first(monkeypatch):
    pages = [
        _page("FOUR", "Four", topics=("claude-ai",), value="4", suggested_action="Do it"),
        _page("FIVE", "Five", topics=("claude-ai",), value="5", suggested_action="Do it"),
    ]
    queue = public_api.build_scout_queue(_load(monkeypatch, pages))
    assert [i["shortcode"] for i in queue] == ["FIVE", "FOUR"]


def test_scout_queue_respects_its_limit(monkeypatch):
    pages = [_page(f"R{i}", f"T{i}", topics=("claude-ai",), value="5",
                    suggested_action="Do it") for i in range(10)]
    assert len(public_api.build_scout_queue(_load(monkeypatch, pages), limit=3)) == 3


# --- caching + resilience ----------------------------------------------------------

def test_corpus_is_cached_and_not_requeried_within_the_ttl(monkeypatch):
    from app import notion_writer

    calls = []

    def _counting(iso):
        calls.append(iso)
        return [_page("A1", "A real title", topics=("claude-ai",))]

    monkeypatch.setattr(notion_writer, "find_saves_pages_since", _counting)

    public_api.load_public_reels(force_refresh=True)
    public_api.load_public_reels()
    public_api.load_public_reels()
    assert len(calls) == 1


def test_notion_failure_serves_the_last_good_snapshot_instead_of_erroring(monkeypatch):
    """A marketing site showing slightly stale numbers beats one showing a
    stack trace."""
    from app import notion_writer

    monkeypatch.setattr(notion_writer, "find_saves_pages_since",
                         lambda iso: [_page("A1", "A real title", topics=("claude-ai",))])
    public_api.load_public_reels(force_refresh=True)

    def _boom(iso):
        raise RuntimeError("notion down")

    monkeypatch.setattr(notion_writer, "find_saves_pages_since", _boom)
    stale = public_api.load_public_reels(force_refresh=True)
    assert [r["shortcode"] for r in stale] == ["A1"]


def test_cold_cache_plus_notion_failure_is_an_empty_list_not_a_crash(monkeypatch):
    from app import notion_writer

    def _boom(iso):
        raise RuntimeError("notion down")

    monkeypatch.setattr(notion_writer, "find_saves_pages_since", _boom)
    assert public_api.load_public_reels(force_refresh=True) == []


# --- rate limiting -----------------------------------------------------------------

class _Req:
    def __init__(self, ip="1.2.3.4"):
        self.client = type("C", (), {"host": ip})()
        self.headers = {}


def test_rate_limit_allows_up_to_the_cap_then_429s(monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setattr(public_api, "PUBLIC_RATE_LIMIT_MAX_PER_MINUTE", 3)
    req = _Req()
    for _ in range(3):
        public_api.check_public_rate_limit(req)
    with pytest.raises(HTTPException) as exc:
        public_api.check_public_rate_limit(req)
    assert exc.value.status_code == 429


def test_rate_limit_is_per_ip_not_global(monkeypatch):
    monkeypatch.setattr(public_api, "PUBLIC_RATE_LIMIT_MAX_PER_MINUTE", 2)
    for _ in range(2):
        public_api.check_public_rate_limit(_Req("1.1.1.1"))
    public_api.check_public_rate_limit(_Req("2.2.2.2"))  # a different visitor is unaffected


def test_rate_limit_window_expires(monkeypatch):
    monkeypatch.setattr(public_api, "PUBLIC_RATE_LIMIT_MAX_PER_MINUTE", 1)
    clock = {"t": 1000.0}
    monkeypatch.setattr(public_api.time, "monotonic", lambda: clock["t"])

    req = _Req()
    public_api.check_public_rate_limit(req)
    clock["t"] += public_api.PUBLIC_RATE_LIMIT_WINDOW_SECONDS + 1
    public_api.check_public_rate_limit(req)   # window rolled over, allowed again


def test_public_rate_limit_bucket_is_separate_from_the_write_path_limiter():
    """A page load legitimately fires several read calls; /capture should stay
    tight. Sharing one bucket would let browsing the site lock out captures."""
    from app import main

    assert public_api._public_rate_buckets is not main._rate_buckets
    assert public_api.PUBLIC_RATE_LIMIT_MAX_PER_MINUTE > main.RATE_LIMIT_MAX_PER_MINUTE


# --- per-reel detail (2026-09-04) ---------------------------------------------------

def _block(kind, **content):
    return {"type": kind, kind: content}


def _rt(text):
    return {"rich_text": [{"plain_text": text}]} if text else {"rich_text": []}


class _FakeBlocksChildren:
    def __init__(self, blocks):
        self._blocks = blocks

    def list(self, block_id, start_cursor=None):  # noqa: ARG002 - fixture signature
        # Single page of results is enough for these tests; pagination itself
        # is exercised by the identical pattern already covered elsewhere.
        return {"results": self._blocks, "has_more": False}


class _FakeBlocks:
    def __init__(self, blocks):
        self.children = _FakeBlocksChildren(blocks)


class _FakeNotionClient:
    def __init__(self, blocks):
        self.blocks = _FakeBlocks(blocks)


_AUTO_PAGE = object()  # sentinel: "generate {'id': 'pg-<shortcode>'}", distinct from page=None


def _detail(monkeypatch, blocks, shortcode="A1", page=_AUTO_PAGE):
    from app import notion_writer
    public_api._DETAIL_CACHE.clear()  # each call is a fresh scenario, not a real re-open
    monkeypatch.setattr(notion_writer, "_client", lambda: _FakeNotionClient(blocks))
    monkeypatch.setattr(
        notion_writer, "find_page_by_shortcode",
        lambda sc: {"id": f"pg-{sc}"} if page is _AUTO_PAGE else page,
    )
    return public_api.load_reel_detail(shortcode)


def test_detail_buckets_each_block_type_into_its_own_section(monkeypatch):
    blocks = [
        {"type": "callout", "callout": {"rich_text": [{"plain_text": "The main point"}]}},
        {"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"plain_text": "Point one"}]}},
        {"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"plain_text": "Point two"}]}},
        {"type": "numbered_list_item", "numbered_list_item": {"rich_text": [{"plain_text": "Step one"}]}},
        {"type": "bookmark", "bookmark": {"url": "https://firecrawl.dev/docs"}},
        {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "Playwright CLI (tool)"}]}},
        {"type": "quote", "quote": {"rich_text": [{"plain_text": "A quotable line."}]}},
    ]
    detail = _detail(monkeypatch, blocks)

    assert detail["supporting_points"] == ["Point one", "Point two"]
    assert detail["steps_or_framework"] == ["Step one"]
    assert detail["quotable_lines"] == ["A quotable line."]
    assert {"name": "Playwright CLI", "type": "tool", "url": None} in detail["resources_mentioned"]
    # Domain + first path segment, not domain alone -- see the comment on the
    # bookmark branch: two bookmarks on the same host must not collapse to
    # one indistinguishable label.
    assert {"name": "firecrawl.dev/docs", "type": "site", "url": "https://firecrawl.dev/docs"} \
        in detail["resources_mentioned"]


def test_detail_distinguishes_multiple_bookmarks_on_the_same_host(monkeypatch):
    """Real bug, found against a live reel while testing this feature: four
    github.com repos all bookmarked in one reel collapsed to the identical
    label "github.com" repeated four times -- correct per item, unreadable as
    a group, since nothing distinguished one from another. The fix folds in
    the first path segment (an org/user, here), which is what actually
    identifies a specific repo on a host that hosts unrelated ones."""
    blocks = [
        {"type": "bookmark", "bookmark": {"url": "https://github.com/darkroomengineering/lenis"}},
        {"type": "bookmark", "bookmark": {"url": "https://github.com/greensock/GSAP"}},
        {"type": "bookmark", "bookmark": {"url": "https://github.com/tengbao/vanta"}},
    ]
    detail = _detail(monkeypatch, blocks)
    names = [r["name"] for r in detail["resources_mentioned"]]
    assert names == ["github.com/darkroomengineering", "github.com/greensock", "github.com/tengbao"]
    assert len(set(names)) == len(names)  # every label distinct


def test_detail_never_descends_into_a_toggle_regardless_of_its_title(monkeypatch):
    """This is the actual redaction mechanism for Transcript/Raw caption/
    Research Context: none of their content is even fetched, let alone
    filtered by name. A toggle carrying a private-looking title -- or any
    title at all -- must still contribute nothing to any section."""
    blocks = [
        {
            "type": "toggle",
            "toggle": {
                "rich_text": [{"plain_text": "Transcript"}],
                "children": [
                    {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "verbatim audio text"}]}},
                ],
            },
        },
        {
            "type": "toggle",
            "toggle": {
                "rich_text": [{"plain_text": "Raw caption"}],
                "children": [
                    {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "the creator's caption"}]}},
                ],
            },
        },
        {"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"plain_text": "A real point"}]}},
    ]
    detail = _detail(monkeypatch, blocks)

    blob = repr(detail)
    assert "verbatim audio text" not in blob
    assert "the creator's caption" not in blob
    assert detail["supporting_points"] == ["A real point"]


def test_detail_skips_a_non_matching_top_level_paragraph_rather_than_show_it(monkeypatch):
    blocks = [
        {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "not a resource line at all"}]}},
    ]
    detail = _detail(monkeypatch, blocks)
    assert detail["resources_mentioned"] == []


def test_detail_on_empty_blocks_is_all_empty_lists_not_an_error(monkeypatch):
    detail = _detail(monkeypatch, [])
    assert detail == {
        "supporting_points": [], "steps_or_framework": [],
        "resources_mentioned": [], "quotable_lines": [], "shortcode": "A1",
    }


def test_detail_returns_none_when_no_page_matches_the_shortcode(monkeypatch):
    detail = _detail(monkeypatch, [], page=None)
    assert detail is None


def test_detail_survives_a_notion_block_fetch_failure_with_empty_sections(monkeypatch):
    from app import notion_writer

    class _Boom:
        def list(self, **kwargs):
            raise RuntimeError("notion hiccup")

    class _BoomClient:
        blocks = type("B", (), {"children": _Boom()})()

    monkeypatch.setattr(notion_writer, "_client", lambda: _BoomClient())
    monkeypatch.setattr(notion_writer, "find_page_by_shortcode", lambda sc: {"id": "pg-A1"})
    public_api._DETAIL_CACHE.clear()

    detail = public_api.load_reel_detail("A1")
    assert detail["supporting_points"] == []
    assert detail["resources_mentioned"] == []


def test_detail_is_cached_and_not_refetched_within_the_ttl(monkeypatch):
    from app import notion_writer

    calls = {"n": 0}

    def _list(self, block_id, start_cursor=None):
        calls["n"] += 1
        return {"results": [], "has_more": False}

    monkeypatch.setattr(_FakeBlocksChildren, "list", _list)
    monkeypatch.setattr(notion_writer, "_client", lambda: _FakeNotionClient([]))
    monkeypatch.setattr(notion_writer, "find_page_by_shortcode", lambda sc: {"id": "pg-A1"})
    public_api._DETAIL_CACHE.clear()

    public_api.load_reel_detail("A1")
    public_api.load_reel_detail("A1")
    assert calls["n"] == 1


def test_detail_endpoint_404s_for_a_shortcode_hidden_at_the_corpus_level(monkeypatch):
    """Same visibility gate as everywhere else: a shortcode that /reels would
    never show (archived status here) must 404 at the detail endpoint too,
    not fall through to a real Notion lookup."""
    from fastapi import HTTPException

    reels = _load(monkeypatch, [_page("HIDDEN", "A real title", status="🗄 Archived")])
    assert reels == []  # confirms the corpus-level gate actually applied

    with pytest.raises(HTTPException) as exc:
        public_api.public_reel_detail(_Req(), "HIDDEN")
    assert exc.value.status_code == 404


def test_detail_endpoint_returns_the_block_derived_sections_for_a_visible_reel(monkeypatch):
    from app import notion_writer

    _load(monkeypatch, [_page("A1", "A real title", topics=("claude-ai",))])
    monkeypatch.setattr(
        notion_writer, "_client",
        lambda: _FakeNotionClient([
            {"type": "quote", "quote": {"rich_text": [{"plain_text": "Ship fast."}]}},
        ]),
    )
    monkeypatch.setattr(notion_writer, "find_page_by_shortcode", lambda sc: {"id": "pg-A1"})
    public_api._DETAIL_CACHE.clear()

    result = public_api.public_reel_detail(_Req(), "A1")
    assert result["quotable_lines"] == ["Ship fast."]
    assert result["shortcode"] == "A1"


def test_detail_response_never_carries_the_private_gate_resource(monkeypatch):
    """resources_mentioned is built ENTIRELY from block content; nothing in
    _reel_body_detail ever reads the "Gate resource" page property, so even a
    reel with a real attached gate resource cannot leak it through here."""
    blocks = [
        {"type": "bookmark", "bookmark": {"url": "https://openly-named-tool.example/docs"}},
    ]
    detail = _detail(monkeypatch, blocks)
    blob = repr(detail)
    assert "private.example" not in blob  # the fixture's own gate_resource URL
