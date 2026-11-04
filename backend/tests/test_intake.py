"""Handoff intake (POST /records): already-HSDS records through the shared pipeline."""

from app.intake import IntakeService
from app.models import Outcome, UnverifiedReason, VerificationStatus
from app.verify.worker import drain_verify

from conftest import handoff_record


def test_complete_record_is_stored_and_queued_for_verification(ctx):
    result = IntakeService(ctx).intake("odrs", "22560", handoff_record(complete=True))
    assert result.outcome is Outcome.INSERTED
    assert result.verification_status is VerificationStatus.UNVERIFIED
    assert result.unverified_reason is None  # nothing missing; waiting for the verifier
    assert result.missing_fields == []
    doc = ctx.stores.blobs.get_json(result.s3_key)
    assert doc["id"] == result.service_id
    assert len(ctx.stores.verify_q) == 1
    drain_verify(ctx)  # stub verifier says "verified"
    assert ctx.repo.index_item(result.service_id)["verification_status"] == "verified"


def test_record_missing_important_fields_is_missing_information(ctx):
    result = IntakeService(ctx).intake("odrs", "22560", handoff_record())
    assert result.outcome is Outcome.INSERTED
    assert result.unverified_reason is UnverifiedReason.MISSING_INFORMATION
    assert set(result.missing_fields) == {"eligibility", "schedule", "languages", "accessibility", "contact"}


def test_missing_required_field_is_quarantined(ctx):
    bad = handoff_record()
    del bad["organization"]["description"]
    result = IntakeService(ctx).intake("odrs", "1", bad)
    assert result.outcome is Outcome.QUARANTINED
    assert any("description" in e for e in result.errors)
    assert not list(ctx.repo.index_items())


def test_reingest_same_record_is_skipped(ctx):
    svc = IntakeService(ctx)
    svc.intake("odrs", "22560", handoff_record())
    assert svc.intake("odrs", "22560", handoff_record()).outcome is Outcome.SKIPPED_DUPLICATE


def test_reingest_changed_record_updates(ctx):
    svc = IntakeService(ctx)
    first = svc.intake("odrs", "22560", handoff_record())
    changed = handoff_record()
    changed["name"] = "Food distribution and groceries"
    second = svc.intake("odrs", "22560", changed)
    assert second.outcome is Outcome.UPDATED
    assert second.service_id == first.service_id
    history = ctx.repo.history("service", first.service_id)
    assert any(m["field_name"] == "name" and m["updated_by"] == "source:odrs" for m in history)


def test_cross_source_same_org_is_flagged_possible_duplicate(ctx):
    svc = IntakeService(ctx)
    svc.intake("odrs", "22560", handoff_record(complete=True))
    other = handoff_record(complete=True)
    other["organization"]["id"] = "00000000-0000-0000-0000-0000000000a2"
    other["service_at_locations"][0]["id"] = "00000000-0000-0000-0000-0000000000b8"
    other["service_at_locations"][0]["location"]["id"] = "00000000-0000-0000-0000-0000000000b2"
    other["service_at_locations"][0]["location"]["addresses"][0]["id"] = "00000000-0000-0000-0000-0000000000c2"
    other["name"] = "Groceries program"
    result = svc.intake("ndp", "abc-999", other)
    assert result.outcome is Outcome.POSSIBLE_DUPLICATE
    assert result.unverified_reason is UnverifiedReason.POSSIBLE_DUPLICATE
    assert result.duplicate_of is not None


def test_distinct_orgs_both_inserted(ctx):
    svc = IntakeService(ctx)
    r1 = svc.intake("odrs", "1", handoff_record(name="Food Bank", postal="94103"))
    r2_rec = handoff_record(name="Legal Aid Society", postal="90001")
    r2_rec["organization"]["id"] = "00000000-0000-0000-0000-0000000000a3"
    r2 = svc.intake("odrs", "2", r2_rec)
    assert r1.outcome is Outcome.INSERTED and r2.outcome is Outcome.INSERTED


def test_batch_endpoint_counts(client):
    body = {"source": "odrs", "records": [handoff_record(), {"name": "no id"}]}
    out = client.post("/records/batch", json=body).json()
    assert out["received"] == 2 and out["stored"] == 1 and out["quarantined"] == 1
