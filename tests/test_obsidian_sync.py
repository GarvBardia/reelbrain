"""Obsidian vault sync — fake Notion client (read shapes: plain_text, pagination,
blocks.children.list by block id), real local SQLite + sqlite-vec for the Related
links, tmp_path as the vault."""
from app import obsidian_sync, store
from app.obsidian_sync import AUTO_END, AUTO_START, _slugify, sync, upsert_auto_block


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


# --- upsert_auto_block: the marker mechanics, tested directly -------------------

def test_upsert_auto_block_creates_new_file_with_default_header(tmp_path):
    path = tmp_path / "topics" / "sleep.md"
    upsert_auto_block(path, "# sleep\n\n## Notes\n", ["## Saved Reels", "", "- one"])
    content = path.read_text(encoding="utf-8")
    assert content.startswith("# sleep\n\n## Notes")
    assert AUTO_START in content and AUTO_END in content
    assert "- one" in content


def test_upsert_auto_block_preserves_prefix_with_no_prior_markers(tmp_path):
    """An old-style bare stub (from before this feature existed) has no markers at
    all — the whole thing must be kept as the prefix, block appended below."""
    path = tmp_path / "topics" / "sleep.md"
    path.parent.mkdir(parents=True)
    path.write_text("# sleep\n\n## Notes\nmy own thoughts about sleep\n", encoding="utf-8")

    upsert_auto_block(path, "# sleep\n\n## Notes\n", ["## Saved Reels", "", "- one"])

    content = path.read_text(encoding="utf-8")
    assert "my own thoughts about sleep" in content
    assert AUTO_START in content
    assert content.index("my own thoughts") < content.index(AUTO_START)


def test_upsert_auto_block_replaces_only_between_markers(tmp_path):
    path = tmp_path / "topics" / "sleep.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        "# sleep\n\nmy notes here\n\n"
        f"{AUTO_START}\n- stale entry\n{AUTO_END}\n",
        encoding="utf-8",
    )

    upsert_auto_block(path, "# sleep\n", ["## Saved Reels", "", "- fresh entry"])

    content = path.read_text(encoding="utf-8")
    assert "my notes here" in content
    assert "stale entry" not in content
    assert "fresh entry" in content


def test_upsert_auto_block_preserves_content_after_end_marker(tmp_path):
    path = tmp_path / "topics" / "sleep.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        f"# sleep\n\n{AUTO_START}\n- stale\n{AUTO_END}\n\nappended by hand at the bottom\n",
        encoding="utf-8",
    )

    upsert_auto_block(path, "# sleep\n", ["## Saved Reels", "", "- fresh"])

    content = path.read_text(encoding="utf-8")
    assert "appended by hand at the bottom" in content
    assert "stale" not in content
    assert "fresh" in content


# --- topic/creator "## Saved Reels" auto-index, and marker preservation ---------

def test_topic_note_gets_real_saved_reels_index_not_just_a_stub(monkeypatch, tmp_path):
    _install(monkeypatch, [_page("SR1", "Sleep Tips", topics=("sleep",))],
             {"pg-SR1": _body("SR1"), "tg-SR1": _toggle_children("t")})
    _seed_row("SR1")

    sync(str(tmp_path))

    topic_note = (tmp_path / "topics" / "sleep.md").read_text(encoding="utf-8")
    assert AUTO_START in topic_note and AUTO_END in topic_note
    assert "## Saved Reels" in topic_note
    assert "[[reels/2026-07-01-SR1|Sleep Tips]]" in topic_note
    assert "value 4" in topic_note
    assert "Main insight here" in topic_note  # the reel's Main Point, pulled in


def test_creator_note_also_gets_saved_reels_index(monkeypatch, tmp_path):
    _install(monkeypatch, [_page("CR1", "A Reel")],
             {"pg-CR1": _body("CR1"), "tg-CR1": _toggle_children("t")})
    _seed_row("CR1", creator="janedoe")

    sync(str(tmp_path))

    creator_note = (tmp_path / "creators" / "janedoe.md").read_text(encoding="utf-8")
    assert AUTO_START in creator_note
    assert "## Saved Reels" in creator_note
    assert "[[reels/2026-07-01-CR1|A Reel]]" in creator_note


def test_manually_edited_notes_section_survives_resync(monkeypatch, tmp_path):
    """The exact scenario asked for: a real prior-sync output, hand-edited above
    the markers, must come through a second sync untouched."""
    _install(monkeypatch, [_page("PERSIST1", "First Reel", topics=("sleep",))],
             {"pg-PERSIST1": _body("PERSIST1"), "tg-PERSIST1": _toggle_children("t")})
    _seed_row("PERSIST1")
    sync(str(tmp_path))  # first sync produces the real marker-based file

    topic_path = tmp_path / "topics" / "sleep.md"
    original = topic_path.read_text(encoding="utf-8")
    assert AUTO_START in original  # sanity: real sync output, not a hand-built fixture

    edited = original.replace(
        "## Notes\n", "## Notes\nSleep is mostly about consistent wake time, not hours.\n"
    )
    topic_path.write_text(edited, encoding="utf-8")

    sync(str(tmp_path))  # second sync: same single reel, nothing new tagged

    final = topic_path.read_text(encoding="utf-8")
    assert "Sleep is mostly about consistent wake time, not hours." in final
    assert "## Saved Reels" in final  # the auto section is still there too


def test_auto_block_updates_when_new_reel_tagged_with_existing_topic(monkeypatch, tmp_path):
    """Second sync adds a second reel under the same topic — the block must show
    BOTH reels, not just the new one and not a stale copy of just the first."""
    _install(monkeypatch, [_page("FIRST1", "First Reel", topics=("sleep",), value="3")],
             {"pg-FIRST1": _body("FIRST1"), "tg-FIRST1": _toggle_children("t")})
    _seed_row("FIRST1")
    sync(str(tmp_path))

    topic_path = tmp_path / "topics" / "sleep.md"
    topic_path.write_text(
        topic_path.read_text(encoding="utf-8").replace("## Notes\n", "## Notes\nkeep me\n"),
        encoding="utf-8",
    )

    _install(monkeypatch, [
        _page("FIRST1", "First Reel", topics=("sleep",), value="3"),
        _page("SECOND1", "Second Reel", topics=("sleep",), value="5", posted="2026-07-10"),
    ], {
        "pg-FIRST1": _body("FIRST1"), "tg-FIRST1": _toggle_children("t"),
        "pg-SECOND1": _body("SECOND1"), "tg-SECOND1": _toggle_children("t"),
    })
    _seed_row("SECOND1")
    sync(str(tmp_path))

    content = topic_path.read_text(encoding="utf-8")
    assert "keep me" in content  # user note untouched
    assert "[[reels/2026-07-01-FIRST1|First Reel]]" in content
    assert "[[reels/2026-07-10-SECOND1|Second Reel]]" in content
    # higher value_score (5) sorts before the lower one (3)
    assert content.index("Second Reel") < content.index("First Reel")


def test_saved_reels_sorted_by_value_desc_then_date_desc(monkeypatch, tmp_path):
    pages = [
        _page("SORT_LOW", "Low value", topics=("t",), value="2", posted="2026-07-20"),
        _page("SORT_HIGH_OLD", "High old", topics=("t",), value="5", posted="2026-07-01"),
        _page("SORT_HIGH_NEW", "High new", topics=("t",), value="5", posted="2026-07-15"),
    ]
    blocks = {}
    for s in ("SORT_LOW", "SORT_HIGH_OLD", "SORT_HIGH_NEW"):
        blocks[f"pg-{s}"] = _body(s)
        blocks[f"tg-{s}"] = _toggle_children("t")
    _install(monkeypatch, pages, blocks)
    for s in ("SORT_LOW", "SORT_HIGH_OLD", "SORT_HIGH_NEW"):
        _seed_row(s)

    sync(str(tmp_path))
    content = (tmp_path / "topics" / "t.md").read_text(encoding="utf-8")

    pos_new = content.index("High new")
    pos_old = content.index("High old")
    pos_low = content.index("Low value")
    assert pos_new < pos_old < pos_low  # value 5/newer, value 5/older, value 2


def test_index_shows_real_reel_previews_not_just_counts(monkeypatch, tmp_path):
    pages = [_page("PRV1", "Preview Reel One", topics=("sleep",)),
             _page("PRV2", "Preview Reel Two", topics=("sleep",))]
    blocks = {}
    for s in ("PRV1", "PRV2"):
        blocks[f"pg-{s}"] = _body(s)
        blocks[f"tg-{s}"] = _toggle_children("t")
    _install(monkeypatch, pages, blocks)
    _seed_row("PRV1")
    _seed_row("PRV2")

    sync(str(tmp_path))
    index = (tmp_path / "_index.md").read_text(encoding="utf-8")

    assert AUTO_START in index and AUTO_END in index
    assert "[[reels/2026-07-01-PRV1|Preview Reel One]]" in index
    assert "[[reels/2026-07-01-PRV2|Preview Reel Two]]" in index


def test_index_manual_preamble_survives_resync(monkeypatch, tmp_path):
    _install(monkeypatch, [_page("IXP1", "X", topics=("sleep",))],
             {"pg-IXP1": _body("IXP1"), "tg-IXP1": _toggle_children("t")})
    _seed_row("IXP1")
    sync(str(tmp_path))

    index_path = tmp_path / "_index.md"
    content = index_path.read_text(encoding="utf-8")
    index_path.write_text(
        content.replace("# Topics Index", "# Topics Index\n\nMy personal preamble note.")
        if "# Topics Index" in content else "My personal preamble note.\n\n" + content,
        encoding="utf-8",
    )

    sync(str(tmp_path))
    final = index_path.read_text(encoding="utf-8")
    assert "My personal preamble note." in final
