"""Unit tests for the single-persistent-page pattern (find_child_page_by_title
/ upsert_named_page) used by both digests -- see PROGRESS.md. A dedicated fake
client here (rather than reusing tests/test_pipeline.py's FakeClient) actually
models the parent-page -> child-page relationship, since FakeClient's block
listing always returns empty and can't represent "the page already exists"."""
from app import notion_writer


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
