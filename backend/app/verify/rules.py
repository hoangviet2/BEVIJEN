"""Heuristic pre-flags: free, no AI (design §5 step 6).

Each rule writes a finding with model_id="rules". They run after every commit
of new or changed services, before the Verifier Agent sees the record, and give
the agent (and the specialist) a head start.
"""

from __future__ import annotations

import re
from datetime import date, datetime

from ..completeness import missing_important_fields
from ..config import Settings
from .findings import FindingsRepo

_ASK = {
    "eligibility": "Who can use this service (area, age, income)?",
    "schedule": "What days and hours is it open?",
    "languages": "Which languages do staff speak?",
    "accessibility": "Is the building wheelchair accessible?",
    "contact": "What is the best phone number or email for the public?",
}


def _days_old(value: str | None) -> int | None:
    if not value:
        return None
    try:
        d = datetime.fromisoformat(str(value)[:10]).date()
    except ValueError:
        return None
    return (date.today() - d).days


def preflag(doc: dict, settings: Settings, findings: FindingsRepo) -> list[dict]:
    sid = doc["id"]
    out: list[dict] = []

    def add(category: str, field: str, what: list[str], note: str = "", current=None):
        out.append(findings.create(service_id=sid, category=category, field=field, current_value=current,
                                   what_to_ask=what, note=note, confidence="high", model_id="rules"))

    for label in missing_important_fields(doc):
        add("missing_information", label, [_ASK[label]], f"record has no {label}")

    schedules = doc.get("schedules") or []
    text_only = [s for s in schedules if s.get("description") and not s.get("opens_at")]
    if text_only:
        add("missing_information", "schedules", ["What are the exact opening and closing times?"],
            "hours only as text", current=[s["description"] for s in text_only])

    has_street = any(a.get("address_type") == "physical" for sal in doc.get("service_at_locations") or []
                     for a in (sal.get("location") or {}).get("addresses") or [])
    virtual = any((sal.get("location") or {}).get("location_type") == "virtual"
                  for sal in doc.get("service_at_locations") or [])
    if (doc.get("service_at_locations") and not has_street and not virtual):
        add("needs_manual", "locations[].addresses", ["What is the street address where people go?"],
            "no physical address (e.g. 'Various locations')")

    age = _days_old(doc.get("assured_date"))
    if age is not None and age > settings.stale_days:
        add("stale", "assured_date", ["Is everything in this listing still correct?"],
            f"last verified {age} days ago", current=doc.get("assured_date"))

    for phone in doc.get("phones") or []:
        if len(re.sub(r"\D", "", str(phone.get("number", "")))) < 10:
            add("needs_manual", "phones[].number", ["What is the full phone number?"], current=phone.get("number"))

    if str((doc.get("organization") or {}).get("description", "")).endswith("Description not available."):
        add("missing_information", "organization.description", [],
            "source has no organization description; verifier drafts one (§12.3)")
    return out
