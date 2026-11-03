"""End-to-end smoke test against REAL AWS (S3 + DynamoDB).

Uses the real Storage (no mocks). Pushes a few HSDS records through the intake
pipeline and prints the outcome. Reads back from both stores to prove the data
landed.

Run:  cd backend && .venv/bin/python scripts/aws_smoke.py
Env (defaults target team20 resources in us-west-2):
  RE_S3_BUCKET=team20-s3-test-bucket
  RE_DDB_TABLE=team20-resource-engine-index
  AWS_REGION=us-west-2
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.intake import IntakeService  # noqa: E402
from app.storage import Storage  # noqa: E402


def complete_record(name: str, postal: str) -> dict:
    """A complete HSDS record that should auto-verify."""
    return {
        "id": "placeholder",
        "name": "Food distribution",
        "status": "active",
        "eligibility_description": "Open to all residents.",
        "schedules": [{"id": "sch-1"}],
        "languages": [{"id": "lang-1"}],
        "accessibility": [{"id": "acc-1"}],
        "phones": [{"id": "ph-1", "number": "510-613-0330"}],
        "organization": {"id": "org-1", "name": name, "description": f"{name} - services."},
        "locations": [
            {
                "id": "loc-1",
                "location_type": "physical",
                "accessibility": [{"id": "acc-1"}],
                "addresses": [
                    {
                        "id": "addr-1",
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


def main() -> int:
    settings = get_settings()
    print(f"Bucket : {settings.s3_bucket}")
    print(f"Table  : {settings.ddb_table}")
    print(f"Region : {settings.aws_region}")
    print("-" * 60)

    storage = Storage(settings)
    service = IntakeService(settings, storage)

    # 1. Complete, unique record -> verified
    r1 = service.intake("smoke", "rec-001", complete_record("Second Harvest Food Bank", "94103"))
    print(f"1) complete/unique   -> {r1.outcome.value} / {r1.verification_status.value}")

    # 2. Same record again -> skipped_duplicate
    r2 = service.intake("smoke", "rec-001", complete_record("Second Harvest Food Bank", "94103"))
    print(f"2) re-ingest same    -> {r2.outcome.value}")

    # 3. Incomplete record -> missing_information
    thin = complete_record("Legal Aid Society", "90001")
    for k in ["eligibility_description", "schedules", "languages", "accessibility", "phones"]:
        thin.pop(k, None)
    thin["locations"][0].pop("accessibility", None)
    r3 = service.intake("smoke", "rec-002", thin)
    reason3 = r3.unverified_reason.value if r3.unverified_reason else None
    print(f"3) incomplete        -> {r3.outcome.value} / {r3.verification_status.value} "
          f"(reason: {reason3}, missing: {r3.missing_fields})")

    # 4. Invalid record -> quarantined
    bad = complete_record("No Desc Org", "94103")
    del bad["organization"]["description"]
    r4 = service.intake("smoke", "rec-003", bad)
    print(f"4) invalid           -> {r4.outcome.value} (errors: {r4.errors})")

    print("-" * 60)
    print("Reading back record 1 from S3 and DynamoDB...")
    doc = storage.get_document(r1.s3_key)
    item = storage.get_index_item(r1.service_id)
    print(f"  S3 doc id            : {doc['id']}")
    print(f"  S3 verification      : {doc['x_resource_engine']['verification']['status']}")
    print(f"  DynamoDB status      : {item['verification_status']}")
    print(f"  DynamoDB reason      : {item.get('unverified_reason')}")
    print(f"  DynamoDB s3_key      : {item['s3_key']}")
    print("-" * 60)
    print("OK - data is in both S3 and DynamoDB.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
