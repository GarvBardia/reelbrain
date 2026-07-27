"""Phase D: root-level vault files are namespaced for a future merge into a
larger Obsidian vault. All mocked, tmp_path as the vault."""
from app import vault_paths
from scripts import namespace_vault_files as nvf


def test_every_root_filename_is_namespaced():
    for name in (vault_paths.INDEX_FILENAME, vault_paths.INSTALLED_FILENAME,
                 vault_paths.QUEUE_FILENAME, vault_paths.SCOUT_INPUT_FILENAME):
        assert name.startswith("ReelBrain-"), name
        assert name.endswith(".md")


def test_legacy_map_covers_every_old_generic_name():
    assert set(vault_paths.LEGACY_NAMES) == {
        "_index.md", "INSTALLED.md", "IMPLEMENTATION_QUEUE.md", "scout_input.md"}


def test_entry_point_stems_cover_both_old_and_new_names():
    """vault_orphans must not flag either spelling as an orphan -- a vault
    mid-migration can legitimately contain the old names."""
    assert "ReelBrain-Index" in vault_paths.ROOT_ENTRY_POINT_STEMS
    assert "_index" in vault_paths.ROOT_ENTRY_POINT_STEMS


# --- planning ---------------------------------------------------------------------

def test_plan_lists_only_files_that_exist(tmp_path):
    (tmp_path / "_index.md").write_text("x", encoding="utf-8")
    plan = nvf.plan_renames(tmp_path)
    assert plan == {"_index.md": "ReelBrain-Index.md"}


def test_plan_is_empty_once_migrated(tmp_path):
    (tmp_path / "ReelBrain-Index.md").write_text("x", encoding="utf-8")
    assert nvf.plan_renames(tmp_path) == {}


def test_plan_skips_when_target_already_exists(tmp_path):
    """Never clobber an existing namespaced file with a stale legacy one."""
    (tmp_path / "_index.md").write_text("old", encoding="utf-8")
    (tmp_path / "ReelBrain-Index.md").write_text("new", encoding="utf-8")
    assert nvf.plan_renames(tmp_path) == {}


# --- link rewriting ----------------------------------------------------------------

def test_rewrites_plain_aliased_and_extension_links():
    plan = {"_index.md": "ReelBrain-Index.md"}
    text = "see [[_index]] and [[_index|home]] and [[_index.md]] and [[_index#Topics]]"
    out = nvf.rewrite_links(text, plan)
    assert "[[ReelBrain-Index]]" in out
    assert "[[ReelBrain-Index|home]]" in out
    assert "[[ReelBrain-Index#Topics]]" in out
    assert "_index" not in out.replace("ReelBrain-Index", "")


def test_does_not_rewrite_unrelated_or_prefixed_links():
    plan = {"_index.md": "ReelBrain-Index.md"}
    text = "[[reels/my-note]] and [[_index_backup]] and the word _index in prose"
    out = nvf.rewrite_links(text, plan)
    assert "[[reels/my-note]]" in out
    assert "[[_index_backup]]" in out   # different target, must be untouched


# --- applying -----------------------------------------------------------------------

def test_apply_renames_and_rewrites(tmp_path):
    (tmp_path / "reels").mkdir()
    (tmp_path / "_index.md").write_text("# index", encoding="utf-8")
    (tmp_path / "reels" / "a.md").write_text("home: [[_index]]", encoding="utf-8")

    renamed, relinked = nvf.apply(tmp_path, nvf.plan_renames(tmp_path),
                                  dry_run=False, print_fn=lambda *a: None)

    assert (renamed, relinked) == (1, 1)
    assert (tmp_path / "ReelBrain-Index.md").exists()
    assert not (tmp_path / "_index.md").exists()
    assert "[[ReelBrain-Index]]" in (tmp_path / "reels" / "a.md").read_text(encoding="utf-8")


def test_dry_run_changes_nothing(tmp_path):
    (tmp_path / "_index.md").write_text("# index", encoding="utf-8")
    nvf.apply(tmp_path, nvf.plan_renames(tmp_path), dry_run=True, print_fn=lambda *a: None)
    assert (tmp_path / "_index.md").exists()
    assert not (tmp_path / "ReelBrain-Index.md").exists()


def test_obsidian_config_dir_untouched(tmp_path):
    (tmp_path / ".obsidian").mkdir()
    (tmp_path / ".obsidian" / "cfg.md").write_text("[[_index]]", encoding="utf-8")
    (tmp_path / "_index.md").write_text("x", encoding="utf-8")
    nvf.apply(tmp_path, nvf.plan_renames(tmp_path), dry_run=False, print_fn=lambda *a: None)
    assert (tmp_path / ".obsidian" / "cfg.md").read_text(encoding="utf-8") == "[[_index]]"


# --- sync writes to the namespaced index -------------------------------------------

def test_sync_index_uses_namespaced_filename(tmp_path):
    from app import obsidian_sync

    obsidian_sync.write_topics_index(tmp_path, {"claude-ai": [
        {"stem": "s", "title": "T", "value_score": 4, "posted": "2026-07-01",
         "main_point": "mp", "plain_summary": "", "priority": "High"}]})
    assert (tmp_path / vault_paths.INDEX_FILENAME).exists()
    assert not (tmp_path / "_index.md").exists()
