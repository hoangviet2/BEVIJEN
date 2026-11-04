"""Check converted rows against the HSDS 3.2.3 column rules (required, type, format,
enum) and check that every foreign key points at a row that exists."""
from __future__ import annotations

import re

from . import schema
from .mapper import Dataset, is_missing, is_uuid

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_URI = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:\S+$")
_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})$")

# Foreign key column -> table it references (where the name alone is not enough).
_FK_TARGET = {"parent_organization_id": "organization", "link_id": None, "resource_id": None,
              "parent_id": "taxonomy_term"}


def validate(ds: Dataset) -> list[dict]:
    issues: list[dict] = []

    def add(table, rid, column, problem, value=None):
        issues.append({"table": table, "id": rid, "column": column, "problem": problem,
                       "value": "" if value is None else str(value)[:200]})

    for table, rows in ds.rows.items():
        tdef = schema.table(table)
        for rid, row in rows.items():
            for name, col in tdef.columns.items():
                value = row.get(name)
                if is_missing(value):
                    if col.required:
                        add(table, rid, name, "required field missing (NA)")
                    continue
                if col.type in ("number", "integer") and (isinstance(value, bool) or
                                                         not isinstance(value, (int, float))):
                    add(table, rid, name, f"not a {col.type}", value)
                elif col.format == "uuid" and not is_uuid(value):
                    add(table, rid, name, "not a UUID", value)
                elif col.format == "email" and not _EMAIL.match(str(value)):
                    add(table, rid, name, "not an email address", value)
                elif col.format == "uri" and not _URI.match(str(value)):
                    add(table, rid, name, "not a URI", value)
                elif col.format == "date-time" and not _DATETIME.match(str(value)):
                    add(table, rid, name, "not an ISO 8601 date-time", value)
                elif col.enum and value not in col.enum:
                    add(table, rid, name, f"not one of {list(col.enum)}", value)
                if name.endswith("_id") and name != "external_identifier" and is_uuid(value):
                    target = _FK_TARGET.get(name, name[:-3])
                    if target and value not in ds.rows.get(target, {}):
                        add(table, rid, name, f"refers to a {target} that is not in this dataset", value)
    return issues
