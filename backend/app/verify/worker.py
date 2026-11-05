"""verify-worker: consumes `re-verify-jobs`, calls the Verifier Agent, writes
the verdict to the index (design §5 step 8, verdict table).

Also hosts the `re-jobs` consumer (EventBridge Scheduler -> SQS): crawl,
nightly recheck, discovery, ingest.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone

from ..context import Context
from ..store.commit import now_iso
from .agent_client import AgentOutputError

log = logging.getLogger(__name__)

VERIFIER_ID = "agent:verifier-v1"


def apply_verdict(ctx: Context, service_id: str, out: dict | None, *, run_id: str, mode: str) -> dict:
    item = ctx.stores.index.get(service_id)
    if item is None:
        return {"service_id": service_id, "skipped": "not in index"}
    now = now_iso()
    item.update(last_check_at=now, last_check_run=run_id)
    if out is None:  # AGENT_MODE=off
        item["last_check_category"] = "skipped"
        ctx.stores.index.put(item)
        return {"service_id": service_id, "verdict": None}

    verdict = out["verdict"]
    item["last_check_category"] = verdict
    item["last_check_model"] = out.get("model_id") or ctx.settings.bedrock_model_id
    item["last_check_session"] = out.get("agent_session_id", "")
    if verdict == "check_failed":
        item["consecutive_check_failures"] = int(item.get("consecutive_check_failures", 0)) + 1
    else:
        item["consecutive_check_failures"] = 0

    open_findings = ctx.findings.for_service(service_id, status="open")
    agent_open = [f for f in open_findings if f.get("model_id") != "rules"]
    if verdict == "verified" and not agent_open:
        # confirmed on a current source today -> the "stale" heuristic no longer applies
        for f in open_findings:
            if f["category"] == "stale":
                ctx.findings.set_status(f["finding_id"], "superseded", by=VERIFIER_ID)
        item.update(verification_status="verified", unverified_reason=None, verified_by=VERIFIER_ID,
                    verified_method="web", verified_at=now)
    else:
        if verdict == "check_failed" and item["consecutive_check_failures"] >= 2 and not any(
                f["category"] == "check_failed" for f in open_findings):
            ctx.findings.create(service_id=service_id, category="check_failed", field="website",
                                note="two consecutive failed checks", model_id=item["last_check_model"],
                                confidence="high", what_to_ask=["Is your website still working? What is the address?"])
        reason = verdict if verdict != "verified" else (agent_open[0]["category"] if agent_open else None)
        item.update(verification_status="unverified", unverified_reason=reason)
    item["status_pk"] = item["verification_status"]
    item["open_flag_count"] = len(ctx.findings.for_service(service_id, status="open"))

    if out.get("memory_note") and item.get("org_id"):
        ctx.memory.add(item["org_id"], out["memory_note"], author=VERIFIER_ID)

    if mode == "discovery_check":
        from ..discovery.service import after_independent_check
        after_independent_check(ctx, item, verdict)
    ctx.stores.index.put(item)
    return {"service_id": service_id, "verdict": verdict}


def verify_one(ctx: Context, service_id: str, *, run_id: str = "adhoc", mode: str = "recheck") -> dict:
    payload = {"service_id": service_id, "run_id": run_id, "mode": mode}
    try:
        out = ctx.agent_client.verify(payload)
    except (AgentOutputError, ValueError) as exc:
        log.warning("verifier_bad_output service=%s err=%s", service_id, type(exc).__name__)
        out = {"service_id": service_id, "verdict": "check_failed", "facts_checked": [], "finding_ids": [],
               "memory_note": ""}
    return apply_verdict(ctx, service_id, out, run_id=run_id, mode=mode)


def drain_verify(ctx: Context, max_messages: int = 1000, wait_seconds: int = 0) -> list[dict]:
    """Process queued verify jobs (used by the worker loop, CLI and tests)."""
    done = []
    while len(done) < max_messages:
        batch = ctx.stores.verify_q.receive(10, wait_seconds)
        if not batch:
            break
        for msg in batch:
            body = msg.body
            try:
                done.append(verify_one(ctx, body["service_id"], run_id=body.get("run_id", "queue"),
                                       mode=body.get("mode", "recheck")))
                ctx.stores.verify_q.ack(msg)
            except Exception:  # throttling etc.: SQS redelivers, DLQ after maxReceiveCount
                log.exception("verify_job_failed")
                getattr(ctx.stores.verify_q, "nack", lambda m: None)(msg)
    return done


def enqueue_recheck(ctx: Context, older_than_days: int | None = None, source: str | None = None,
                    zip_code: str | None = None, run_id: str | None = None) -> int:
    days = ctx.settings.recheck_days if older_than_days is None else older_than_days
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    n = 0
    for item in ctx.repo.index_items():
        if source and item.get("source") != source:
            continue
        if zip_code and item.get("primary_zip") != zip_code:
            continue
        if days and (item.get("last_check_at") or "") > cutoff:
            continue
        ctx.stores.verify_q.send({"service_id": item["service_id"], "run_id": run_id or "recheck", "mode": "recheck"})
        n += 1
    return n


def handle_job(ctx: Context, body: dict) -> dict:
    kind = body.get("type")
    if kind == "crawl":
        from ..tasks.crawler import crawl
        return crawl(ctx)
    if kind == "recheck":
        return {"queued": enqueue_recheck(ctx, body.get("older_than_days"))}
    if kind == "discovery":
        from ..discovery.service import execute_run, start_scheduled_runs
        return execute_run(ctx, body["run_id"]) if body.get("run_id") else start_scheduled_runs(ctx)
    if kind == "ingest":
        from .. import adapters
        from ..pipeline import run_ingest
        if body.get("source") == "odrs":
            return run_ingest(ctx, adapters.odrs_api(ctx.settings, body.get("max_pages", 10000)))
        if body.get("s3_key"):
            return run_ingest(ctx, adapters.ndp_s3(ctx.stores.blobs, body["s3_key"]))
        return run_ingest(ctx, adapters.ndp_mock(ctx.settings, body.get("url")))
    raise ValueError(f"unknown job type {kind!r}")


def run_forever(ctx: Context, stop: threading.Event) -> None:
    """Long-poll both queues until stopped (docker compose `worker` service)."""
    while not stop.is_set():
        try:
            drain_verify(ctx, max_messages=10, wait_seconds=5)
            for msg in ctx.stores.jobs_q.receive(5, 5):
                try:
                    handle_job(ctx, msg.body)
                    ctx.stores.jobs_q.ack(msg)
                except Exception:
                    log.exception("job_failed type=%s", msg.body.get("type"))
        except Exception:
            log.exception("worker_loop_error")
            stop.wait(5)
