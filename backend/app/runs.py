"""`re-runs`: ingest / verify / discovery / crawl runs with their counts (R1.7).

Counts are also published as CloudWatch metrics (namespace RE_METRICS_NAMESPACE)
when running against AWS, so dashboards and alarms see received / stored /
quarantined per source without reading DynamoDB.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from .config import Settings
from .store import Stores
from .store.commit import now_iso

log = logging.getLogger(__name__)


class RunsRepo:
    def __init__(self, stores: Stores, settings: Settings):
        self.t = stores.runs
        self.settings = settings

    def start(self, kind: str, params: dict | None = None, run_id: str | None = None) -> dict:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        rid = run_id or f"{kind[:2]}r-{day}-{uuid.uuid4().hex[:8]}"
        run = {"PK": f"RUN#{rid}", "run_id": rid, "kind": kind, "params": params or {}, "status": "running",
               "started_at": now_iso(), "counts": {}, "errors": []}
        self.t.put(run)
        return run

    def save(self, run: dict) -> dict:
        self.t.put(run)
        return run

    def finish(self, run: dict, status: str = "completed") -> dict:
        run["status"] = status
        run["finished_at"] = now_iso()
        self.t.put(run)
        self._metrics(run)
        return run

    def get(self, run_id: str) -> dict | None:
        return self.t.get(f"RUN#{run_id}")

    def list(self, kind: str, limit: int = 50) -> list[dict]:
        return self.t.query(kind, index="kind-index", descending=True, limit=limit)

    def _metrics(self, run: dict) -> None:
        if self.settings.is_memory:
            return
        counts: dict[str, Any] = run.get("counts") or {}
        source = (run.get("params") or {}).get("source", run["kind"])
        data = [{"MetricName": k, "Value": float(v), "Unit": "Count",
                 "Dimensions": [{"Name": "Kind", "Value": run["kind"]}, {"Name": "Source", "Value": str(source)}]}
                for k, v in counts.items() if isinstance(v, (int, float))]
        if not data:
            return
        try:
            from . import aws
            aws.client("cloudwatch", self.settings).put_metric_data(Namespace=self.settings.metrics_namespace,
                                                                     MetricData=data[:1000])
        except Exception as exc:  # metrics must never fail a run
            log.warning("metrics_failed %s", type(exc).__name__)
