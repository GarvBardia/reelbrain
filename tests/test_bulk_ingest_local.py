"""scripts/bulk_ingest_local.py — fully mocked (probe_fn / existing_fn / sleep /
print injected; no network, no clock)."""
from scripts import bulk_ingest_local as bil

U = "https://www.instagram.com/p/{}/"


def _probe_returning(outcomes: dict):
    """probe_fn stub: outcome keyed by shortcode, extracted from the URL."""
    def _probe(url, write=False):
        sc = url.rstrip("/").split("/")[-1]
        return {"url": url, "shortcode": sc, **outcomes[sc]}
    return _probe


def test_dedupes_against_notion_without_fetching(tmp_path):
    probed = []

    summary = bil.run_bulk_ingest(
        [U.format("NEW1"), U.format("DUP1")], str(tmp_path / "p.json"),
        probe_fn=lambda url, write=False: probed.append(url) or {
            "url": url, "shortcode": "NEW1", "outcome": "extracted", "written": True},
        existing_fn=lambda: {"DUP1"},
        sleep_fn=lambda s: None, print_fn=lambda m: None,
    )

    assert summary["written"] == 1
    assert summary["duplicates"] == 1
    assert probed == [U.format("NEW1")]  # DUP1 never fetched


def test_dry_run_fetches_nothing(tmp_path):
    probed = []
    summary = bil.run_bulk_ingest(
        [U.format("A1"), U.format("B2")], str(tmp_path / "p.json"), dry_run=True,
        probe_fn=lambda url, write=False: probed.append(url) or {"outcome": "extracted"},
        existing_fn=lambda: set(), sleep_fn=lambda s: None, print_fn=lambda m: None,
    )
    assert probed == []


def test_degraded_rows_retried_once_and_recovered(tmp_path):
    # A1 degrades on pass 1, succeeds on the retry pass
    call_count = {"A1": 0}

    def _probe(url, write=False):
        sc = url.rstrip("/").split("/")[-1]
        if sc == "A1":
            call_count["A1"] += 1
            if call_count["A1"] == 1:
                return {"url": url, "shortcode": sc, "outcome": "degraded"}
            return {"url": url, "shortcode": sc, "outcome": "extracted", "written": True}
        return {"url": url, "shortcode": sc, "outcome": "extracted", "written": True}

    slept = []
    summary = bil.run_bulk_ingest(
        [U.format("A1")], str(tmp_path / "p.json"),
        probe_fn=_probe, existing_fn=lambda: set(),
        sleep_fn=slept.append, print_fn=lambda m: None, retry_delay=42,
    )

    assert call_count["A1"] == 2            # retried exactly once
    assert slept == [42]                    # waited the delay before retrying
    assert summary["written"] == 1
    assert summary["degraded_remaining"] == 0
    assert summary["still_degraded"] == []


def test_persistently_degraded_reported_not_dropped(tmp_path):
    summary = bil.run_bulk_ingest(
        [U.format("STUCK1")], str(tmp_path / "p.json"),
        probe_fn=_probe_returning({"STUCK1": {"outcome": "degraded"}}),
        existing_fn=lambda: set(), sleep_fn=lambda s: None, print_fn=lambda m: None,
    )
    assert summary["degraded_remaining"] == 1
    assert summary["still_degraded"] == ["STUCK1"]
    assert summary["written"] == 0


def test_resume_skips_already_written(tmp_path):
    progress_file = str(tmp_path / "p.json")
    bil.save_progress(progress_file, {
        U.format("DONE1"): {"outcome": "extracted", "written": True},
    })
    probed = []
    summary = bil.run_bulk_ingest(
        [U.format("DONE1"), U.format("NEW1")], progress_file,
        probe_fn=lambda url, write=False: probed.append(url) or {
            "url": url, "shortcode": "NEW1", "outcome": "extracted", "written": True},
        existing_fn=lambda: set(), sleep_fn=lambda s: None, print_fn=lambda m: None,
    )
    assert probed == [U.format("NEW1")]  # DONE1 skipped
    assert summary["skipped"] == 1


def test_blocked_and_error_counted_and_retryable(tmp_path):
    summary = bil.run_bulk_ingest(
        [U.format("BLK1"), U.format("ERR1")], str(tmp_path / "p.json"),
        probe_fn=_probe_returning({
            "BLK1": {"outcome": "blocked"},
            "ERR1": {"outcome": "no_caption"},
        }),
        existing_fn=lambda: set(), sleep_fn=lambda s: None, print_fn=lambda m: None,
    )
    assert summary["blocked"] == 1
    assert summary["errors"] == 1
    # neither is terminal — a rerun would re-attempt (not written, not duplicate)
    progress = bil.load_progress(str(tmp_path / "p.json"))
    assert not bil._is_written(progress[U.format("BLK1")])


def test_header_and_junk_lines_skipped(tmp_path):
    summary = bil.run_bulk_ingest(
        ["reels", "not a url", U.format("REAL1")], str(tmp_path / "p.json"),
        probe_fn=_probe_returning({"REAL1": {"outcome": "extracted", "written": True}}),
        existing_fn=lambda: set(), sleep_fn=lambda s: None, print_fn=lambda m: None,
    )
    assert summary["written"] == 1
