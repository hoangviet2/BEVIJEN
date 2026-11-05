"""Check whether an HSDS record has the 'important' optional information.

HSDS *required* fields are already guaranteed by validation - a record missing
those is quarantined and never stored. This module checks the optional-but-
important fields that One Degree's specialists verify today: eligibility, hours
(schedule), languages, accessibility, and contact (phone or email).

A record missing any of these is treated as 'missing information' and stays
unverified. A record that has them all can be auto-marked verified (when it is
also not a duplicate).
"""

from __future__ import annotations

from typing import Any

# The important optional fields and how to detect them on a service record.
# Each entry: a human label -> a predicate that returns True when present.
IMPORTANT_FIELDS: dict[str, str] = {
    "eligibility": "eligibility_description",
    "schedule": "schedules",
    "languages": "languages",
    "accessibility": "accessibility",
    "contact": "phones/email",
}


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (str, list, dict)):
        return len(value) > 0
    return True


def missing_important_fields(record: dict[str, Any]) -> list[str]:
    """Return the labels of important fields missing from the record.

    Looks at the service record and, where relevant, its nested locations.
    """
    missing: list[str] = []

    if not _has_value(record.get("eligibility_description")):
        missing.append("eligibility")

    if not _has_value(record.get("schedules")) and not _any_location_has(record, "schedules"):
        missing.append("schedule")

    if not _has_value(record.get("languages")):
        missing.append("languages")

    if not _has_value(record.get("accessibility")) and not _any_location_has(
        record, "accessibility"
    ):
        missing.append("accessibility")

    has_phone = _has_value(record.get("phones")) or _any_location_has(record, "phones")
    has_email = _has_value(record.get("email"))
    if not has_phone and not has_email:
        missing.append("contact")

    return missing


def _any_location_has(record: dict[str, Any], key: str) -> bool:
    locations = list(record.get("locations") or [])
    locations += [sal.get("location") for sal in record.get("service_at_locations") or [] if isinstance(sal, dict)]
    for loc in locations:
        if isinstance(loc, dict) and _has_value(loc.get(key)):
            return True
    return False
