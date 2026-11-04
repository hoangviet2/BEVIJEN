"""Turning a person's decision into HSDS row changes (always via commit()).

- `apply_field`  : a finding's HSDS path (e.g. `service_areas[].name`) + value
- `apply_patch`  : record-grid edits as JSON-Patch ops on flat objects,
                   path = /<table>/<id>/<field>   (PATCH /records/{id})
- `flatten`      : a nested HSDS service document -> rows (handoff intake,
                   discovery create_record)
"""

from __future__ import annotations

import copy
import uuid
from typing import Any

from hsds_transform.coerce import coerce_dataset
from hsds_transform.convert import convert_records

from .hsds.render import Rows

_NS = uuid.UUID("9b8f3c1e-55aa-4b8e-bf51-0d8e2a7c6f14")

SERVICE_COLUMNS = {"name", "alternate_name", "description", "url", "email", "status", "interpretation_services",
                   "application_process", "fees_description", "wait_time", "fees", "accreditations",
                   "eligibility_description", "minimum_age", "maximum_age", "assured_date", "assurer_email",
                   "alert", "licenses"}
ORG_COLUMNS = {"name", "alternate_name", "description", "email", "website", "tax_status", "tax_id",
               "year_incorporated", "legal_status", "logo", "uri", "parent_organization_id"}


class EditError(ValueError):
    pass


def _child_rows(rows: Rows, table: str, service_id: str) -> list[str]:
    return [rid for rid, r in rows.get(table, {}).items() if r.get("service_id") == service_id]


def _replace_children(rows: Rows, table: str, service_id: str, new: list[dict]) -> Rows:
    """Return the change-set: new rows (deterministic ids) for a service."""
    changes: Rows = {table: {}}
    for i, values in enumerate(new):
        rid = str(uuid.uuid5(_NS, f"{table}|{service_id}|{i}|{sorted(values.items())}"))
        changes[table][rid] = {"id": rid, "service_id": service_id, **values}
    return changes


def apply_field(rows: Rows, service_id: str, field: str, value: Any) -> tuple[Rows, list[str]]:
    """Return (changed rows, ids of rows to retire) for one field update."""
    svc = rows.get("service", {}).get(service_id)
    if svc is None:
        raise EditError("service not found")
    field = field.strip()
    if field.startswith("organization."):
        col = field.split(".", 1)[1]
        if col not in ORG_COLUMNS:
            raise EditError(f"unsupported organization field {col!r}")
        org = copy.deepcopy(rows["organization"][svc["organization_id"]])
        org[col] = value
        return {"organization": {org["id"]: org}}, []
    if field in SERVICE_COLUMNS or field == "status":
        new = copy.deepcopy(svc)
        new[field] = value
        return {"service": {service_id: new}}, []
    as_list = value if isinstance(value, list) else [value]
    if field in ("service_areas[].name", "service_areas"):
        new = [{"name": v} if isinstance(v, str) else v for v in as_list]
        return _replace_children(rows, "service_area", service_id, new), _child_rows(rows, "service_area", service_id)
    if field in ("languages[].name", "languages"):
        new = [{"name": v} if isinstance(v, str) else v for v in as_list]
        return _replace_children(rows, "language", service_id, new), _child_rows(rows, "language", service_id)
    if field in ("required_documents[].document", "required_documents"):
        new = [{"document": v} if isinstance(v, str) else v for v in as_list]
        return (_replace_children(rows, "required_document", service_id, new),
                _child_rows(rows, "required_document", service_id))
    if field in ("schedules", "schedules[]"):
        if not all(isinstance(v, dict) for v in as_list):
            raise EditError("schedules must be a list of schedule objects")
        return _replace_children(rows, "schedule", service_id, as_list), _child_rows(rows, "schedule", service_id)
    if field in ("phones[].number", "phones"):
        new = [{"number": v, "type": "voice"} if isinstance(v, str) else v for v in as_list]
        return _replace_children(rows, "phone", service_id, new), _child_rows(rows, "phone", service_id)
    raise EditError(f"field {field!r} can't be applied automatically; edit it in the record grid (PATCH /records)")


def apply_patch(rows: Rows, ops: list[dict]) -> tuple[Rows, list[tuple[str, str]]]:
    """JSON-Patch-lite on flat rows. Returns (changed rows, removed (table, id))."""
    changes: Rows = {}
    removed: list[tuple[str, str]] = []
    for op in ops:
        parts = [p for p in str(op.get("path", "")).split("/") if p]
        kind = op.get("op")
        if kind not in ("add", "replace", "remove") or len(parts) not in (2, 3):
            raise EditError(f"bad op {op!r}; use add/replace/remove on /<table>/<id>[/<field>]")
        table, rid = parts[0], parts[1]
        if len(parts) == 2:
            if kind == "remove":
                removed.append((table, rid))
                continue
            if not isinstance(op.get("value"), dict):
                raise EditError("adding a row needs an object value")
            changes.setdefault(table, {})[rid] = {**op["value"], "id": rid}
            continue
        base = changes.get(table, {}).get(rid) or copy.deepcopy(rows.get(table, {}).get(rid))
        if base is None:
            raise EditError(f"/{table}/{rid} not found")
        if parts[2] == "id":
            raise EditError("ids can't be changed")
        if kind == "remove":
            base.pop(parts[2], None)
        else:
            base[parts[2]] = op.get("value")
        changes.setdefault(table, {})[rid] = base
    return changes, removed


def flatten(doc: dict, source: str) -> Rows:
    """Nested HSDS service document -> flat rows (ids that are UUIDs are kept)."""
    ds = convert_records([doc], source, f"{source}:{doc.get('id')}", root="service")
    coerce_dataset(ds)
    if ds.errors:
        raise EditError(f"could not read the HSDS document: {ds.errors[0]['error']}")
    return ds.rows
