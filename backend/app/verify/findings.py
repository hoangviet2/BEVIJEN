"""`re-findings`: one item per proposed change or problem (design §4.5).

PK = SERVICE#<service_id>   SK = F#<checked_at>#<finding_id>
GSI category-status-index: cat_status = "<category>#<status>", checked_at
GSI finding-index:         finding_id

Findings never change HSDS data by themselves; applying one goes through
`commit()` with the specialist as the actor (app/tasks/service.py).
"""

from __future__ import annotations

import uuid
from typing import Any

from ..store import Stores
from ..store.commit import now_iso

CATEGORIES = ("actively_bad", "check_failed", "needs_manual", "discrepancy", "missing_information",
              "possible_duplicate", "stale", "clash", "new_facility")
SEVERITY = {c: i for i, c in enumerate(CATEGORIES)}  # lower = more severe
PRIORITY = {"actively_bad": 1, "check_failed": 2, "needs_manual": 2, "new_facility": 2, "discrepancy": 3,
            "clash": 3, "missing_information": 4, "stale": 4, "possible_duplicate": 5}
STATUSES = ("open", "applied", "edited", "dismissed", "superseded")
CONFIDENCE = ("high", "medium", "low")


class FindingError(ValueError):
    pass


class FindingsRepo:
    def __init__(self, stores: Stores):
        self.t = stores.findings

    def create(self, *, service_id: str, category: str, field: str = "", current_value: Any = None,
               proposed_value: Any = None, evidence: list[dict] | None = None, confidence: str = "medium",
               model_id: str = "rules", agent_session_id: str = "", what_to_ask: list[str] | None = None,
               note: str = "", source: str = "") -> dict:
        if category not in CATEGORIES:
            raise FindingError(f"category must be one of {CATEGORIES}")
        if confidence not in CONFIDENCE:
            raise FindingError(f"confidence must be one of {CONFIDENCE}")
        evidence = evidence or []
        is_agent = model_id != "rules"
        # SKILL.md §2.7: no snippet -> no finding; low confidence only as needs_manual
        if is_agent and category in ("discrepancy", "missing_information") and proposed_value not in (None, "", []):
            if not any((e or {}).get("url") and (e or {}).get("snippet") for e in evidence):
                raise FindingError("a proposed value needs at least one evidence item with url and snippet")
        if is_agent and confidence == "low" and category not in ("needs_manual", "check_failed", "actively_bad"):
            raise FindingError("low-confidence findings are only allowed as needs_manual")
        for e in evidence:
            if isinstance(e, dict) and isinstance(e.get("snippet"), str):
                e["snippet"] = e["snippet"][:300]
        # one open finding per (service, category, field): newer supersedes older
        for old in self.for_service(service_id, status="open"):
            if old["category"] == category and old.get("field", "") == field:
                if old.get("model_id") == "rules" and not is_agent:
                    return old  # same heuristic flag already open
                self.set_status(old["finding_id"], "superseded", by=model_id)
        checked_at = now_iso()
        fid = str(uuid.uuid4())
        item = {
            "PK": f"SERVICE#{service_id}", "SK": f"F#{checked_at}#{fid}",
            "finding_id": fid, "service_id": service_id, "category": category, "field": field,
            "current_value": current_value, "proposed_value": proposed_value, "evidence": evidence,
            "confidence": confidence, "model_id": model_id, "agent_session_id": agent_session_id,
            "what_to_ask": what_to_ask or [], "note": note, "source": source,
            "status": "open", "cat_status": f"{category}#open", "checked_at": checked_at,
        }
        self.t.put(item)
        return item

    def get(self, finding_id: str) -> dict | None:
        rows = self.t.query(finding_id, index="finding-index", limit=1)
        return rows[0] if rows else None

    def for_service(self, service_id: str, status: str | None = None) -> list[dict]:
        rows = self.t.query(f"SERVICE#{service_id}", prefix="F#")
        return [r for r in rows if status is None or r["status"] == status]

    def list(self, category: str | None = None, status: str = "open") -> list[dict]:
        cats = [category] if category else list(CATEGORIES)
        out: list[dict] = []
        for c in cats:
            out.extend(self.t.query(f"{c}#{status}", index="category-status-index", descending=True))
        return out

    def set_status(self, finding_id: str, status: str, *, by: str, reason: str = "", value: Any = None) -> dict:
        if status not in STATUSES:
            raise FindingError(f"status must be one of {STATUSES}")
        item = self.get(finding_id)
        if not item:
            raise FindingError("finding not found")
        item.update(status=status, cat_status=f"{item['category']}#{status}", resolved_by=by,
                    resolved_at=now_iso())
        if reason:
            item["resolution_reason"] = reason
        if value is not None:
            item["applied_value"] = value
        self.t.put(item)
        return item

    def open_count(self, service_id: str) -> int:
        return len(self.for_service(service_id, status="open"))


def most_severe(categories: list[str]) -> str | None:
    cats = [c for c in categories if c in SEVERITY]
    return min(cats, key=SEVERITY.__getitem__) if cats else None
