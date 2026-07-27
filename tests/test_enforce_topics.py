"""scripts/enforce_topics.py — the free (zero-Gemini) topic sweep."""
from scripts import enforce_topics as et




# --- upgrading stranded "uncategorized" rows -------------------------------------

def _page(shortcode, topics, entities):
    return {"id": f"pg-{shortcode}", "properties": {
        "Shortcode": {"rich_text": [{"plain_text": shortcode}]},
        "Name": {"title": [{"plain_text": shortcode}]},
        "Topics": {"multi_select": [{"name": t} for t in topics]},
        "Named entities": {"multi_select": [{"name": e} for e in entities]},
        "Content type": {"select": {"name": "tutorial"}},
    }}


def test_uncategorized_rows_are_ignored_by_default():
    """The default sweep selects EMPTY topics only — an already-swept row must
    not be re-selected, or the sweep would never converge."""
    pages = [_page("AAA", ["uncategorized"], ["LangGraph"])]
    assert et.find_topicless_rows(pages) == []


def test_uncategorized_row_with_entities_is_upgradable():
    """These rows reached 'uncategorized' only because named_entities hadn't
    been backfilled yet. Once entities exist the free fallback can do better."""
    pages = [_page("AAA", ["uncategorized"], ["LangGraph"])]
    rows = et.find_topicless_rows(pages, include_upgradable=True)
    assert [r["shortcode"] for r in rows] == ["AAA"]


def test_uncategorized_row_without_entities_is_not_upgradable():
    """Guard against a daily no-op write: with no entities the fallback would
    just produce 'uncategorized' again."""
    pages = [_page("AAA", ["uncategorized"], [])]
    assert et.find_topicless_rows(pages, include_upgradable=True) == []


def test_properly_tagged_rows_are_never_upgradable():
    pages = [_page("AAA", ["ai-agents", "tutorial"], ["LangGraph"])]
    assert et.find_topicless_rows(pages, include_upgradable=True) == []
