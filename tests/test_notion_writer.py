"""Unit tests for the single-persistent-page pattern (find_child_page_by_title
/ upsert_named_page) used by both digests -- see PROGRESS.md. A dedicated fake
client here (rather than reusing tests/test_pipeline.py's FakeClient) actually
models the parent-page -> child-page relationship, since FakeClient's block
listing always returns empty and can't represent "the page already exists"."""
import pytest

from app import notion_writer
from tests.test_digest import _DigestDS, _digest_page
from tests.test_pipeline import FakeClient


class _FakeChildPageClient:
    """Simulates one parent page with zero or more named child pages."""

    def __init__(self):
        self.pages_by_title: dict[str, str] = {}
        self.page_children: dict[str, list] = {}
        self.created_calls: list[dict] = []
        self.updated_calls: list[dict] = []
        self.appended_calls: list[dict] = []
        self.deleted_block_ids: list[str] = []
        self.pages = self._Pages(self)
        self.blocks = self._Blocks(self)

    class _Pages:
        def __init__(self, outer):
            self._outer = outer

        def create(self, **kwargs):
            outer = self._outer
            title = kwargs["properties"]["title"]["title"][0]["text"]["content"]
            page_id = f"page-{len(outer.pages_by_title) + 1}"
            outer.pages_by_title[title] = page_id
            outer.page_children[page_id] = list(kwargs.get("children", []))
            outer.created_calls.append(kwargs)
            return {"id": page_id, "url": f"https://notion.so/{page_id}"}

        def update(self, **kwargs):
            self._outer.updated_calls.append(kwargs)
            return {"id": kwargs["page_id"], "url": f"https://notion.so/{kwargs['page_id']}"}

    class _Blocks:
        def __init__(self, outer):
            self._outer = outer
            self.children = self

        def list(self, block_id, **kwargs):
            outer = self._outer
            if block_id == "PARENT":
                return {
                    "results": [
                        {"type": "child_page", "id": pid, "child_page": {"title": t}}
                        for t, pid in outer.pages_by_title.items()
                    ],
                    "has_more": False,
                }
            children = outer.page_children.get(block_id, [])
            return {"results": [{"id": f"{block_id}-block-{i}"} for i in range(len(children))]}

        def append(self, block_id, children):
            outer = self._outer
            outer.page_children[block_id] = list(children)
            outer.appended_calls.append({"block_id": block_id, "children": children})
            return {"results": children}

        def delete(self, block_id):
            self._outer.deleted_block_ids.append(block_id)
            return {}


def test_find_child_page_by_title_returns_none_when_absent(monkeypatch):
    client = _FakeChildPageClient()
    assert notion_writer.find_child_page_by_title(client, "PARENT", "🌙 Daily Reflection") is None


def test_find_child_page_by_title_finds_existing(monkeypatch):
    client = _FakeChildPageClient()
    client.pages_by_title["🌙 Daily Reflection"] = "existing-page-id"
    found = notion_writer.find_child_page_by_title(client, "PARENT", "🌙 Daily Reflection")
    assert found == "existing-page-id"


def test_find_child_page_by_title_paginates(monkeypatch):
    calls = []

    class _PagedBlocks:
        def __init__(self):
            self.children = self

        def list(self, block_id, **kwargs):
            calls.append(kwargs)
            if not kwargs:
                return {
                    "results": [{"type": "child_page", "id": "p1", "child_page": {"title": "Other"}}],
                    "has_more": True,
                    "next_cursor": "c2",
                }
            return {
                "results": [{"type": "child_page", "id": "p2", "child_page": {"title": "Target"}}],
                "has_more": False,
            }

    class _Client:
        def __init__(self):
            self.blocks = _PagedBlocks()

    found = notion_writer.find_child_page_by_title(_Client(), "PARENT", "Target")
    assert found == "p2"
    assert calls[1]["start_cursor"] == "c2"


def test_upsert_named_page_creates_on_first_call(monkeypatch):
    client = _FakeChildPageClient()
    monkeypatch.setattr(notion_writer, "_client", lambda: client)

    children = [{"object": "block", "type": "paragraph",
                 "paragraph": {"rich_text": notion_writer._rich_text("hello")}}]
    result = notion_writer.upsert_named_page("PARENT", "🌙 Daily Reflection", children)

    assert result["page_id"] == "page-1"
    assert len(client.created_calls) == 1
    assert client.created_calls[0]["parent"] == {"type": "page_id", "page_id": "PARENT"}


def test_upsert_named_page_replaces_blocks_on_second_call_same_page(monkeypatch):
    """The actual fix: a second run finds the same page and replaces its body
    (delete-then-append), instead of creating a second dated page."""
    client = _FakeChildPageClient()
    monkeypatch.setattr(notion_writer, "_client", lambda: client)

    first_children = [{"object": "block", "type": "paragraph",
                        "paragraph": {"rich_text": notion_writer._rich_text("day one")}}]
    second_children = [{"object": "block", "type": "paragraph",
                         "paragraph": {"rich_text": notion_writer._rich_text("day two")}}]

    first = notion_writer.upsert_named_page("PARENT", "🌙 Daily Reflection", first_children)
    second = notion_writer.upsert_named_page("PARENT", "🌙 Daily Reflection", second_children)

    assert first["page_id"] == second["page_id"]
    assert len(client.created_calls) == 1  # created ONCE
    assert len(client.appended_calls) == 1  # second run appended fresh blocks
    assert client.appended_calls[0]["children"] == second_children
    assert len(client.deleted_block_ids) == 1  # old block(s) from run one deleted


def test_upsert_named_page_caps_children_at_100(monkeypatch):
    client = _FakeChildPageClient()
    monkeypatch.setattr(notion_writer, "_client", lambda: client)

    children = [{"object": "block", "type": "paragraph",
                 "paragraph": {"rich_text": notion_writer._rich_text(f"line {i}")}} for i in range(150)]
    notion_writer.upsert_named_page("PARENT", "Some Digest", children)

    assert len(client.created_calls[0]["children"]) == 100


# --- get_live_taxonomy: the 2026-08-09 taxonomy-collapse incident fix ---
# store.get_taxonomy used to read the local `tags` table (wiped on every
# redeploy, 4 rows against 212 real Notion rows) -- every extraction call's
# candidate list was silently near-empty, so the model had nothing real to
# converge toward. This is the Notion-backed replacement.

@pytest.fixture(autouse=True)
def _reset_taxonomy_cache():
    """The cache is module-level and TTL-based, so a prior test's fetch would
    otherwise leak into the next test's assertions."""
    notion_writer._TAXONOMY_CACHE["tags"] = []
    notion_writer._TAXONOMY_CACHE["fetched_at"] = 0.0
    yield
    notion_writer._TAXONOMY_CACHE["tags"] = []
    notion_writer._TAXONOMY_CACHE["fetched_at"] = 0.0


def test_get_live_taxonomy_orders_by_frequency(monkeypatch):
    client = FakeClient()
    client.data_sources = _DigestDS([
        _digest_page("T1", "one", topics=("ai-workflows", "productivity")),
        _digest_page("T2", "two", topics=("ai-workflows",)),
        _digest_page("T3", "three", topics=("fitness",)),
    ])
    monkeypatch.setattr(notion_writer, "_client", lambda: client)

    taxonomy = notion_writer.get_live_taxonomy(limit=40)

    assert taxonomy[0] == "ai-workflows"
    assert "fitness" in taxonomy


def test_get_live_taxonomy_applies_merges(monkeypatch):
    """A stray unmerged spelling (e.g. "claude", which crept back in as a
    fresh tag after the one-time Phase 0 cleanup) must count toward its
    canonical target rather than polluting the ranking as its own tag."""
    client = FakeClient()
    client.data_sources = _DigestDS([
        _digest_page("M1", "one", topics=("claude-ai",)),
        _digest_page("M2", "two", topics=("claude",)),  # merges -> claude-ai
        _digest_page("M3", "three", topics=("claude",)),  # merges -> claude-ai
    ])
    monkeypatch.setattr(notion_writer, "_client", lambda: client)

    taxonomy = notion_writer.get_live_taxonomy(limit=40)

    assert taxonomy == ["claude-ai"]


def test_get_live_taxonomy_excludes_marker_tags(monkeypatch):
    """uncategorized/near-duplicate/pending-extraction are pipeline-state
    markers, never genuine subject-matter candidates to offer the model."""
    client = FakeClient()
    client.data_sources = _DigestDS([
        _digest_page("N1", "one", topics=("near-duplicate", "fitness")),
        _digest_page("N2", "two", topics=("uncategorized",)),
        _digest_page("N3", "three", topics=("pending-extraction",)),
    ])
    monkeypatch.setattr(notion_writer, "_client", lambda: client)

    taxonomy = notion_writer.get_live_taxonomy(limit=40)

    assert taxonomy == ["fitness"]


def test_get_live_taxonomy_respects_limit(monkeypatch):
    client = FakeClient()
    client.data_sources = _DigestDS([
        _digest_page("L1", "one", topics=("a", "b", "c")),
    ])
    monkeypatch.setattr(notion_writer, "_client", lambda: client)

    assert len(notion_writer.get_live_taxonomy(limit=2)) == 2


def test_get_live_taxonomy_caches_within_ttl(monkeypatch):
    client = FakeClient()
    calls = []
    real_query = _DigestDS.query

    class _CountingDS(_DigestDS):
        def query(self, **kwargs):
            calls.append(kwargs)
            return real_query(self, **kwargs)

    client.data_sources = _CountingDS([_digest_page("C1", "one", topics=("fitness",))])
    monkeypatch.setattr(notion_writer, "_client", lambda: client)

    first = notion_writer.get_live_taxonomy(limit=40)
    second = notion_writer.get_live_taxonomy(limit=40)

    assert first == second == ["fitness"]
    assert len(calls) == 1  # second call served from cache, no re-query


def test_get_live_taxonomy_force_refresh_bypasses_cache(monkeypatch):
    client = FakeClient()
    client.data_sources = _DigestDS([_digest_page("F1", "one", topics=("fitness",))])
    monkeypatch.setattr(notion_writer, "_client", lambda: client)

    notion_writer.get_live_taxonomy(limit=40)
    client.data_sources = _DigestDS([_digest_page("F2", "two", topics=("cooking",))])
    refreshed = notion_writer.get_live_taxonomy(limit=40, force_refresh=True)

    assert refreshed == ["cooking"]


def test_get_live_taxonomy_falls_back_to_stale_cache_on_notion_error(monkeypatch):
    client = FakeClient()
    client.data_sources = _DigestDS([_digest_page("E1", "one", topics=("fitness",))])
    monkeypatch.setattr(notion_writer, "_client", lambda: client)
    notion_writer.get_live_taxonomy(limit=40)  # populate the cache

    def _boom():
        raise RuntimeError("notion down")
    monkeypatch.setattr(notion_writer, "_client", _boom)

    stale = notion_writer.get_live_taxonomy(limit=40, force_refresh=True)

    assert stale == ["fitness"]  # last-known-good, not a crash or empty list


def test_get_live_taxonomy_empty_on_cold_cache_and_notion_error(monkeypatch):
    def _boom():
        raise RuntimeError("notion down")
    monkeypatch.setattr(notion_writer, "_client", _boom)

    assert notion_writer.get_live_taxonomy(limit=40) == []


# --- write-time canonicalization: _build_properties applies apply_merges +
# canonicalize_plurals, not just get_live_taxonomy's candidate-list generation
# (2026-08-09 incident: "claude" drifted back in because the curated MERGES map
# was only ever applied by the one-time offline script, never at actual write
# time -- see PROGRESS.md and app/notion_writer.py's _build_properties).

def test_create_page_canonicalizes_merged_and_pluralized_topics(monkeypatch, tutorial_reel, tutorial_extraction):
    client = FakeClient()
    monkeypatch.setattr(notion_writer, "_client", lambda: client)
    extraction = tutorial_extraction.model_copy(
        update={"topic_tags": ["claude", "startup", "sleep"]}
    )

    notion_writer.create_page(tutorial_reel, extraction, status="done")

    saves_ds_id = notion_writer._resolve_data_source_id(client, notion_writer.NOTION_DB_ID)
    save_call = next(
        c for c in client.pages.created if c["parent"].get("data_source_id") == saves_ds_id
    )
    tag_names = {t["name"] for t in save_call["properties"]["Topics"]["multi_select"]}
    assert tag_names == {"claude-ai", "startups", "sleep"}
