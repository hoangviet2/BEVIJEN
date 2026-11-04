"""Smoke test against real AWS (after ./infra/deploy.sh core secrets).

Checks every table, the bucket and both queues are reachable with the current
credentials, ingests NDP sample 1 through the full pipeline, and reads one
service back from DynamoDB (GSI1 rebuild) and S3. Writes real data under
source "ndp" — run it against a sandbox account or the team bucket only.

    cd backend && STORE_BACKEND=aws RE_SQS_VERIFY_URL=... RE_SQS_JOBS_URL=... python scripts/aws_smoke.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.adapters import ndp_sample  # noqa: E402
from app.context import get_context  # noqa: E402
from app.pipeline import run_ingest  # noqa: E402


def main() -> int:
    ctx = get_context()
    if ctx.settings.is_memory:
        print("STORE_BACKEND=memory: nothing to smoke-test; set STORE_BACKEND=aws", file=sys.stderr)
        return 2
    for name in ("hsds", "index", "findings", "tasks", "runs", "keys"):
        table = getattr(ctx.stores, name)
        table.get("__smoke__", "CURRENT") if table.spec.range_key else table.get("__smoke__")
        print(f"ok  dynamodb {table.name}")
    ctx.stores.blobs.get_bytes("__smoke__")
    print(f"ok  s3 {ctx.stores.blobs.bucket}")
    run = run_ingest(ctx, ndp_sample([ROOT / "ingestion/tests/fixtures/national-data-platform-sample-1.json"]))
    print(f"ok  ingest {run['run_id']} {run['status']} {run['counts']}")
    item = next(iter(ctx.repo.index_items()))
    assert ctx.repo.render_service(item["service_id"]) == ctx.stores.blobs.get_json(item["s3_key"])
    print(f"ok  round trip {item['service_name']!r} (DynamoDB rebuild == S3 document)")
    print(f"    verify jobs queued on {getattr(ctx.stores.verify_q, 'url', 'memory')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
