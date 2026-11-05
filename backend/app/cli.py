"""Command line (design §16.2): the same operations as the HTTP API, no server.

    python -m app.cli ingest ndp --file national-data-platform-sample-1.json
    python -m app.cli ingest ndp --s3-key samples/ndp/national-data-platform-sample-1.json
    python -m app.cli ingest odrs --max-pages 3            # ODRS staging (key from Secrets Manager)
    python -m app.cli ingest odrs --file BayesExport_20250729.json
    python -m app.cli verify --source ndp [--drain]
    python -m app.cli crawl
    python -m app.cli discover --zip 94590 --categories food,shelter [--now]
    python -m app.cli worker                               # long-poll SQS (docker compose `worker`)
    python -m app.cli issue-key --owner one-degree [--source ndp]
    python -m app.cli infra-specs                          # regenerate infra/dynamodb/*.json
    python -m app.cli gateway-openapi                      # regenerate gateway/openapi-tools.json
    python -m app.cli search-rebuild                       # OpenSearch index from DynamoDB
    python -m app.cli kb-load                              # docs/ -> S3 Vectors agent knowledge base
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
from pathlib import Path

from .context import get_context

BACKEND = Path(__file__).resolve().parents[1]


def _to_openapi30(node):
    """FastAPI emits OpenAPI 3.1; the Gateway imports 3.0. Collapse `anyOf: [X, {type: null}]`
    into `X` + `nullable: true` and drop 3.1-only keys."""
    if isinstance(node, list):
        return [_to_openapi30(n) for n in node]
    if not isinstance(node, dict):
        return node
    node = {k: _to_openapi30(v) for k, v in node.items() if k not in ("const", "examples", "contentMediaType")}
    for key in ("anyOf", "oneOf"):
        alts = node.get(key)
        if isinstance(alts, list) and any(a == {"type": "null"} for a in alts):
            rest = [a for a in alts if a != {"type": "null"}]
            node.pop(key)
            if len(rest) == 1:
                node.update(rest[0])
            elif rest:
                node[key] = rest
            node["nullable"] = True
    return node


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m app.cli")
    sub = p.add_subparsers(dest="cmd", required=True)

    ing = sub.add_parser("ingest")
    ing.add_argument("source", choices=["ndp", "odrs"])
    ing.add_argument("--file", nargs="*")
    ing.add_argument("--s3-key")
    ing.add_argument("--url")
    ing.add_argument("--max-pages", type=int, default=10000)
    ing.add_argument("--verify", action="store_true", help="drain the verify queue afterwards")
    ing.add_argument("--no-verify", action="store_true", help="store only; queue no agent verification")

    ver = sub.add_parser("verify")
    ver.add_argument("--source")
    ver.add_argument("--zip")
    ver.add_argument("--older-than-days", type=int, default=0)
    ver.add_argument("--drain", action="store_true", help="process the queue now instead of leaving it to the worker")

    sub.add_parser("crawl")
    disc = sub.add_parser("discover")
    disc.add_argument("--zip")
    disc.add_argument("--county")
    disc.add_argument("--categories", default="")
    disc.add_argument("--now", action="store_true", help="run the agent now instead of queueing")
    sub.add_parser("worker")
    key = sub.add_parser("issue-key")
    key.add_argument("--owner", required=True)
    key.add_argument("--source")
    grant = sub.add_parser("grant", help="create a Cognito user in a group (bootstrap the first admin)")
    grant.add_argument("--email", required=True)
    grant.add_argument("--role", choices=["specialist", "lead", "admin"], default="admin")
    grant.add_argument("--name", default="")
    sub.add_parser("infra-specs")
    sub.add_parser("gateway-openapi")
    sub.add_parser("search-rebuild")
    sub.add_parser("kb-load")

    a = p.parse_args(argv)

    if a.cmd == "infra-specs":
        from .config import get_settings
        from .store import table_names
        from .store.backend import SPECS
        names = table_names(get_settings())
        out = BACKEND / "infra" / "dynamodb"
        out.mkdir(parents=True, exist_ok=True)
        for logical, spec in SPECS.items():
            (out / f"{logical}.json").write_text(json.dumps(spec.create_table_json(names[logical]), indent=2) + "\n")
            print(f"wrote infra/dynamodb/{logical}.json ({names[logical]})")
        return 0

    if a.cmd == "gateway-openapi":
        from .main import app
        spec = app.openapi()
        paths = {k: v for k, v in spec["paths"].items() if k.startswith("/tools/")}
        for op in (op for item in paths.values() for op in item.values()):  # Gateway injects x-api-key itself
            op["parameters"] = [p for p in op.get("parameters", []) if p.get("name") != "x-api-key"]
        doc = {"openapi": "3.0.1", "info": {"title": "Resource Engine agent tools", "version": spec["info"]["version"]},
               "servers": [{"url": "https://RE_HOST"}], "paths": paths,
               "components": {"schemas": spec.get("components", {}).get("schemas", {}),
                              "securitySchemes": {"ToolKey": {"type": "apiKey", "in": "header", "name": "x-api-key"}}},
               "security": [{"ToolKey": []}]}
        (BACKEND / "gateway" / "openapi-tools.json").write_text(json.dumps(_to_openapi30(doc), indent=2) + "\n")
        print(f"wrote gateway/openapi-tools.json ({len(paths)} paths)")
        return 0

    if a.cmd == "kb-load":
        from .config import get_settings
        from .knowledge import load
        _print(load(get_settings()))
        return 0

    ctx = get_context()
    if a.cmd == "ingest":
        from . import adapters
        from .pipeline import run_ingest
        if a.file:
            adapter = adapters.ndp_sample(a.file) if a.source == "ndp" else adapters.odrs_file(a.file)
        elif a.s3_key:
            adapter = adapters.ndp_s3(ctx.stores.blobs, a.s3_key)
        elif a.source == "odrs":
            adapter = adapters.odrs_api(ctx.settings, a.max_pages)
        else:
            adapter = adapters.ndp_mock(ctx.settings, a.url)
        run = run_ingest(ctx, adapter, enqueue=not a.no_verify)
        run.pop("results", None)
        _print(run)
        if a.verify:
            from .verify.worker import drain_verify
            _print(drain_verify(ctx))
        return 0 if run["status"] == "completed" else 1
    if a.cmd == "verify":
        from .verify.worker import drain_verify, enqueue_recheck
        n = enqueue_recheck(ctx, a.older_than_days, a.source, a.zip, "cli")
        print(f"queued {n} verify jobs")
        if a.drain:
            _print(drain_verify(ctx))
        return 0
    if a.cmd == "crawl":
        from .tasks.crawler import crawl
        from .tasks.service import wake_snoozed
        wake_snoozed(ctx)
        _print(crawl(ctx))
        return 0
    if a.cmd == "discover":
        from .discovery.service import execute_run, start_run
        run = start_run(ctx, zip_code=a.zip, county=a.county,
                        categories=[c.strip() for c in a.categories.split(",") if c.strip()] or None,
                        requested_by="cli", enqueue=not a.now)
        _print(execute_run(ctx, run["run_id"]) if a.now else run)
        return 0
    if a.cmd == "worker":
        from .verify.worker import run_forever
        stop = threading.Event()
        try:
            run_forever(ctx, stop)
        except KeyboardInterrupt:
            stop.set()
        return 0
    if a.cmd == "issue-key":
        from .auth import issue_api_key
        print(issue_api_key(ctx, a.owner, source_filter=a.source))
        return 0
    if a.cmd == "grant":
        from .accounts import create_user
        create_user(ctx, a.email, a.name, a.role)
        print(f"{a.email}: {a.role} (Cognito emails a temporary password to new users)")
        return 0
    if a.cmd == "search-rebuild":
        from .search import OpenSearch
        if not ctx.settings.opensearch_endpoint:
            print("OPENSEARCH_ENDPOINT is not set", file=sys.stderr)
            return 2
        _print(OpenSearch(ctx).rebuild())
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
