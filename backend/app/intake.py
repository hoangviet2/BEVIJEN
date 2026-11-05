"""Intake of records that are *already* HSDS (handoff contract, POST /records).

{source, native_id, record} -> validate (pinned HSDS 3.2.3 JSON Schema)
-> deterministic id uuid5(source:native_id) -> flatten to rows -> the shared
pipeline (dedup, commit, pre-flags, verify job). Same rules as adapter ingest,
so there is exactly one write path (app/store/commit.py).
"""

from __future__ import annotations

import copy
from typing import Any

from .context import Context
from .hsds.normalize import normalize
from .hsds.render import as_dataset
from .hsds.validate import validate_service
from .identity import deterministic_id
from .models import IntakeResult, Outcome
from .pipeline import process_rows
from .records import EditError, flatten


class IntakeService:
    def __init__(self, ctx: Context):
        self.ctx = ctx

    def intake_many(self, source: str, records: list[tuple[str, dict[str, Any]]]) -> tuple[dict, list[IntakeResult]]:
        run = self.ctx.runs.start("intake", {"source": source, "records": len(records)})
        results: list[IntakeResult] = []
        for native_id, record in records:
            results.append(self._one(source, native_id, record, run))
        run.pop("counts_c", None)
        run.pop("results", None)
        run["counts"] = {"received": len(results),
                         "quarantined": sum(r.outcome is Outcome.QUARANTINED for r in results)}
        run["counts"]["stored"] = run["counts"]["received"] - run["counts"]["quarantined"]
        self.ctx.runs.finish(run)
        return run, results

    def intake(self, source: str, native_id: str, record: dict[str, Any]) -> IntakeResult:
        return self.intake_many(source, [(native_id, record)])[1][0]

    def _one(self, source: str, native_id: str, record: dict[str, Any], run: dict) -> IntakeResult:
        if not str(native_id or "").strip():
            return IntakeResult(outcome=Outcome.QUARANTINED, errors=["record has no native id"])
        service_id = deterministic_id(source, native_id, self.ctx.settings.id_namespace)
        doc = copy.deepcopy(record)
        doc["id"] = service_id
        check = validate_service(doc)
        if not check.valid:
            return IntakeResult(outcome=Outcome.QUARANTINED, errors=check.errors)
        try:
            ds = as_dataset(flatten(doc, source))
        except EditError as exc:
            return IntakeResult(outcome=Outcome.QUARANTINED, errors=[str(exc)])
        normalize(ds, source)
        run["results"] = []
        process_rows(self.ctx, ds.rows, source, run, actor=f"source:{source}",
                     index_extra={"native_id": str(native_id)})
        res = next((r for r in run["results"] if r["service_id"] == service_id), None)
        if res is None:
            return IntakeResult(outcome=Outcome.QUARANTINED, errors=["service not found after mapping"])
        if res["outcome"] == "quarantined":
            return IntakeResult(outcome=Outcome.QUARANTINED, service_id=service_id, errors=res.get("errors", []))
        item = self.ctx.stores.index.get(service_id) or {}
        return IntakeResult(
            outcome=Outcome(res["outcome"]),
            service_id=service_id,
            verification_status=item.get("verification_status"),
            unverified_reason=item.get("unverified_reason"),
            missing_fields=item.get("missing_fields") or [],
            duplicate_of=res.get("duplicate_of"),
            s3_key=item.get("s3_key"),
        )
