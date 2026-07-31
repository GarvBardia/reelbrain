"""scripts/classify_uncategorized.py — the A/B/C split that decides which
uncategorized rows represent LOST WORK vs genuinely-empty ones. Pure/mocked."""
from scripts import classify_uncategorized as cu


def _row(title="", status_label="", body_text=""):
    return {"shortcode": "SC1", "page_id": "pg", "title": title,
            "status_label": status_label, "named_entities": [], "value_score": "",
            "body_text": body_text}


# --- bucket A: real content that never got extracted ---------------------------------

def test_substantial_caption_is_bucket_a():
    assert cu.classify_row(_row(title="x", body_text=" ".join(["word"] * 40))) == "A"


def test_substantial_title_alone_is_bucket_a():
    assert cu.classify_row(_row(title=" ".join(["word"] * 20))) == "A"


def test_awaiting_dm_row_with_content_is_still_bucket_a():
    """Status doesn't disqualify a row — an Awaiting-DM row with a real caption
    still has unextracted content (many live bucket-A rows look exactly so)."""
    row = _row(title="c", status_label="⏳ Awaiting DM", body_text=" ".join(["w"] * 30))
    assert cu.classify_row(row) == "A"


# --- bucket B: genuine placeholders ---------------------------------------------------

def test_placeholder_title_is_bucket_b():
    assert cu.classify_row(_row(title=cu.PLACEHOLDER_TITLE)) == "B"


def test_placeholder_wins_even_inside_failed_retry():
    row = _row(title=cu.PLACEHOLDER_TITLE, status_label=cu.FAILED_RETRY_STATUS)
    assert cu.classify_row(row) == "B"


def test_too_thin_content_is_bucket_b_not_a():
    assert cu.classify_row(_row(title="just four words here")) == "B"


# --- bucket C: failed-retry with a bare URL ------------------------------------------

def test_bare_permalink_in_failed_retry_is_bucket_c():
    row = _row(title="https://www.instagram.com/p/ABC123/",
               status_label=cu.FAILED_RETRY_STATUS)
    assert cu.classify_row(row) == "C"


def test_bare_permalink_any_status_is_bucket_c():
    assert cu.classify_row(_row(title="https://instagram.com/reel/XYZ/")) == "C"


# --- caption word counting -------------------------------------------------------------

def test_word_count_ignores_pipeline_markers():
    assert cu.caption_word_count("(no caption) (no speech detected)") == 0


def test_word_count_ignores_bare_urls():
    assert cu.caption_word_count("https://example.com/a/b") == 0


def test_word_count_counts_real_prose():
    assert cu.caption_word_count("one two three four five") == 5


# --- the split ---------------------------------------------------------------------------

def test_classify_all_splits_into_three_buckets():
    rows = [
        _row(title=" ".join(["w"] * 30)),                       # A
        _row(title=cu.PLACEHOLDER_TITLE),                       # B
        _row(title="https://www.instagram.com/p/Z/",
             status_label=cu.FAILED_RETRY_STATUS),              # C
    ]
    buckets = cu.classify_all(rows)
    assert len(buckets["A"]) == 1 and len(buckets["B"]) == 1 and len(buckets["C"]) == 1
