"""Tests for the intake pipeline using an in-memory fake Storage.

No AWS is required: FakeStorage stands in for S3 + DynamoDB and implements the
same methods the IntakeService calls.
"""

from typing import Any

import pytest

from app.config import get_settings
from app.intake import IntakeService
from app.models import Outcome, UnverifiedReason, VerificationStatus


class FakeStorage:
    """In-memory stand-in for S3 + DynamoDB."""

    def __init__(self):
        self.docs: dict[str, dict] = {}
        self.items: dict[str, dict] = {}

    def s3_key_for(self, source: str, service_id: str) -> str:
        return f"hsds/{source}/{service_id}.json"

    def put_document(self, key: str, record: dict[str, Any]) -> None:
        self.docs[key] = record

    def get_document(self, key: str):
        return self.docs.get(key)

    def get_index_item(self, service_id: str):
        return self.items.get(service_id)

    def find_by_content_hash(self, content_hash: str):
        for item in self.items.values():
            if item.get("content_hash") == content_hash:
                return item
        return None

    def find_by_match_key(self, match_key: str):
        for item in self.items.values():
            if match_key and item.get("fuzzy_match_key") == match_key:
                return item
        return None

    def put_index_item(self, item: dict[str, Any]) -> None:
        self.items[item["service_id"]] = item


def valid_record(
    name="Second Harvest Food Bank",
    postal="94103",
    org_desc="A food bank.",
    complete=False,
):
    """Build a valid HSDS record.

    complete=False -> valid but missing important optional fields (eligibility,
    hours, languages, accessibility, contact), so it should land as
    'missing_information'.
    complete=True -> adds all important fields, so it should auto-verify.
    """
    record = {
        "id": "00000000-0000-0000-0000-000000000001",
        "name": "Food distribution",
        "status": "active",
        "organization": {
            "id": "00000000-0000-0000-0000-0000000000a1",
            "name": name,
            "description": org_desc,
        },
        "locations": [
            {
                "id": "00000000-0000-0000-0000-0000000000b1",
                "location_type": "physical",
                "addresses": [
                    {
                        "id": "00000000-0000-0000-0000-0000000000c1",
                        "address_1": "123 Main St",
                        "city": "San Francisco",
                        "state_province": "CA",
                        "postal_code": postal,
                        "country": "US",
                        "address_type": "physical",
                    }
                ],
            }
        ],
    }
    if complete:
        record["eligibility_description"] = "Open to all residents."
        record["schedules"] = [{"id": "00000000-0000-0000-0000-0000000000d1"}]
        record["languages"] = [{"id": "00000000-0000-0000-0000-0000000000e1"}]
        record["accessibility"] = [{"id": "00000000-0000-0000-0000-0000000000f1"}]
        record["phones"] = [
            {"id": "00000000-0000-0000-0000-0000000000a9", "number": "510-613-0330"}
        ]
    return record


@pytest.fixture
def service():
    return IntakeService(get_settings(), FakeStorage())


def test_complete_non_duplicate_record_is_verified(service):
    result = service.intake("odrs", "22560", valid_record(complete=True))
    assert result.outcome is Outcome.INSERTED
    assert result.verification_status is VerificationStatus.VERIFIED
    assert result.unverified_reason is None
    assert result.missing_fields == []
    assert result.service_id
    # Stored document carries the id and the verified extension (no reason).
    doc = service._storage.get_document(result.s3_key)
    assert doc["id"] == result.service_id
    assert doc["x_resource_engine"]["verification"]["status"] == "verified"
    assert "unverified_reason" not in doc["x_resource_engine"]["verification"]


def test_record_missing_important_fields_is_missing_information(service):
    result = service.intake("odrs", "22560", valid_record(complete=False))
    assert result.outcome is Outcome.INSERTED
    assert result.verification_status is VerificationStatus.UNVERIFIED
    assert result.unverified_reason is UnverifiedReason.MISSING_INFORMATION
    # The minimal record lacks eligibility, hours, languages, accessibility, contact.
    assert set(result.missing_fields) == {
        "eligibility",
        "schedule",
        "languages",
        "accessibility",
        "contact",
    }
    doc = service._storage.get_document(result.s3_key)
    assert doc["x_resource_engine"]["verification"]["status"] == "unverified"
    assert doc["x_resource_engine"]["verification"]["unverified_reason"] == "missing_information"


def test_missing_required_field_is_quarantined(service):
    bad = valid_record()
    del bad["organization"]["description"]  # HSDS requires organization.description
    result = service.intake("odrs", "1", bad)
    assert result.outcome is Outcome.QUARANTINED
    assert any("description" in e for e in result.errors)
    assert not service._storage.items  # nothing stored


def test_reingest_same_record_is_skipped(service):
    service.intake("odrs", "22560", valid_record())
    again = service.intake("odrs", "22560", valid_record())
    assert again.outcome is Outcome.SKIPPED_DUPLICATE


def test_reingest_changed_record_updates(service):
    first = service.intake("odrs", "22560", valid_record())
    changed = valid_record()
    changed["name"] = "Food distribution and groceries"
    second = service.intake("odrs", "22560", changed)
    assert second.outcome is Outcome.UPDATED
    assert second.service_id == first.service_id


def test_cross_source_same_org_is_flagged_possible_duplicate(service):
    # Even a complete record is NOT auto-verified when it looks like a duplicate.
    service.intake("odrs", "22560", valid_record(complete=True))
    other = valid_record(complete=True, org_desc="Provides free groceries to residents.")
    other["name"] = "Groceries program"
    result = service.intake("ndp", "abc-999", other)
    assert result.outcome is Outcome.POSSIBLE_DUPLICATE
    assert result.verification_status is VerificationStatus.UNVERIFIED
    assert result.unverified_reason is UnverifiedReason.POSSIBLE_DUPLICATE
    assert result.duplicate_of is not None


def test_distinct_orgs_both_inserted(service):
    r1 = service.intake("odrs", "1", valid_record(name="Food Bank", postal="94103"))
    r2 = service.intake("odrs", "2", valid_record(name="Legal Aid Society", postal="90001"))
    assert r1.outcome is Outcome.INSERTED
    assert r2.outcome is Outcome.INSERTED
