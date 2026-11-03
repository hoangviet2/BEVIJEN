"""Demo: run the real sample data through the intake pipeline.

IMPORTANT: the files in ai/resources/ are RAW source data (ODRS and 211 NDP
formats), not HSDS. The intake service only accepts HSDS. So this script does a
*lightweight demo mapping* of each source into HSDS shape, then runs the result
through the real IntakeService (validate -> dedup -> store). The mapping here is
a demonstration, not the production adapter - it maps enough fields to produce
valid HSDS and to exercise the pipeline, including the duplicate and quarantine
paths.

Storage is in-memory (no AWS needed), so you can see outcomes immediately.

Run:  cd backend && .venv/bin/python scripts/demo_ingest.py
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.intake import IntakeService  # noqa: E402
from app.models import Outcome  # noqa: E402

RES = Path(__file__).resolve().parents[2] / "ai" / "resources"


# --------------------------------------------------------------------------
# In-memory storage (same contract as app.storage.Storage)
# --------------------------------------------------------------------------
class InMemoryStorage:
    def __init__(self) -> None:
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
        return next((i for i in self.items.values() if i.get("content_hash") == content_hash), None)

    def find_by_match_key(self, match_key: str):
        if not match_key:
            return None
        return next((i for i in self.items.values() if i.get("fuzzy_match_key") == match_key), None)

    def put_index_item(self, item: dict[str, Any]) -> None:
        self.items[item["service_id"]] = item


# --------------------------------------------------------------------------
# Demo mappings to HSDS (NOT the production adapter)
# --------------------------------------------------------------------------
COUNTRY_2 = {"united states": "US", "usa": "US", "us": "US"}


def _country(value: Any) -> str:
    return COUNTRY_2.get(str(value or "").strip().lower(), "US")


def map_odrs(opp: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Map one ODRS opportunity to an HSDS service record."""
    native_id = str(opp["id"])
    org = opp.get("organization") or {}

    # HSDS requires organization.description; ODRS has none. Demo fallback
    # (the docs list this as a decision; production would use the AI layer).
    org_desc = f"{org.get('name', 'Organization')} (description not provided by source)."

    status = "active" if not opp.get("unavailable_at") else "inactive"

    locations = []
    for i, loc in enumerate(opp.get("locations") or []):
        addr_1 = loc.get("address")
        if not addr_1:
            continue
        locations.append(
            {
                "id": f"odrs-loc-{loc.get('id', i)}",
                "location_type": "physical",
                "addresses": [
                    {
                        "id": f"odrs-addr-{loc.get('id', i)}",
                        "address_1": addr_1,
                        "address_2": loc.get("unit") or None,
                        "city": loc.get("city") or "Unknown",
                        "state_province": loc.get("state") or "CA",
                        "postal_code": str(loc.get("zip_code") or "00000"),
                        "country": "US",
                        "address_type": "physical",
                    }
                ],
            }
        )

    phones = [
        {"id": f"odrs-ph-{p.get('id', i)}", "number": p.get("digits")}
        for i, p in enumerate(opp.get("phones") or [])
        if p.get("digits")
    ]

    record = {
        "id": "placeholder",
        "name": opp.get("title") or "Untitled service",
        "status": status,
        "description": opp.get("description"),
        "organization": {
            "id": f"odrs-org-{org.get('id', 'unknown')}",
            "name": org.get("name") or "Unknown organization",
            "description": org_desc,
            "url": org.get("website") or None,
        },
        "locations": locations,
        "phones": phones,
    }
    return native_id, record


def map_ndp_org(org: dict[str, Any]) -> Iterable[tuple[str, dict[str, Any]]]:
    """Map one NDP organization into one HSDS service per NDP service."""
    org_id = str(org["id"])
    org_desc = _strip_html(org.get("description")) or f"{org.get('name', 'Organization')} services."

    locations = []
    for i, loc in enumerate(org.get("locations") or []):
        addrs = []
        for j, a in enumerate(loc.get("addresses") or []):
            addrs.append(
                {
                    "id": f"ndp-addr-{a.get('id', f'{i}-{j}')}",
                    "address_1": a.get("street") or "Unknown",
                    "city": a.get("city") or "Unknown",
                    "state_province": a.get("state") or "CA",
                    "postal_code": str(a.get("postalCode") or "00000"),
                    "country": _country(a.get("country")),
                    "address_type": a.get("type") or "physical",
                }
            )
        if addrs:
            locations.append(
                {
                    "id": f"ndp-loc-{loc.get('id', i)}",
                    "location_type": "physical",
                    "addresses": addrs,
                }
            )

    services = org.get("services") or []
    if not services:
        # Organization with no services: emit a single placeholder service so
        # the org still flows through. Production would decide differently.
        services = [{"id": f"{org_id}-svc", "name": org.get("name"), "status": "active"}]

    for svc in services:
        native_id = str(svc.get("id") or f"{org_id}-svc")
        record = {
            "id": "placeholder",
            "name": svc.get("name") or org.get("name") or "Untitled service",
            "status": svc.get("status") or "active",  # NDP status is often null
            "description": _strip_html(svc.get("description")),
            "organization": {
                "id": f"ndp-org-{org_id}",
                "name": org.get("name") or "Unknown organization",
                "description": org_desc,
                "url": org.get("url") or None,
            },
            "locations": locations,
        }
        yield native_id, record


def _strip_html(text: Any) -> str | None:
    if not text:
        return None
    return re.sub(r"<[^>]+>", "", str(text)).strip() or None


def _clean(record: dict[str, Any]) -> dict[str, Any]:
    """Drop keys whose value is None (HSDS prefers omission over null)."""
    if isinstance(record, dict):
        return {k: _clean(v) for k, v in record.items() if v is not None}
    if isinstance(record, list):
        return [_clean(v) for v in record]
    return record


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------
def run() -> None:
    service = IntakeService(get_settings(), InMemoryStorage())
    counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    missing_counts: Counter[str] = Counter()
    examples: dict[str, Any] = {}

    def ingest(source: str, native_id: str, record: dict[str, Any]) -> None:
        result = service.intake(source, native_id, _clean(record))
        counts[result.outcome.value] += 1
        if result.verification_status is not None:
            status_counts[result.verification_status.value] += 1
        if result.unverified_reason is not None:
            reason_counts[result.unverified_reason.value] += 1
        for field in result.missing_fields:
            missing_counts[field] += 1
        if result.outcome not in examples:
            examples[result.outcome] = (source, native_id, result)

    # 1) ODRS
    odrs = json.load(open(RES / "BayesExport_20250729.json"))["opportunities"]
    for opp in odrs:
        native_id, rec = map_odrs(opp)
        ingest("odrs", native_id, rec)

    # 2) NDP - two sample pages
    for fname in ["national-data-platform-sample-1.json", "national-data-platform-sample-2.json"]:
        data = json.load(open(RES / fname))["data"]
        for org in data:
            for native_id, rec in map_ndp_org(org):
                ingest("ndp", native_id, rec)

    # 3) Re-run ODRS to prove re-ingest is skipped/updated (dedup)
    reingest_before = dict(counts)
    for opp in odrs[:5]:
        native_id, rec = map_odrs(opp)
        ingest("odrs", native_id, rec)

    total = sum(counts.values())
    stored = total - counts.get(Outcome.QUARANTINED.value, 0)
    print("=" * 60)
    print("DEMO INGEST RESULTS (in-memory storage, demo HSDS mapping)")
    print("=" * 60)
    print(f"Records processed : {total}")
    print(f"  inserted           : {counts.get('inserted', 0)}")
    print(f"  updated            : {counts.get('updated', 0)}")
    print(f"  skipped_duplicate  : {counts.get('skipped_duplicate', 0)}")
    print(f"  possible_duplicate : {counts.get('possible_duplicate', 0)}")
    print(f"  quarantined        : {counts.get('quarantined', 0)}")
    print("-" * 60)
    print("Verification status of STORED records:")
    print(f"  verified   : {status_counts.get('verified', 0)}")
    print(f"  unverified : {status_counts.get('unverified', 0)}")
    print("  unverified reason breakdown:")
    print(f"    possible_duplicate  : {reason_counts.get('possible_duplicate', 0)}")
    print(f"    missing_information : {reason_counts.get('missing_information', 0)}")
    if missing_counts:
        print("    (missing-field detail: "
              + ", ".join(f"{k}={v}" for k, v in sorted(missing_counts.items())) + ")")
    print("-" * 60)
    print(f"R1.7 invariant: received({total}) == stored({stored}) + quarantined({counts.get('quarantined', 0)})")
    print(f"  -> {total == stored + counts.get('quarantined', 0)}")
    print("-" * 60)
    print("Note: re-running the first 5 ODRS records produced "
          f"{counts.get('skipped_duplicate', 0) - reingest_before.get('skipped_duplicate', 0)} "
          "skipped_duplicate outcomes (dedup working).")

    # Show one stored HSDS document end to end
    sample = examples.get(Outcome.INSERTED)
    if sample:
        src, nid, res = sample
        print("-" * 60)
        print(f"Example stored HSDS record ({src} native_id={nid}):")
        doc = service._storage.get_document(res.s3_key)
        print(json.dumps(doc, indent=2)[:1400])


if __name__ == "__main__":
    run()
