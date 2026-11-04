"""Validate HSDS 3.2.3 objects with the official JSON Schema files.

The schema files are vendored unmodified (openreferral/specification tag v3.2.3)
in `backend/transform/hsds_transform/schema/` and shared with the transform
package, so there is exactly one pinned copy in the repo.

All HSDS that leaves the system goes through `validate_object` (AGENT.md §8).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

import hsds_transform

SCHEMA_DIR = Path(hsds_transform.__file__).parent / "schema"


@dataclass
class ValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)


@lru_cache(maxsize=1)
def _registry() -> Registry:
    resources = []
    for path in SCHEMA_DIR.glob("*.json"):
        contents = json.loads(path.read_text())
        resources.append((path.name, Resource.from_contents(contents, default_specification=DRAFT202012)))
    return Registry().with_resources(resources)


@lru_cache(maxsize=None)
def _validator(object_type: str) -> Draft202012Validator:
    schema = json.loads((SCHEMA_DIR / f"{object_type}.json").read_text())
    return Draft202012Validator(schema, registry=_registry(), format_checker=FormatChecker())


def validate_object(object_type: str, obj: Any, max_errors: int = 25) -> ValidationResult:
    """Validate one nested HSDS object (service, organization, location, ...)."""
    if not isinstance(obj, dict):
        return ValidationResult(False, [f"{object_type}: must be a JSON object"])
    errors = []
    for err in _validator(object_type).iter_errors(obj):
        path = ".".join(str(p) for p in err.absolute_path) or object_type
        errors.append(f"{path}: {err.message[:200]}")
        if len(errors) >= max_errors:
            break
    return ValidationResult(not errors, errors)


def validate_service(doc: dict) -> ValidationResult:
    """A service document must validate and carry its organization (design §5.3)."""
    result = validate_object("service", doc)
    org = doc.get("organization") if isinstance(doc, dict) else None
    if not isinstance(org, dict):
        result.errors.insert(0, "service: missing required 'organization'")
        result.valid = False
    return result
