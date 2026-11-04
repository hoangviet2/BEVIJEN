"""Pipeline C — discovering new facilities (design §7, AGENT.md §3).

The Discovery Agent may *create* records, fenced by:
- Cedar policy on the Gateway (only discovery-agent may call create_record),
- this module: DISCOVERY_ENABLED kill switch, per-run create cap, own source
  namespace `agent-discovery`, create-only (an id that exists is refused),
  validation + dedup, publish_state=held until an independent verifier pass.
"""

from __future__ import annotations

import re
import uuid
from urllib.parse import urlsplit

from ..context import Context
from ..hsds.normalize import normalize
from ..hsds.render import as_dataset
from ..identity import _normalize_text
from ..pipeline import after_commit
from ..records import flatten
from ..store.commit import CommitRejected, commit, now_iso

SOURCE = "agent-discovery"
ACTOR = "agent:discovery-v1"
DEFAULT_CATEGORIES = ["food", "shelter", "legal aid", "benefits enrollment", "senior meals", "clothing", "showers"]


class DiscoveryError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# --- runs --------------------------------------------------------------------
def start_run(ctx: Context, *, zip_code: str | None, county: str | None, categories: list[str] | None,
              requested_by: str, enqueue: bool = True) -> dict:
    if not ctx.settings.discovery_enabled:
        raise DiscoveryError("discovery is disabled (DISCOVERY_ENABLED=false)", 409)
    if not zip_code and not county:
        raise DiscoveryError("zip or county is required")
    run = ctx.runs.start("discovery", {"zip": zip_code, "county": county,
                                       "categories": categories or DEFAULT_CATEGORIES,
                                       "requested_by": requested_by,
                                       "max_creates": ctx.settings.discovery_max_creates},
                         run_id=f"dr-{now_iso()[:10]}-{zip_code or re.sub(r'\W+', '', county or '')}-{uuid.uuid4().hex[:4]}")
    run["created"] = []
    ctx.runs.save(run)
    if enqueue:
        ctx.stores.jobs_q.send({"type": "discovery", "run_id": run["run_id"]})
    return run


def execute_run(ctx: Context, run_id: str) -> dict:
    run = ctx.runs.get(run_id)
    if not run:
        raise DiscoveryError("run not found", 404)
    p = run["params"]
    payload = {"run_id": run_id, "zip": p.get("zip"), "county": p.get("county"),
               "categories": p.get("categories"), "max_creates": p.get("max_creates")}
    try:
        summary = ctx.agent_client.discover(payload)
    except Exception as exc:
        run["errors"] = [{"stage": "agent", "code": type(exc).__name__}]
        return ctx.runs.finish(run, "failed")
    run = ctx.runs.get(run_id) or run  # creates happened during the agent call
    run["summary"] = summary
    run["counts"] = {"candidates_seen": (summary or {}).get("candidates_seen", 0),
                     "matched_existing": (summary or {}).get("matched_existing", 0),
                     "created": len(run.get("created", [])),
                     "rejected": len((summary or {}).get("rejected", []))}
    return ctx.runs.finish(run, "completed" if summary is not None else "skipped")


def start_scheduled_runs(ctx: Context) -> dict:
    """Weekly EventBridge job: one run per ZIP/county bucket we have tasks or records for."""
    buckets = {i.get("primary_zip") for i in ctx.repo.index_items() if i.get("primary_zip")}
    runs = [start_run(ctx, zip_code=z, county=None, categories=None, requested_by="schedule")["run_id"]
            for z in sorted(buckets)]
    return {"runs": runs}


# --- duplicate matching ----------------------------------------------------------
def _domain(url: str | None) -> str | None:
    if not url:
        return None
    host = urlsplit(url if "//" in url else f"//{url}").hostname or ""
    return host.removeprefix("www.").lower() or None


def _digits(phone: str | None) -> str:
    return re.sub(r"\D", "", phone or "")[-10:]


def _street(addr: str | None) -> str:
    return _normalize_text(addr or "").split(" ")[0] if addr else ""


def match_existing(ctx: Context, *, name: str | None = None, phone: str | None = None,
                   address: str | None = None, domain: str | None = None, zip_code: str | None = None) -> list[dict]:
    want_domain, want_phone = _domain(domain), _digits(phone)
    want_name = set(_normalize_text(name or "").split())
    want_street = _street(address)
    out = []
    for item in ctx.repo.index_items():
        doc = ctx.stores.blobs.get_json(item["s3_key"]) if item.get("s3_key") else None
        if not doc:
            continue
        org = doc.get("organization") or {}
        domains = {_domain(org.get("website")), _domain(doc.get("url"))} - {None}
        phones = {_digits(p.get("number")) for p in (doc.get("phones") or []) + (org.get("phones") or [])} - {""}
        streets = {_street(a.get("address_1")) for sal in doc.get("service_at_locations") or []
                   for a in (sal.get("location") or {}).get("addresses") or []}
        names = set(_normalize_text(f"{org.get('name', '')} {doc.get('name', '')}").split())
        score, why = 0.0, []
        if want_domain and want_domain in domains:
            score += 0.6; why.append("same website domain")
        if want_phone and want_phone in phones:
            score += 0.3; why.append("same phone")
            if want_street and want_street in streets:
                score += 0.3; why.append("same street number")
        if want_name and names:
            overlap = len(want_name & names) / max(len(want_name), 1)
            if overlap >= 0.6:
                score += 0.3 * overlap; why.append(f"name overlap {overlap:.0%}")
        if zip_code and item.get("primary_zip") == zip_code:
            score += 0.05
        if score >= 0.3:
            out.append({"service_id": item["service_id"], "organization_id": org.get("id"),
                        "name": f"{org.get('name')} — {doc.get('name')}", "score": round(min(score, 1.0), 2),
                        "strength": "strong" if score >= 0.6 else "weak", "why": why})
    return sorted(out, key=lambda m: -m["score"])[:10]


# --- create_record (Discovery-only tool) ----------------------------------------------
def create_record(ctx: Context, *, hsds: dict, evidence: list[dict], run_id: str, caller: str) -> dict:
    s = ctx.settings
    if not s.discovery_enabled:
        raise DiscoveryError("discovery is disabled", 409)
    # Primary fence is the Cedar policy on the Gateway; this is defense in depth:
    # the verifier is refused outright and a create must belong to a running discovery run.
    if caller == "verifier-agent":
        raise DiscoveryError("verifier-agent may not create records", 403)
    if (hsds.get("x_resource_engine") or {}).get("source", SOURCE) != SOURCE:
        raise DiscoveryError(f"records may only be created in source {SOURCE!r}", 403)
    run = ctx.runs.get(run_id)
    if not run or run.get("kind") != "discovery":
        raise DiscoveryError("unknown discovery run_id", 404)
    if run.get("status") != "running":
        raise DiscoveryError("discovery run is not running", 409)
    cap = min(int(run["params"].get("max_creates") or s.discovery_max_creates), s.discovery_max_creates)
    if len(run.get("created", [])) >= cap:
        raise DiscoveryError(f"create cap reached for this run ({cap})", 429)
    if not evidence or not all(e.get("url") and e.get("snippet") for e in evidence):
        raise DiscoveryError("every create needs evidence items with url and snippet")
    if len({_domain(e["url"]) for e in evidence}) < 2:
        raise DiscoveryError("evidence bar: at least two independent sources are required")

    doc = {k: v for k, v in hsds.items() if k != "x_resource_engine"}
    doc["id"] = str(uuid.uuid4())  # create-only: the agent never chooses ids of existing rows
    org = doc.get("organization") or {}
    org_name = org.get("name")
    if not org_name or not doc.get("name"):
        raise DiscoveryError("organization.name and service name are required")
    first_addr = next((a for sal in doc.get("service_at_locations") or []
                       for a in (sal.get("location") or {}).get("addresses") or []), {})
    matches = match_existing(ctx, name=org_name, phone=next((p.get("number") for p in doc.get("phones") or []), None),
                             address=first_addr.get("address_1"), domain=org.get("website") or doc.get("url"),
                             zip_code=first_addr.get("postal_code"))
    if any(m["strength"] == "strong" for m in matches):
        raise DiscoveryError(f"matches an existing record: {matches[0]['service_id']} — flag it instead", 409)

    ds = as_dataset(flatten(_fresh_ids(doc), SOURCE))
    normalize(ds, SOURCE)
    rows = ds.rows
    publish = "published" if s.discovery_autopublish == "immediate" else "held"
    service_id = next(iter(rows.get("service", {})))
    extra = {"source": SOURCE, "publish_state": publish, "discovery": {"run_id": run_id, "evidence": evidence[:40],
                                                                       "weak_matches": matches[:3]}}
    try:
        commit(ctx.stores, s, rows, source=SOURCE, actor=ACTOR, index_updates={service_id: extra})
    except CommitRejected as exc:
        raise DiscoveryError(f"invalid HSDS: {exc}", 422) from None
    ctx.stores.blobs.put_json(f"evidence/{service_id}/discovery.json", {"run_id": run_id, "evidence": evidence})
    after_commit(ctx, service_id, run_id=run_id, mode="discovery_check", enqueue=s.discovery_autopublish != "never")
    item = ctx.stores.index.get(service_id)
    item.update(unverified_reason="agent_discovered", publish_state=publish)
    ctx.stores.index.put(item)
    if s.discovery_autopublish == "never":
        ctx.findings.create(service_id=service_id, category="new_facility", field="*", model_id="rules",
                            confidence="high", note="agent-discovered; DISCOVERY_AUTOPUBLISH=never",
                            what_to_ask=["Is this service open to the public, and are these details right?"])
    run["created"] = run.get("created", []) + [{"service_id": service_id, "name": doc["name"],
                                                "evidence_count": len(evidence)}]
    ctx.runs.save(run)
    return {"service_id": service_id, "publish_state": publish, "weak_matches": matches[:3]}


def _fresh_ids(doc: dict) -> dict:
    """Replace every id in an agent-built document with a new UUID (keeping links)."""
    mapping: dict[str, str] = {}

    def walk(node):
        if isinstance(node, list):
            return [walk(n) for n in node]
        if not isinstance(node, dict):
            return node
        out = {}
        for k, v in node.items():
            if (k == "id" or k.endswith("_id")) and isinstance(v, str) and v:
                out[k] = mapping.setdefault(v, str(uuid.uuid4()))
            else:
                out[k] = walk(v)
        if "id" not in out:
            out["id"] = str(uuid.uuid4())
        return out

    root = walk(doc)
    root["id"] = doc["id"]
    return root


def after_independent_check(ctx: Context, item: dict, verdict: str) -> None:
    """Called by the verify worker for mode=discovery_check (design §7.1 step 7)."""
    if item.get("source") != SOURCE or item.get("publish_state") == "rejected":
        return
    if verdict == "verified" and ctx.settings.discovery_autopublish in ("after_verifier", "immediate"):
        item["publish_state"] = "published"
        return
    if not any(f["category"] == "new_facility" for f in ctx.findings.for_service(item["service_id"], "open")):
        ctx.findings.create(service_id=item["service_id"], category="new_facility", field="*", model_id="rules",
                            confidence="high", note=f"independent verifier verdict: {verdict}",
                            what_to_ask=["Is this service open to the public, and are these details right?"])


def set_publish_state(ctx: Context, service_id: str, state: str, by: str) -> dict:
    item = ctx.stores.index.get(service_id)
    if not item or item.get("source") != SOURCE:
        raise DiscoveryError("not an agent-discovered record", 404)
    if state == "rejected":
        # nothing is hard-deleted: held + inactive + a metadata row (via commit)
        from ..tasks.service import _commit_field
        _commit_field(ctx, service_id, "status", "inactive", f"user:{by}")
        item = ctx.stores.index.get(service_id)
        item.update(publish_state="held", rejected=True, rejected_by=by, rejected_at=now_iso())
    else:
        item.update(publish_state="published", published_by=by, published_at=now_iso())
    ctx.stores.index.put(item)
    return item


def list_candidates(ctx: Context, state: str | None = None) -> list[dict]:
    out = []
    for item in ctx.repo.index_items():
        if item.get("source") != SOURCE:
            continue
        st = "rejected" if item.get("rejected") else item.get("publish_state", "held")
        if state and st != state:
            continue
        out.append({**item, "state": st})
    return out
