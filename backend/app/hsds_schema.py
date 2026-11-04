"""Pragmatic HSDS v3.2.3 validation.

This validates the required fields for the HSDS objects that appear in an
intake payload. It is intentionally a subset focused on the required
properties listed in the Open Referral HSDS schema reference, so that the
API never stores or serves a record missing a field HSDS requires.

For full validation, pin a tagged release of the official schema files from
github.com/openreferral/specification (tag v3.2.3, schema/*.json) and validate
with a JSON Schema library (jsonschema). The structure below mirrors those
required-field lists so swapping in the official schema is a drop-in change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Required properties per HSDS object (v3.2.3 schema reference).
REQUIRED_FIELDS: dict[str, list[str]] = {
    "organization": ["id", "name", "description"],
    "service": ["id", "name", "status"],
    "location": ["id", "location_type"],
    "address": [
        "id",
        "address_1",
        "city",
        "state_province",
        "postal_code",
        "country",
        "address_type",
    ],
    "phone": ["id", "number"],
    "service_at_location": ["id"],
    "schedule": ["id"],
    "url": ["id", "url"],
    "attribute": ["id"],
    "language": ["id"],
    "accessibility": ["id"],
    "service_area": ["id"],
    "required_document": ["id"],
    "cost_option": ["id"],
}

# Allowed enum values worth enforcing.
ALLOWED_VALUES: dict[str, dict[str, set[str]]] = {
    "service": {"status": {"active", "inactive", "defunct", "temporarily closed"}},
    "location": {"location_type": {"physical", "postal", "virtual"}},
    "address": {"address_type": {"physical", "postal", "virtual"}},
}


@dataclass
class ValidationResult:
    """Outcome of validating a single HSDS record."""

    valid: bool
    errors: list[str] = field(default_factory=list)


def _validate_object(obj_type: str, obj: dict[str, Any], path: str) -> list[str]:
    errors: list[str] = []
    for required in REQUIRED_FIELDS.get(obj_type, ["id"]):
        value = obj.get(required)
        if value is None or value == "":
            errors.append(f"{path}: missing required '{required}' for {obj_type}")

    for prop, allowed in ALLOWED_VALUES.get(obj_type, {}).items():
        value = obj.get(prop)
        if value is not None and value not in allowed:
            errors.append(
                f"{path}: {obj_type}.{prop} value '{value}' not in {sorted(allowed)}"
            )
    return errors


def validate_service_record(record: dict[str, Any]) -> ValidationResult:
    """Validate one HSDS service record and its nested objects.

    The intake payload is a single ``service`` object with nested
    ``organization``, ``location`` / ``address`` / ``phone`` and so on. We walk
    the parts we know and check required fields. Unknown properties are allowed
    (HSDS permits extensions).
    """
    errors: list[str] = []

    if not isinstance(record, dict):
        return ValidationResult(valid=False, errors=["record must be a JSON object"])

    errors += _validate_object("service", record, "service")

    org = record.get("organization")
    if org is None:
        errors.append("service: missing required 'organization'")
    elif isinstance(org, dict):
        errors += _validate_object("organization", org, "service.organization")

    errors += _validate_nested_list(record, "locations", "location", _validate_location)
    errors += _validate_nested_list(record, "phones", "phone", _validate_simple("phone"))
    errors += _validate_nested_list(record, "schedules", "schedule", _validate_simple("schedule"))
    errors += _validate_nested_list(record, "service_at_locations", "service_at_location", _validate_simple("service_at_location"))

    return ValidationResult(valid=not errors, errors=errors)


def _validate_simple(obj_type: str):
    def _inner(obj: dict[str, Any], path: str) -> list[str]:
        return _validate_object(obj_type, obj, path)

    return _inner


def _validate_location(obj: dict[str, Any], path: str) -> list[str]:
    errors = _validate_object("location", obj, path)
    addresses = obj.get("addresses") or ([] if obj.get("address") is None else [obj["address"]])
    for i, addr in enumerate(addresses):
        if isinstance(addr, dict):
            errors += _validate_object("address", addr, f"{path}.address[{i}]")
    return errors


def _validate_nested_list(record: dict[str, Any], key: str, obj_type: str, validator):
    errors: list[str] = []
    items = record.get(key)
    if items is None:
        return errors
    if not isinstance(items, list):
        return [f"service.{key}: expected a list of {obj_type} objects"]
    for i, item in enumerate(items):
        if isinstance(item, dict):
            errors += validator(item, f"service.{key}[{i}]")
    return errors
