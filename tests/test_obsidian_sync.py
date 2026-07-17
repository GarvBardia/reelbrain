"""Obsidian vault sync — fake Notion client (read shapes: plain_text, pagination,
blocks.children.list by block id), real local SQLite + sqlite-vec for the Related
links, tmp_path as the vault."""
from app import obsidian_sync, store
from app.obsidian_sync import _slugify, sync


# --- fake Notion client (read side) -------------------------------------------

class _FakeDatabases:
    def retrieve(self, database_id):
        return {"id": database_id, "data_sources": [{"id": f"ds-{database_id or 'default'}"}]}


class _FakeDataSources:
    def __init__(self, pages, page_size=100):
        self._pages = pages
        self._page_size = page_size
        self.query_calls = 0

    def query(self, data_source_id, start_cursor=None, **kwargs):
        self.query_calls += 1
        start = int(start_cursor or 0)
        chunk = self._pages[start:start + self._page_size]
        has_more = start + self._page_size < len(self._pages)
        return {
            "results": chunk,
            "has_more": has_more,
            "next_cursor": str(start + self._page_size) if has_more else None,
        }


class _FakeBlockChildren:
    def __init__(self, blocks_by_id):
        self._blocks_by_id = blocks_by_id

    def list(self, block_id, start_cursor=None, **kwargs):
        return {"results": self._blocks_by_id.get(block_id, []), "has_more": False}


class _FakeBlocks:
    def __init__(self, blocks_by_id):
        self.children = _FakeBlockChildren(blocks_by_id)


class SyncFakeClient:
    def __init__(self, pages, blocks_by_id, page_size=100):
        self.databases = _FakeDatabases()
        self.data_sources = _FakeDataSources(pages, page_size)
        self.blocks = _FakeBlocks(blocks_by_id)


# --- fixtures-in-miniature ------------------------------------------------------

def _rt(text):
    return [{"plain_text": text}]


def _page(shortcode, title, status="📥 Inbox", topics=(), value="4", posted="2026-07-01"):
    return {
        "id": f"pg-{shortcode}",
        "created_time": "2026-07-02T00:00:00.000Z",
        "properties": {
            "Shortcode": {"rich_text": _rt(shortcode)},
            "Title": {"title": _rt(title)},
            "Status": {"select": {"name": status}},
            "Value score": {"select": {"name": value}},
            "Topics": {"multi_select": [{"name": t} for t in topics]},
            "Reel URL": {"url": f"https://www.instagram.com/reel/{shortcode}/"},
            "Posted at": {"date": {"start": posted} if posted else None},
        },
    }


def _body(shortcode):
    return [
        {"type": "callout", "callout": {"rich_text": _rt("Main insight here")}},
        {"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": _rt("Point A")}},
        {"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": _rt("Point B")}},
        {"type": "numbered_list_item", "numbered_list_item": {"rich_text": _rt("Do the thing")}},
        {"type": "bookmark", "bookmark": {"url": "https://tool.example"}},
        {"type": "quote", "quote": {"rich_text": _rt("Verbatim line")}},
        {"type": "toggle", "id": f"tg-{shortcode}", "has_children": True,
         "toggle": {"rich_text": _rt("Transcript")}},
    ]


def _toggle_children(text):
    return [{"type": "paragraph", "paragraph": {"rich_text": _rt(text)}}]


def _vector(primary, blend=0.0, dim=768):
    v = [0.0] * dim
    v[primary] = 1.0
    if blend:
        v[(primary + 1) % dim] = blend
    return v


def _install(monkeypatch, pages, blocks_by_id):
    client = SyncFakeClient(pages, blocks_by_id)
    monkeypatch.setattr("app.notion_writer._client", lambda: client)
    return client


def _seed_row(shortcode, creator="janedoe"):
    store.insert_processing(shortcode, f"https://www.instagram.com/reel/{shortcode}/")
    store.update_save(shortcode, creator=creator, status="done")


# --- tests -----------------------------------------------------------------------

def test_full_sync_writes_note_with_frontmatter_sections_and_stubs(monkeypatch, tmp_path):
    _install(monkeypatch, [_page("AAA111", "Sleep tips", topics=("sleep", "health"))],
             {"pg-AAA111": _body("AAA111"), "tg-AAA111": _toggle_children("full transcript text")})
    _seed_row("AAA111")

    result = sync(str(tmp_path))

    note_path = tmp_path / "reels" / "2026-07-01-AAA111.md"
    assert note_path.exists()
    content = note_path.read_text(encoding="utf-8")

    # frontmatter
    assert "shortcode: AAA111" in content
    assert 'creator: "[[creators/janedoe]]"' in content
    assert '- "[[topics/sleep]]"' in content
    assert "url: https://www.instagram.com/reel/AAA111/" in content
    assert "posted: 2026-07-01" in content
    # sections in order, converted from blocks
    assert "## Main point" in content and "> Main insight here" in content
    assert "## Supporting points" in content and "- Point A" in content
    assert "## Steps" in content and "1. Do the thing" in content
    assert "## Resources" in content and "<https://tool.example>" in content
    assert "## Quotable lines" in content and "> Verbatim line" in content
    assert "## Transcript" in content and "full transcript text" in content
    # stubs exist so no wikilink dangles
    assert (tmp_path / "creators" / "janedoe.md").exists()
    assert (tmp_path / "topics" / "sleep.md").exists()
    assert (tmp_path / "topics" / "health.md").exists()
    assert result["notes_written"] == 1


def test_related_section_from_stored_embeddings(monkeypatch, tmp_path):
    pages = [_page("REL_A", "First", topics=("x",)), _page("REL_B", "Second", topics=("x",)),
             _page("REL_C", "Unrelated", topics=("y",))]
    blocks = {f"pg-{s}": _body(s) for s in ("REL_A", "REL_B", "REL_C")}
    blocks.update({f"tg-{s}": _toggle_children("t") for s in ("REL_A", "REL_B", "REL_C")})
    _install(monkeypatch, pages, blocks)
    for shortcode in ("REL_A", "REL_B", "REL_C"):
        _seed_row(shortcode)
    store.upsert_embedding("REL_A", _vector(0))
    store.upsert_embedding("REL_B", _vector(0, blend=0.2))   # similar to A
    store.upsert_embedding("REL_C", _vector(400))            # orthogonal

    sync(str(tmp_path))

    a_note = (tmp_path / "reels" / "2026-07-01-REL_A.md").read_text(encoding="utf-8")
    assert "## Related" in a_note
    assert "[[reels/2026-07-01-REL_B]]" in a_note   # nearest other save, as a real wikilink
    # the note never links to itself
    assert "[[reels/2026-07-01-REL_A]]" not in a_note


def test_no_embedding_means_no_related_section(monkeypatch, tmp_path):
    _install(monkeypatch, [_page("LONE1", "Alone")],
             {"pg-LONE1": _body("LONE1"), "tg-LONE1": _toggle_children("t")})
    _seed_row("LONE1")

    sync(str(tmp_path))
    content = (tmp_path / "reels" / "2026-07-01-LONE1.md").read_text(encoding="utf-8")
    assert "## Related" not in content


def test_idempotent_rerun_updates_in_place(monkeypatch, tmp_path):
    blocks = {"pg-IDEM1": _body("IDEM1"), "tg-IDEM1": _toggle_children("t")}
    _install(monkeypatch, [_page("IDEM1", "V1", status="📥 Inbox")], blocks)
    _seed_row("IDEM1")
    sync(str(tmp_path))

    # second run: status changed AND posted date changed (filename would differ) —
    # the note must be matched by frontmatter shortcode and updated, not duplicated
    _install(monkeypatch, [_page("IDEM1", "V1", status="✅ Processed/Reviewed",
                                 posted="2026-07-15")], blocks)
    sync(str(tmp_path))

    notes = list((tmp_path / "reels").glob("*.md"))
    assert len(notes) == 1
    assert notes[0].name == "2026-07-01-IDEM1.md"  # original path kept
    assert "✅ Processed/Reviewed" in notes[0].read_text(encoding="utf-8")


def test_pagination_walks_all_pages(monkeypatch, tmp_path):
    pages = [_page(f"PG{i:03d}", f"Save {i}") for i in range(5)]
    blocks = {}
    for i in range(5):
        blocks[f"pg-PG{i:03d}"] = _body(f"PG{i:03d}")
        blocks[f"tg-PG{i:03d}"] = _toggle_children("t")
    client = SyncFakeClient(pages, blocks, page_size=2)
    monkeypatch.setattr("app.notion_writer._client", lambda: client)
    for i in range(5):
        _seed_row(f"PG{i:03d}")

    result = sync(str(tmp_path))
    assert result["notes_written"] == 5
    assert client.data_sources.query_calls == 3  # 2 + 2 + 1


def test_topics_index_lists_counts(monkeypatch, tmp_path):
    pages = [_page("IX1", "One", topics=("sleep",)), _page("IX2", "Two", topics=("sleep", "money"))]
    blocks = {}
    for s in ("IX1", "IX2"):
        blocks[f"pg-{s}"] = _body(s)
        blocks[f"tg-{s}"] = _toggle_children("t")
    _install(monkeypatch, pages, blocks)
    _seed_row("IX1")
    _seed_row("IX2")

    sync(str(tmp_path))
    index = (tmp_path / "_index.md").read_text(encoding="utf-8")
    assert "# Topics Index" in index
    assert "[[topics/sleep|sleep]] — 2 saves" in index
    assert "[[topics/money|money]] — 1 save" in index


def test_stub_not_overwritten_on_rerun(monkeypatch, tmp_path):
    """User notes added to a stub must survive re-syncs."""
    blocks = {"pg-STUB1": _body("STUB1"), "tg-STUB1": _toggle_children("t")}
    _install(monkeypatch, [_page("STUB1", "X", topics=("sleep",))], blocks)
    _seed_row("STUB1")
    sync(str(tmp_path))

    stub = tmp_path / "topics" / "sleep.md"
    stub.write_text("# sleep\n\n## Notes\nmy precious thoughts\n", encoding="utf-8")
    sync(str(tmp_path))
    assert "my precious thoughts" in stub.read_text(encoding="utf-8")


def test_slugify():
    assert _slugify("AI Workflows!") == "ai-workflows"
    assert _slugify("désí créator") == "d-s-cr-ator"
    assert _slugify("---") == "unnamed"
