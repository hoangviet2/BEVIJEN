"""Tests for the important-fields completeness check."""

from app.completeness import missing_important_fields


def _full():
    return {
        "name": "svc",
        "eligibility_description": "Open to all.",
        "schedules": [{"id": "s1"}],
        "languages": [{"id": "l1"}],
        "accessibility": [{"id": "a1"}],
        "phones": [{"id": "p1", "number": "510-000-0000"}],
    }


def test_complete_record_has_no_missing_fields():
    assert missing_important_fields(_full()) == []


def test_empty_record_missing_all():
    assert set(missing_important_fields({"name": "svc"})) == {
        "eligibility",
        "schedule",
        "languages",
        "accessibility",
        "contact",
    }


def test_contact_satisfied_by_email():
    rec = _full()
    del rec["phones"]
    rec["email"] = "info@example.org"
    assert "contact" not in missing_important_fields(rec)


def test_schedule_and_accessibility_counted_on_locations():
    rec = _full()
    del rec["schedules"]
    del rec["accessibility"]
    rec["locations"] = [{"schedules": [{"id": "s1"}], "accessibility": [{"id": "a1"}]}]
    missing = missing_important_fields(rec)
    assert "schedule" not in missing
    assert "accessibility" not in missing


def test_empty_list_counts_as_missing():
    rec = _full()
    rec["languages"] = []
    assert "languages" in missing_important_fields(rec)
