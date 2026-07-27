"""scripts/archive_dated_digests.py — all mocked."""
from scripts import archive_dated_digests as add


def _child(title, page_id=None):
    return {"type": "child_page", "id": page_id or f"pg-{title[:8]}", "child_page": {"title": title}}


class _FakeClient:
    def __init__(self, blocks):
        self._blocks = blocks
        self.archived = []
        outer = self

        class _Children:
            @staticmethod
            def list(block_id, start_cursor=None):
                return {"results": outer._blocks, "has_more": False}

        class _Blocks:
            children = _Children()

        class _Pages:
            @staticmethod
            def update(page_id, archived=None):
                outer.archived.append((page_id, archived))
                return {"id": page_id}

        self.blocks = _Blocks()
        self.pages = _Pages()


# --- the pattern: only pre-fix dated titles match --------------------------------

def test_dated_titles_match():
    for title in ("🌙 Daily reflection — 2026-07-20", "📬 Weekly digest — 2026-07-20",
                  "🌙 Daily reflection — 2026-07-21"):
        assert add.DATED_DIGEST_RE.match(title), title


def test_live_persistent_pages_never_match():
    """The live pages differ in BOTH case and the missing date suffix -- they
    must be impossible to catch by accident."""
    for title in ("🌙 Daily Reflection", "📬 Weekly Digest", "🔍 Attach Audit Log", "🔭 Scout Pick"):
        assert not add.DATED_DIGEST_RE.match(title), title


def test_arbitrary_user_pages_never_match():
    for title in ("My notes", "Daily reflection", "📬 Weekly digest", "Reading list — 2026-07-20"):
        assert not add.DATED_DIGEST_RE.match(title), title


# --- listing + splitting -----------------------------------------------------------

def test_list_child_pages_returns_only_child_pages():
    client = _FakeClient([
        _child("🌙 Daily reflection — 2026-07-20"),
        {"type": "paragraph", "id": "p1", "paragraph": {}},
        _child("🔍 Attach Audit Log"),
    ])
    pages = add.list_child_pages(client, "parent")
    assert [p["title"] for p in pages] == ["🌙 Daily reflection — 2026-07-20", "🔍 Attach Audit Log"]


def test_split_separates_artifacts_from_live_pages():
    pages = [
        {"page_id": "1", "title": "🌙 Daily reflection — 2026-07-20"},
        {"page_id": "2", "title": "📬 Weekly digest — 2026-07-20"},
        {"page_id": "3", "title": "🌙 Daily Reflection"},
        {"page_id": "4", "title": "🔍 Attach Audit Log"},
        {"page_id": "5", "title": "🔭 Scout Pick"},
        {"page_id": "6", "title": "📬 Weekly Digest"},
    ]
    dated, kept = add.split_dated_digests(pages)
    assert [p["page_id"] for p in dated] == ["1", "2"]
    assert [p["page_id"] for p in kept] == ["3", "4", "5", "6"]


# --- archiving ---------------------------------------------------------------------

def test_archive_sets_archived_true_never_deletes():
    client = _FakeClient([])
    count = add.archive_pages(client, [{"page_id": "pg-1", "title": "t"}], print_fn=lambda *a: None)
    assert count == 1
    assert client.archived == [("pg-1", True)]


def test_archive_continues_past_a_failure():
    client = _FakeClient([])

    def _boom(page_id, archived=None):
        if page_id == "bad":
            raise RuntimeError("notion down")
        client.archived.append((page_id, archived))

    client.pages.update = _boom
    count = add.archive_pages(
        client, [{"page_id": "bad", "title": "x"}, {"page_id": "ok", "title": "y"}],
        print_fn=lambda *a: None)
    assert count == 1
    assert client.archived == [("ok", True)]
