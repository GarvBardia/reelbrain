"""Unit tests for the sqlite-vec storage layer. No mocking here — sqlite-vec is a
local SQLite extension (deterministic vector math), not a live external service,
so exercising it directly is the honest test rather than mocking around it.
"""
import pytest

from app import store


def _vector(primary_index: int, *, blend: float = 0.0, dim: int = 768) -> list[float]:
    """A near-one-hot vector: 1.0 at `primary_index`, `blend` at the next slot over.
    Two vectors with the same primary_index but different blend are highly similar
    but not identical — handy for near-dup vs. merely-related test cases."""
    v = [0.0] * dim
    v[primary_index] = 1.0
    if blend:
        v[(primary_index + 1) % dim] = blend
    return v


def test_upsert_and_find_neighbors_orders_by_similarity():
    assert store.vec_available(), "sqlite-vec must be installed for these tests to mean anything"

    # cosine_sim against a pure e0 query is 1/sqrt(1+blend^2) for these vectors:
    # blend=0.1 -> ~0.995 (near-dup), blend=0.75 -> exactly 0.8 (related, not a dup)
    store.upsert_embedding("NEARDUP", _vector(0, blend=0.1))
    store.upsert_embedding("RELATED", _vector(0, blend=0.75))
    store.upsert_embedding("UNRELATED", _vector(400))  # orthogonal -> cosine_sim 0

    neighbors = store.find_neighbors(_vector(0), k=4)
    by_shortcode = dict(neighbors)

    assert by_shortcode["NEARDUP"] > 0.92
    assert 0.75 < by_shortcode["RELATED"] < 0.92
    assert by_shortcode["UNRELATED"] < 0.1
    # nearest first
    assert [sc for sc, _ in neighbors] == ["NEARDUP", "RELATED", "UNRELATED"]


def test_find_neighbors_on_empty_table_returns_empty():
    assert store.find_neighbors(_vector(0), k=4) == []


def test_upsert_embedding_replaces_existing():
    store.upsert_embedding("SHIFT001", _vector(0))
    store.upsert_embedding("SHIFT001", _vector(500))  # moved far away

    neighbors = store.find_neighbors(_vector(0), k=4)
    assert dict(neighbors)["SHIFT001"] < 0.1  # reflects the new vector, not the old one


def test_dimension_mismatch_raises_not_silently_wrong():
    with pytest.raises(Exception):
        store.upsert_embedding("BADDIM", [1.0, 2.0, 3.0])  # not 768-dim


def test_count_saves_by_creator():
    for i in range(3):
        shortcode = f"CREATOR_A_{i}"
        store.insert_processing(shortcode, f"https://www.instagram.com/reel/{shortcode}/")
        store.update_save(shortcode, creator="janedoe")
    store.insert_processing("OTHER1", "https://www.instagram.com/reel/OTHER1/")
    store.update_save("OTHER1", creator="someoneelse")

    assert store.count_saves_by_creator("janedoe") == 3
    assert store.count_saves_by_creator("someoneelse") == 1
    assert store.count_saves_by_creator("nobody") == 0
