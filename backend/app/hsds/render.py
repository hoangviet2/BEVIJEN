"""HSDS rows <-> DynamoDB items <-> nested HSDS documents (design §4.3).

Internally every HSDS object is a flat row (HSDS field names, foreign keys as
`*_id`) — exactly the shape the transform package produces and the HSDS
tabular format uses. In DynamoDB each row is one item:

    PK = "SERVICE#<uuid>"  SK = "CURRENT"
    entity_type, org_pk (GSI1), entity_sk (GSI1), zip_pk (GSI2, locations/addresses)
    data = {the HSDS row}

`render_service` rebuilds the nested HSDS 3.2.3 service document from rows
using the transform package's schema-driven `nest`, with empty properties
omitted (the strict form for the API and exports).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from hsds_transform.mapper import Dataset, is_missing
from hsds_transform.writer import NAPolicy, _Index, nest

Rows = dict[str, dict[str, dict]]
SHARED_ORG = "SHARED"  # taxonomies / taxonomy terms are not owned by one organization
ORPHAN = "__orphan__"  # rows whose organization can't be resolved
_OMIT = NAPolicy("omit")

# How to reach a row's organization: ordered (fk column, table it points at).
_OWNER_PATH: dict[str, list[tuple[str, str]]] = {
    "service": [("organization_id", "organization")],
    "location": [("organization_id", "organization")],
    "program": [("organization_id", "organization")],
    "funding": [("organization_id", "organization"), ("service_id", "service")],
    "organization_identifier": [("organization_id", "organization")],
    "address": [("location_id", "location")],
    "accessibility": [("location_id", "location")],
    "service_at_location": [("service_id", "service"), ("location_id", "location")],
    "phone": [("organization_id", "organization"), ("service_id", "service"), ("location_id", "location"),
              ("service_at_location_id", "service_at_location"), ("contact_id", "contact")],
    "contact": [("organization_id", "organization"), ("service_id", "service"), ("location_id", "location"),
                ("service_at_location_id", "service_at_location")],
    "schedule": [("service_id", "service"), ("location_id", "location"), ("service_at_location_id", "service_at_location")],
    "language": [("service_id", "service"), ("location_id", "location"), ("phone_id", "phone")],
    "service_area": [("service_id", "service"), ("service_at_location_id", "service_at_location")],
    "required_document": [("service_id", "service")],
    "cost_option": [("service_id", "service")],
    "service_capacity": [("service_id", "service")],
    "unit": [],
    "url": [("organization_id", "organization"), ("service_id", "service")],
    "metadata": [],
}


def pk(table: str, row_id: str) -> str:
    return f"{table.upper()}#{row_id}"


def org_of(table: str, row: dict, rows: Rows, _depth: int = 0) -> str | None:
    if table == "organization":
        return row.get("id")
    if table in ("taxonomy", "taxonomy_term"):
        return SHARED_ORG
    if table == "attribute":
        target = row.get("link_entity") or row.get("link_type")
        ref = rows.get(str(target), {}).get(row.get("link_id")) if target else None
        if ref is None:  # link_entity missing: find the linked row anywhere
            for t, tbl in rows.items():
                if row.get("link_id") in tbl:
                    target, ref = t, tbl[row["link_id"]]
                    break
        return org_of(str(target), ref, rows, _depth + 1) if ref and _depth < 5 else SHARED_ORG
    for col, ref_table in _OWNER_PATH.get(table, []):
        ref_id = row.get(col)
        if is_missing(ref_id):
            continue
        ref = rows.get(ref_table, {}).get(ref_id)
        if ref is not None and _depth < 5:
            found = org_of(ref_table, ref, rows, _depth + 1)
            if found:
                return found
    if table == "location" and _depth < 5:  # handoff locations often omit organization_id
        for sal in rows.get("service_at_location", {}).values():
            svc = rows.get("service", {}).get(sal.get("service_id"))
            if sal.get("location_id") == row.get("id") and svc:
                return org_of("service", svc, rows, _depth + 1)
    return None


def zip_of_location(location_id: str, rows: Rows) -> str | None:
    """Physical address ZIP first, then postal (design §6.1 steps 1–2)."""
    best = None
    for addr in rows.get("address", {}).values():
        if addr.get("location_id") != location_id or is_missing(addr.get("postal_code")):
            continue
        z = str(addr["postal_code"]).strip()[:5]
        if addr.get("address_type") == "physical":
            return z
        best = best or z
    return best


def rows_to_items(rows: Rows, *, source: str, actor: str, now: str, versions: dict[str, int] | None = None) -> list[dict]:
    versions = versions or {}
    items = []
    for table, tbl in rows.items():
        for rid, row in tbl.items():
            org_id = org_of(table, row, rows) or SHARED_ORG
            key = pk(table, rid)
            item = {
                "PK": key, "SK": "CURRENT", "entity_type": table,
                "org_pk": org_id if org_id == SHARED_ORG else pk("organization", org_id),
                "entity_sk": key,
                "data": {k: v for k, v in row.items() if not is_missing(v)},
                "source": source, "updated_at": now, "updated_by": actor,
                "version": versions.get(key, 0) + 1,
            }
            z = None
            if table == "location":
                z = zip_of_location(rid, rows)
            elif table == "address" and not is_missing(row.get("postal_code")):
                z = str(row["postal_code"]).strip()[:5]
            if z:
                item["zip_pk"] = f"ZIP#{z}"
            items.append(item)
    return items


def items_to_rows(items: Iterable[dict]) -> Rows:
    rows: Rows = defaultdict(dict)
    for item in items:
        if item.get("SK") != "CURRENT":
            continue
        rows[item["entity_type"]][item["data"]["id"]] = dict(item["data"])
    return rows


def as_dataset(rows: Rows) -> Dataset:
    ds = Dataset()
    for table, tbl in rows.items():
        ds.rows[table] = dict(sorted(tbl.items()))  # deterministic child order in rendered documents
    return ds


def render(table: str, row_id: str, rows: Rows) -> dict | None:
    row = rows.get(table, {}).get(row_id)
    if row is None:
        return None
    ds = as_dataset(rows)
    return nest(table, row, ds, _OMIT, _Index(ds))


def render_service(service_id: str, rows: Rows) -> dict | None:
    return render("service", service_id, rows)


def group_by_organization(rows: Rows) -> dict[str, Rows]:
    """Split a dataset into one row-set per organization (shared rows copied in)."""
    groups: dict[str, Rows] = defaultdict(lambda: defaultdict(dict))
    shared: Rows = defaultdict(dict)
    for table, tbl in rows.items():
        for rid, row in tbl.items():
            org = org_of(table, row, rows)
            if org == SHARED_ORG:
                shared[table][rid] = row
            else:  # None -> orphan group; its services fail validation and are quarantined
                groups[org or ORPHAN][table][rid] = row
    for org_rows in groups.values():
        for table, tbl in shared.items():
            org_rows[table].update(tbl)
    return groups
