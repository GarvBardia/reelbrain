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


def _page(shortcode, title, status="📥 Inbox", topics=(), value="4", posted="2026-07-01",
          priority=""):
    props = {
        "Shortcode": {"rich_text": _rt(shortcode)},
        "Title": {"title": _rt(title)},
        "Status": {"select": {"name": status}},
        "Value score": {"select": {"name": value}},
        "Topics": {"multi_select": [{"name": t} for t in topics]},
        "Reel URL": {"url": f"https://www.instagram.com/reel/{shortcode}/"},
        "Posted at": {"date": {"start": posted} if posted else None},
    }
    if priority:
        props["Priority"] = {"select": {"name": priority}}
    return {
        "id": f"pg-{shortcode}",
        "created_time": "2026-07-02T00:00:00.000Z",
        "properties": props,
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


def _contains_emoji(text: str) -> bool:
    """True if any character looks pictographic/emoji. Threshold chosen to
    cover every emoji this codebase actually uses (⏳=U+23F3, ⚠=U+26A0,
    🗑=U+1F5D1, 🎯=U+1F3AF, etc. — all >= U+2300) while NOT flagging ordinary
    punctuation this codebase also uses, like em-dash (—, U+2014) or curly
    quotes (U+2018-201D)."""
    return any(ord(ch) >= 0x2300 for ch in text)


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

    note_path = tmp_path / "reels" / "sleep-tips.md"
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


def test_reel_note_shows_plain_text_priority_and_score_no_emoji(monkeypatch, tmp_path):
    """Priority system: frontmatter AND body carry plain 'Priority: High' /
    'Score: 4' lines — no emoji anywhere in either."""
    _install(monkeypatch, [_page("PRI1", "Claude tips", topics=("claude-code",),
                                 value="4", priority="High")],
             {"pg-PRI1": _body("PRI1"), "tg-PRI1": _toggle_children("t")})
    _seed_row("PRI1")

    sync(str(tmp_path))
    content = (tmp_path / "reels" / "claude-tips.md").read_text(encoding="utf-8")

    assert "priority: High" in content        # frontmatter
    assert "Priority: High" in content        # body, plain text
    assert "Score: 4" in content               # body, plain text
    # the specific ask (item 5): the generated Priority/Score lines carry no emoji.
    priority_line = next(l for l in content.splitlines() if l.startswith("Priority:"))
    score_line = next(l for l in content.splitlines() if l.startswith("Score:"))
    assert not _contains_emoji(priority_line)
    assert not _contains_emoji(score_line)


def test_topic_stub_listing_shows_plain_priority_and_score_no_emoji(monkeypatch, tmp_path):
    _install(monkeypatch, [_page("PRI2", "Claude tips", topics=("mcp",),
                                 value="5", priority="High")],
             {"pg-PRI2": _body("PRI2"), "tg-PRI2": _toggle_children("t")})
    _seed_row("PRI2")

    sync(str(tmp_path))
    topic_note = (tmp_path / "topics" / "mcp.md").read_text(encoding="utf-8")

    assert "Priority: High" in topic_note
    assert "Score: 5" in topic_note
    assert "value 5" not in topic_note  # old "value N" phrasing fully replaced
    listing_line = next(l for l in topic_note.splitlines() if l.startswith("- [[reels/"))
    assert not _contains_emoji(listing_line)  # no emoji in the auto-generated reel listing


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

    a_note = (tmp_path / "reels" / "first.md").read_text(encoding="utf-8")
    assert "## Related" in a_note
    assert "[[reels/second]]" in a_note   # nearest other save, as a real wikilink
    # the note never links to itself
    assert "[[reels/first]]" not in a_note


def test_no_embedding_means_no_related_section(monkeypatch, tmp_path):
    _install(monkeypatch, [_page("LONE1", "Alone")],
             {"pg-LONE1": _body("LONE1"), "tg-LONE1": _toggle_children("t")})
    _seed_row("LONE1")

    sync(str(tmp_path))
    content = (tmp_path / "reels" / "alone.md").read_text(encoding="utf-8")
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
    assert notes[0].name == "v1.md"  # original path kept
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
    assert "[[reels/sleep-tips|Sleep Tips]]" in topic_note
    assert "Score: 4" in topic_note  # plain-text Score, replacing the old "value 4" phrasing
    assert "Main insight here" in topic_note  # the reel's Main Point, pulled in


def test_creator_note_also_gets_saved_reels_index(monkeypatch, tmp_path):
    _install(monkeypatch, [_page("CR1", "A Reel")],
             {"pg-CR1": _body("CR1"), "tg-CR1": _toggle_children("t")})
    _seed_row("CR1", creator="janedoe")

    sync(str(tmp_path))

    creator_note = (tmp_path / "creators" / "janedoe.md").read_text(encoding="utf-8")
    assert AUTO_START in creator_note
    assert "## Saved Reels" in creator_note
    assert "[[reels/a-reel|A Reel]]" in creator_note


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
    assert "[[reels/first-reel|First Reel]]" in content
    assert "[[reels/second-reel|Second Reel]]" in content
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


def test_index_groups_by_priority_first_with_topic_counts(monkeypatch, tmp_path):
    """_index.md's new structure (replacing the old alphabetical/by-volume topic
    dump): grouped by Priority tier first, each section listing topic links with
    a count of how many reels at that tier carry the topic."""
    pages = [
        _page("PRV1", "Preview Reel One", topics=("sleep",), priority="High"),
        _page("PRV2", "Preview Reel Two", topics=("sleep",), priority="High"),
        _page("PRV3", "Medium Reel", topics=("money",), priority="Medium"),
        _page("PRV4", "Low Reel", topics=("music",), priority="Low"),
    ]
    blocks = {}
    for s in ("PRV1", "PRV2", "PRV3", "PRV4"):
        blocks[f"pg-{s}"] = _body(s)
        blocks[f"tg-{s}"] = _toggle_children("t")
    _install(monkeypatch, pages, blocks)
    for s in ("PRV1", "PRV2", "PRV3", "PRV4"):
        _seed_row(s)

    sync(str(tmp_path))
    index = (tmp_path / "_index.md").read_text(encoding="utf-8")

    assert AUTO_START in index and AUTO_END in index
    assert "## High Priority" in index
    assert "## Medium Priority" in index
    assert "## Low Priority" in index
    # priority section ordering: High before Medium before Low
    assert index.index("## High Priority") < index.index("## Medium Priority") < index.index("## Low Priority")
    # topic + count, grouped under the correct tier — no more individual reel previews
    high_section = index[index.index("## High Priority"):index.index("## Medium Priority")]
    assert "[[topics/sleep|sleep]] — 2 saves" in high_section
    medium_section = index[index.index("## Medium Priority"):index.index("## Low Priority")]
    assert "[[topics/money|money]] — 1 save" in medium_section
    low_section = index[index.index("## Low Priority"):]
    assert "[[topics/music|music]] — 1 save" in low_section


def test_index_priority_section_shows_none_when_empty(monkeypatch, tmp_path):
    _install(monkeypatch, [_page("SOLO1", "Solo", topics=("sleep",), priority="Low")],
              {"pg-SOLO1": _body("SOLO1"), "tg-SOLO1": _toggle_children("t")})
    _seed_row("SOLO1")

    sync(str(tmp_path))
    index = (tmp_path / "_index.md").read_text(encoding="utf-8")
    high_section = index[index.index("## High Priority"):index.index("## Medium Priority")]
    assert "(none)" in high_section


def test_index_rows_without_priority_property_fall_into_low(monkeypatch, tmp_path):
    """Reels saved before the Priority property existed have no value at all —
    they must still show up (in Low), never silently vanish from the index."""
    _install(monkeypatch, [_page("NOPRI1", "No priority set", topics=("sleep",))],  # priority=""
              {"pg-NOPRI1": _body("NOPRI1"), "tg-NOPRI1": _toggle_children("t")})
    _seed_row("NOPRI1")

    sync(str(tmp_path))
    index = (tmp_path / "_index.md").read_text(encoding="utf-8")
    low_section = index[index.index("## Low Priority"):]
    assert "[[topics/sleep|sleep]] — 1 save" in low_section


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


# --- resource linking (scripts/ingest_resources.py bidirectional links) ---------

def _write_resource_note(vault, shortcode, stem, topics=("ai-tools",)):
    path = vault / "resources" / f"{stem}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---", f"source_shortcode: {shortcode}", "resource_kind: github_repo"]
    if topics:
        lines.append("topics:")
        lines += [f'  - "[[topics/{t}]]"' for t in topics]
        lines.append(f"topics_plain: {', '.join(topics)}")
    lines += ["---", "", "# Attached resource", "", "## Summary", "", "A summary."]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def test_existing_resource_notes_parses_shortcode_and_topics(tmp_path):
    _write_resource_note(tmp_path, "RES1", "RES1-some-guide", topics=("ai-tools", "web-design"))

    found = obsidian_sync.existing_resource_notes(tmp_path)

    assert found["RES1"]["stem"] == "RES1-some-guide"
    assert found["RES1"]["topics"] == ["ai-tools", "web-design"]


def test_existing_resource_notes_empty_when_no_resources_dir(tmp_path):
    assert obsidian_sync.existing_resource_notes(tmp_path) == {}


# --- research_context rendering (the generic any-titled-toggle mechanism) ------

def test_blocks_to_markdown_renders_research_context_toggle_as_its_own_section():
    """research_context lands on Notion as a "Research Context" toggle (see
    notion_writer._build_children) -- confirms obsidian_sync's existing
    generic toggle handling renders it as its own "## Research Context"
    section with each topic:context line visible, no code change needed
    beyond this test locking the behavior in."""
    blocks = [
        {"type": "callout", "callout": {"rich_text": _rt("Main insight here")}},
        {"type": "toggle", "id": "tg-research", "has_children": True,
         "toggle": {"rich_text": _rt("Research Context")}},
    ]
    toggle_children = {
        "tg-research": [
            {"type": "paragraph", "paragraph": {"rich_text": _rt("Cleanlist.ai: A LinkedIn scraping tool.")}},
            {"type": "paragraph", "paragraph": {"rich_text": _rt("Exply: not found via search")}},
        ],
    }
    client = SyncFakeClient([], toggle_children)

    md = obsidian_sync.blocks_to_markdown(client, blocks)

    assert "## Research Context" in md
    assert "Cleanlist.ai: A LinkedIn scraping tool." in md
    assert "Exply: not found via search" in md
    assert md.index("## Research Context") > md.index("Main insight here")


def test_sync_includes_research_context_section_in_reel_note(monkeypatch, tmp_path):
    body = _body("RCX1") + [
        {"type": "toggle", "id": "tg-RCX1-research", "has_children": True,
         "toggle": {"rich_text": _rt("Research Context")}},
    ]
    blocks_by_id = {
        "pg-RCX1": body,
        "tg-RCX1": _toggle_children("full transcript text"),
        "tg-RCX1-research": [
            {"type": "paragraph", "paragraph": {"rich_text": _rt("Cleanlist.ai: A LinkedIn scraping tool.")}},
        ],
    }
    _install(monkeypatch, [_page("RCX1", "A reel", topics=("productivity-hacks",))], blocks_by_id)
    _seed_row("RCX1")

    sync(str(tmp_path))

    note = (tmp_path / "reels" / "a-reel.md").read_text(encoding="utf-8")
    assert "## Research Context" in note
    assert "Cleanlist.ai: A LinkedIn scraping tool." in note


def test_build_note_renders_attached_resource_section_when_resource_stem_given():
    fields = {
        "shortcode": "AAA111", "title": "Some reel", "status": "📥 Inbox",
        "priority": "", "value_score": "", "topics": [], "url": "https://x",
        "posted": "2026-07-01", "gate_resource": "https://github.com/x/y",
    }
    note = obsidian_sync.build_note(fields, None, "body text", [], resource_stem="AAA111-some-guide")
    assert "## Attached Resource" in note
    assert "- [[resources/AAA111-some-guide]]" in note


def test_build_note_omits_attached_resource_section_without_resource_stem():
    fields = {
        "shortcode": "AAA111", "title": "Some reel", "status": "📥 Inbox",
        "priority": "", "value_score": "", "topics": [], "url": "https://x",
        "posted": "2026-07-01", "gate_resource": "",
    }
    note = obsidian_sync.build_note(fields, None, "body text", [])
    assert "## Attached Resource" not in note


def test_format_entry_line_marks_resource_entries_distinctly():
    entry = {"stem": "RES1-guide", "title": "RES1-guide", "folder": "resources",
              "value_score": None, "posted": "", "main_point": "attached resource", "priority": None}
    line = obsidian_sync._format_entry_line(entry)
    assert "[[resources/RES1-guide|RES1-guide]]" in line
    assert "📄" in line
    assert "Priority: —" not in line  # no placeholder dashes for a field that doesn't apply


def test_sync_links_reel_note_to_pre_existing_resource_and_folds_into_topic_index(monkeypatch, tmp_path):
    """End-to-end: a resources/*.md note already sitting in the vault (as
    scripts/ingest_resources.py would have written it) gets linked from the
    reel note on the next sync, and shows up in the topic index too."""
    _install(monkeypatch, [_page("RLK1", "Great reel", topics=("ai-tools",))],
             {"pg-RLK1": _body("RLK1"), "tg-RLK1": _toggle_children("t")})
    _seed_row("RLK1")
    _write_resource_note(tmp_path, "RLK1", "RLK1-a-great-guide", topics=("ai-tools",))

    sync(str(tmp_path))

    reel_note = (tmp_path / "reels" / "great-reel.md").read_text(encoding="utf-8")
    assert "## Attached Resource" in reel_note
    assert "[[resources/RLK1-a-great-guide]]" in reel_note

    topic_stub = (tmp_path / "topics" / "ai-tools.md").read_text(encoding="utf-8")
    assert "RLK1-a-great-guide" in topic_stub
    assert "📄" in topic_stub
