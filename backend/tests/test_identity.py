"""Tests for deterministic ids, content hashing, and fuzzy match keys."""

from app.config import get_settings
from app.identity import content_hash, deterministic_id, fuzzy_match_key

NS = get_settings().id_namespace


def test_deterministic_id_is_stable_and_case_insensitive_on_source():
    a = deterministic_id("odrs", "22560", NS)
    b = deterministic_id("ODRS", "22560", NS)
    assert a == b
    # Same inputs always produce the same id (re-ingest upsert).
    assert a == deterministic_id("odrs", "22560", NS)


def test_deterministic_id_differs_by_source_and_native_id():
    assert deterministic_id("odrs", "1", NS) != deterministic_id("ndp", "1", NS)
    assert deterministic_id("odrs", "1", NS) != deterministic_id("odrs", "2", NS)


def test_content_hash_ignores_volatile_fields():
    base = {"id": "x", "name": "Food Bank", "status": "active"}
    with_ext = {
        **base,
        "x_resource_engine": {"verification": {"status": "unverified"}},
        "last_modified": "2026-01-01T00:00:00Z",
    }
    assert content_hash(base) == content_hash(with_ext)


def test_content_hash_changes_with_substance():
    a = {"id": "x", "name": "Food Bank", "status": "active"}
    b = {"id": "x", "name": "Food Pantry", "status": "active"}
    assert content_hash(a) != content_hash(b)


def test_fuzzy_match_key_matches_despite_suffix_and_punctuation():
    r1 = {
        "name": "Service",
        "organization": {"name": "Second Harvest Food Bank, Inc."},
        "locations": [{"addresses": [{"postal_code": "94103"}]}],
    }
    r2 = {
        "name": "Service",
        "organization": {"name": "second harvest food bank"},
        "locations": [{"addresses": [{"postal_code": "94103-1234"}]}],
    }
    assert fuzzy_match_key(r1) == fuzzy_match_key(r2)


def test_fuzzy_match_key_none_without_signal():
    assert fuzzy_match_key({"name": "x", "organization": {"name": "Food Bank"}}) is None
