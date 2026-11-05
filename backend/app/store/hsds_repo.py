"""Read side of the single-table HSDS store (`re-hsds`) and the service index."""

from __future__ import annotations

from collections import defaultdict
from typing import Iterator

from hsds_transform.mapper import is_missing

from ..hsds.render import SHARED_ORG, Rows, items_to_rows, pk, render_service
from . import Stores


class HsdsRepo:
    def __init__(self, stores: Stores):
        self.s = stores

    # --- objects -----------------------------------------------------------
    def get(self, table: str, row_id: str) -> dict | None:
        item = self.s.hsds.get(pk(table, row_id), "CURRENT")
        return item["data"] if item else None

    def get_item(self, table: str, row_id: str) -> dict | None:
        return self.s.hsds.get(pk(table, row_id), "CURRENT")

    def org_rows(self, org_id: str) -> Rows:
        """Everything under one organization (one GSI1 query) plus the shared
        taxonomy rows its attributes reference."""
        items = self.s.hsds.query(pk("organization", org_id), index="org-index")
        rows = items_to_rows(items)
        for term_id in {a.get("taxonomy_term_id") for a in rows.get("attribute", {}).values()} - {None}:
            term = self.get_item("taxonomy_term", term_id)
            if term:
                rows["taxonomy_term"][term_id] = term["data"]
                tax_id = term["data"].get("taxonomy_id")
                if tax_id and tax_id not in rows.get("taxonomy", {}):
                    tax = self.get("taxonomy", tax_id)
                    if tax:
                        rows["taxonomy"][tax_id] = tax
        return rows

    def versions_for_org(self, org_id: str) -> dict[str, int]:
        return {i["PK"]: int(i.get("version", 0))
                for i in self.s.hsds.query(pk("organization", org_id), index="org-index") if i["SK"] == "CURRENT"}

    def service_rows(self, service_id: str) -> Rows | None:
        svc = self.get("service", service_id)
        if not svc or is_missing(svc.get("organization_id")):
            return None
        return self.org_rows(svc["organization_id"])

    def render_service(self, service_id: str) -> dict | None:
        rows = self.service_rows(service_id)
        return render_service(service_id, rows) if rows else None

    def list_type(self, table: str) -> list[dict]:
        return [i["data"] for i in self.s.hsds.query(table, index="type-index") if i["SK"] == "CURRENT"]

    def at_zip(self, zip_code: str) -> list[dict]:
        return self.s.hsds.query(f"ZIP#{zip_code}", index="zip-index")

    def history(self, table: str, row_id: str) -> list[dict]:
        return [i["data"] for i in self.s.hsds.query(pk(table, row_id), prefix="META#", descending=True)]

    def service_history(self, service_id: str) -> list[dict]:
        rows = self.service_rows(service_id) or {}
        out = self.history("service", service_id)
        for table in ("phone", "schedule", "language", "service_area", "required_document", "service_at_location"):
            for rid, row in rows.get(table, {}).items():
                if row.get("service_id") == service_id:
                    out.extend(self.history(table, rid))
        return sorted(out, key=lambda m: m.get("last_action_date", ""), reverse=True)

    # --- index -------------------------------------------------------------
    def index_item(self, service_id: str) -> dict | None:
        return self.s.index.get(service_id)

    def index_items(self) -> Iterator[dict]:
        return self.s.index.scan()

    def published_services(self, source_filter: str | None = None) -> list[dict]:
        out = []
        for item in self.s.index.scan():
            if item.get("publish_state", "published") != "published":
                continue
            if source_filter and item.get("source") != source_filter:
                continue
            out.append(item)
        return sorted(out, key=lambda i: (i.get("service_name") or "", i["service_id"]))


def organization_ids_by_zip(repo: HsdsRepo, zip_code: str) -> set[str]:
    return {i["org_pk"].split("#", 1)[1] for i in repo.at_zip(zip_code) if i.get("org_pk") not in (None, SHARED_ORG)}


def group_items(items: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for item in items:
        out[item["entity_type"]].append(item)
    return out
