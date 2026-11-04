# Backend — Resource Engine server

What the backend is, how it is built, and how to run, test and deploy it.
Design source: `docs/SystemDesign/RESOURCE-ENGINE-DESIGN.md` (section numbers below, "§n", refer to it).
The AI part (agents, tools, Gateway, Policy) is documented in `ai/ai.md`.

---

## 1. What it does

One FastAPI server (one EC2 instance, Docker Compose) runs three pipelines. Everything else is a managed AWS service.

| Pipeline | Code | Result |
|---|---|---|
| **A. Data processing** — NDP / ODRS / already-HSDS records → raw S3 → HSDS 3.2.3 → validate → dedup → S3 + DynamoDB → heuristic pre-flags → Verifier Agent | `app/pipeline.py`, `app/adapters/`, `app/hsds/`, `app/store/`, `app/verify/` | each record is `verified` or carries open findings |
| **B. Specialist tasks** — open findings → one task per facility → ZIP / county buckets → claim, call, resolve | `app/tasks/` | fixes written with the specialist's name; HSDS metadata history |
| **C. Discovery** — Discovery Agent finds facilities not in the store and creates held HSDS records; an independent verifier pass publishes them | `app/discovery/` | new records, fenced (create-only, own source, cap, kill switch) |

Rules the code enforces:

1. **One write path.** Every HSDS change goes through `app/store/commit.py::commit()`; it validates every affected service against the pinned HSDS 3.2.3 JSON Schema before writing anything.
2. **HSDS is the exchange format, flat rows are the storage format.** One DynamoDB item per HSDS object; the nested document in S3 is rebuilt from those items and must match.
3. **The AI never silently changes existing records.** The verifier writes findings only; the discovery agent can only create new, held records.
4. **Nothing invalid is stored or served.** Invalid input is quarantined with its errors; every API response object is re-validated before it leaves.

---

## 2. Layout

```
backend/
  app/
    main.py              FastAPI app factory; RUN_WORKERS=true runs the worker thread in-process
    config.py            every env var (§15), one frozen Settings
    context.py           Context: settings + stores + repos + agent client (one per process)
    aws.py               boto3 client factory (credential chain only; LocalStack endpoint override), Secrets Manager reads
    cli.py               python -m app.cli … (ingest, verify, crawl, discover, worker, issue-key, infra-specs, gateway-openapi, search-rebuild)
    models.py            request/response models
    adapters/            source fetchers: NDP sample / S3 / mock API, ODRS API / file, uploads; raw archive layout §4.2
    pipeline.py          Pipeline A: map → normalise → validate → quarantine → dedup → reconcile → commit → pre-flag → enqueue
    intake.py            handoff contract POST /records (already-HSDS) on the same pipeline
    hsds/
      validate.py        official HSDS 3.2.3 JSON Schema (vendored once in transform/hsds_transform/schema/)
      normalize.py       documented source-quirk fixes (§11 table, §12) — each reported as a Fix
      render.py          rows ⇄ DynamoDB items ⇄ nested HSDS documents; organization grouping; ZIP of a location
      schedules.py       free-text hours → RFC 5545 rows; local time → offset for the response date (DST-aware)
    store/
      backend.py         TableSpec (single source for DynamoDB tables + infra JSON), DynamoTable / MemoryTable, S3Blobs / MemoryBlobs
      queue.py           SqsQueue / MemoryQueue
      hsds_repo.py       read side: org graph (GSI1), ZIP (GSI2), by type, history, index
      commit.py          the write path
    verify/
      rules.py           heuristic pre-flags (model_id="rules")
      findings.py        re-findings repo, categories, priorities, evidence rules
      agent_client.py    AGENT_MODE agentcore | inprocess | off; output schema checks
      worker.py          verify worker + scheduled job consumer
      memory.py          per-organization notes: AgentCore Memory (fallback: table)
    tasks/               crawler.py (findings → facility tasks), zip_resolver.py, service.py (claim/resolve/snooze/assign)
    discovery/service.py runs, match_existing, create_record (fenced), publish/reject
    records.py           apply a finding's field / JSON-Patch-lite edits / flatten nested HSDS to rows
    search.py            OpenSearch (keyword + Titan semantic) or keyword scan fallback
    ai.py                server-side Bedrock: parse_schedule fallback, embeddings
    auth.py              consumer API keys, Cognito JWT (groups), Gateway tool key
    routers/             hsds_api.py · internal.py · work.py (tasks + discovery) · tools.py · ops.py
  agents/                verifier + discovery agents (see ai/ai.md)
  lambdas/web_tools/     Gateway Lambda target (fetch_page, check_site, geocode, places_near, pdf_text)
  gateway/               openapi-tools.json (generated), targets/*.json, policies/*.cedar, protocol.json
  infra/                 deploy.sh (AWS CLI), dynamodb/*.json (generated), iam/, sqs/, scheduler/, ec2/, create-local-resources.sh
  ingestion/             raw NDP/ODRS acquisition package (unchanged; used by app/adapters)
  transform/             HSDS mapping package (unchanged; used by the pipeline; holds the pinned schema)
  tests/                 pytest suite (86 tests, no AWS needed; moto for the AWS classes)
  scripts/               aws_smoke.py (real-AWS smoke test), inspect_samples.py
  Dockerfile, docker-compose.yml, requirements.txt, requirements-dev.txt, pytest.ini
```

What changed from the handoff (`feature/hsds`) code:

| Before | Now |
|---|---|
| `hsds_schema.py` — hand-typed required-field subset | `hsds/validate.py` — the official 3.2.3 JSON Schema files with `jsonschema` |
| `storage.py` — one blob per service in S3, one index item | `store/` — single-table HSDS store (`re-hsds`) + S3 document + index; operational tables |
| `intake.py` — its own validate/dedup/store | thin wrapper over `pipeline.process_rows` (one write path) |
| `dev_server.py` — in-memory fake | `STORE_BACKEND=memory` (same fakes the tests use) |
| auto-`verified` when complete | `unverified` until the Verifier Agent (or a person) confirms it |
| `scripts/demo_ingest.py` — hand-written NDP mapping | removed; `python -m app.cli ingest ndp --file …` runs the real mapping |
| `infra/dynamodb-table.json` | `infra/dynamodb/*.json`, generated from `store/backend.py::SPECS` |

---

## 3. Data model (§4)

### S3 (`RE_S3_BUCKET`, versioned, SSE-KMS)

```
raw/{source}/{yyyy}/{mm}/{dd}/{run_id}/page-{n}.json      as received (byte-for-byte), + page-{n}.meta.json (sha256, time)
hsds/{source}/{service_id}.json                           current nested HSDS service, validated
quarantine/{source}/{run_id}/{service_id}.json            mapped rows + validation errors
evidence/{service_id}/…                                   agent screenshots / discovery evidence
webcache/{sha256(url)}.json                               fetched page text (30-day lifecycle)
exports/{yyyy-mm-dd}/hsds-services.ndjson                 validated export
gateway/openapi-tools.json · deploy/.env · deploy/docker-compose.yml
samples/websites/{domain}/{path}.json                     seeded evaluation websites (WEBSITE_SOURCE=sample)
```

### DynamoDB (on-demand, SSE-KMS, PITR)

| Table | Keys | GSIs | Holds |
|---|---|---|---|
| `re-hsds` | `PK={TYPE}#{uuid}`, `SK=CURRENT` or `META#{ts}#{id}` | `org-index` (org_pk, entity_sk), `zip-index` (zip_pk, PK), `type-index` (entity_type, PK) | one item per HSDS object (`data` = HSDS row), HSDS `metadata` change rows, `version` for optimistic locking |
| `team20-resource-engine-index` | `service_id` | `content-hash-index`, `match-key-index`, `status-index` | one item per service: status, reason, publish_state, last_check_*, org/location/ZIP, s3_key |
| `re-findings` | `PK=SERVICE#{id}`, `SK=F#{ts}#{fid}` (+ `ORG#{id}`/`NOTE#…` memory fallback) | `category-status-index`, `finding-index` | proposed changes / problems with evidence |
| `re-tasks` | `PK=TASK#{id}` (+ `DEDUPE#LOC#{location}` pointer) | `zip-index` (zip_status, sort), `assignee-index` | specialist tasks |
| `re-runs` | `PK=RUN#{id}` | `kind-index` | ingest / intake / verify / discovery runs and counts |
| `re-api-keys` | `PK=KEY#{sha256(key+pepper)}` | – | consumer keys (owner, scopes, source_filter) |

Table JSON is generated: `python -m app.cli infra-specs` → `infra/dynamodb/*.json` (a test fails if they drift).

### Index status values

`verification_status` ∈ `verified | unverified`. `unverified_reason` ∈ `possible_duplicate, missing_information, discrepancy, actively_bad, check_failed, needs_manual, agent_discovered, stale, clash, new_facility` (most severe open finding wins). `publish_state` ∈ `published | held`; only `published` is served.

---

## 4. Pipeline A in detail

1. **Fetch + archive** (`adapters/`, `ndp_ingestion`): pages are written to `raw/` before parsing. NDP mock paging, ODRS `next_page` paging, retries, redirect refusal and credential redaction come from `ndp_ingestion`. The ODRS key is read from Secrets Manager (`re/odrs-api-key`) and never logged.
2. **Map** (`hsds_transform`): each source record is mapped under the stable label `{source}:{native_id}` so derived ids never change between runs.
3. **Normalise** (`hsds/normalize.py`) — every fix is counted in the run's `fixes`:

   | Fix code | What | Design ref |
   |---|---|---|
   | `strip_html` (silent) | `<p>…</p>` removed from descriptions | §11 |
   | `taxonomy_description_from_name`, `term_description_from_name` | HSDS requires a description; the term's own name is used | – |
   | `organization_description_missing` | ODRS orgs: "<name>. Description not available." + finding for the verifier to draft one | §12.3 |
   | `status_defaulted_active` | ODRS has no service status | §14.4 |
   | `phone_was_url` | phone "my.commute.org" → HSDS `url` row | quirk 4 |
   | `duplicate_phone_dropped`, phone type map (main/other/null → voice) | | quirks 5–6 |
   | `country_from_us_state`, `address_type_defaulted_physical`, `location_type_derived` | ODRS addresses lack country/type | tech doc §4 |
   | `address_incomplete_dropped` | "Various Locations" (no city/ZIP) — address not emitted, location kept with county in description, `needs_manual` | quirk 7 |
   | `schedule_parsed_from_text` / `schedule_text_only` | "2nd Sunday of the month 1:30pm-3pm" → `MONTHLY;2SU`; start-only times stay text + finding | quirk 8–9 |
   | `invalid_link` | service_at_location pointing nowhere | quirk 2 |

4. **Validate** each nested service (JSON Schema). Invalid → `quarantine/` with errors, removed with its child rows.
5. **Dedup** (`dedup.py`, unchanged rules): same id + same content hash → `skipped_duplicate`; same id → `updated`; same content elsewhere → skip; fuzzy key collision → `possible_duplicate` (flag only, never merged). The content hash is order-insensitive.
6. **Reconcile** (§14.5): if a field was last written by a person or agent, a re-ingest does not overwrite it; a `clash` finding is raised instead.
7. **Commit** (§4.1) → S3 document + index.
8. **Pre-flag** (`verify/rules.py`): missing important fields, hours only as text, no physical address, `assured_date` older than `STALE_DAYS`, short phone numbers, missing org description.
9. **Enqueue** `{service_id, run_id, mode: "new"}` on `re-verify-jobs`.

Counts per run (R1.7): `services_received = services_stored + services_quarantined`, `services_stored` includes `services_skipped_unchanged`. Results on the supplied samples:

| Sample | Orgs | Services received / stored / quarantined | Notable fixes |
|---|---|---|---|
| NDP sample 1 | 10 | 20 / 20 / 0 | 2 phone→url, 2 Various-Locations addresses dropped, 2 text schedules parsed, 2 text-only |
| NDP sample 1 again | 10 | 20 / 20 (all `skipped_unchanged`) / 0 | – |
| ODRS Bayes export | 50 opportunities | 50 / 50 / 0 | 41 org descriptions missing, 50 statuses defaulted |

(The design expected NDP sample 1 to quarantine "Zzz_ Holiday Programs" and a null-id SAL. The transform package already attaches that service to its organization and drops the null-id SAL, so both pass validation; the inactive "Zzz_" service is stored as `inactive`. See open question 6 in `docs/IMPLEMENTATION-NOTES.md`.)

---

## 5. Pipeline B (§6)

- `tasks/crawler.py` (every 15 min via EventBridge, or `POST /tasks/crawl`): open findings → the service's locations (organization-level problems → every location of the org) → one task per facility. A `DEDUPE#LOC#…` item written with a conditional put makes double tasks impossible. Priority = lowest P of its findings (`actively_bad` P1 … `possible_duplicate` P5). Tasks whose findings were resolved elsewhere are closed.
- Bucket (`zip_resolver.py`): physical ZIP → postal ZIP → Amazon Location reverse geocode → `COUNTY#<name>` → `VIRTUAL`. "Various Locations" skip the mailing ZIP and land in `COUNTY#Solano`, `COUNTY#San Francisco`.
- `tasks/service.py`: claim = 2-hour lease; resolve with one outcome per finding: `confirmed` (sets `assured_date`), `apply` / `edit` (commit with `user:<email>`), `closed` (status `defunct` / `temporarily closed`), `dismiss`, `unreachable` (snooze `SNOOZE_DAYS`; after 3 attempts escalated to P1). When all findings on a task are resolved the services become `verified` (`verified_method` = phone/visit/email/web) and "Confirmed by phone …" goes to the organization's memory for the next agent run.

---

## 6. HTTP API (§9)

Interactive docs at `/docs`. Auth: **C** consumer API key · **S** Cognito JWT (group in brackets) · **T** Gateway tool key.

| Area | Endpoints |
|---|---|
| HSDS API (C) | `GET /services` (HSDS Page; `page, per_page, modified_after, organization_id, taxonomy_term_id, taxonomy_id, postal_code, search, minimal`), `/services/{id}`, `/organizations[/{id}]`, `/service_at_locations[/{id}]`, `/taxonomies[/{id}]`, `/taxonomy_terms[/{id}]`, `/search?q=&postal_code=&mode=keyword\|semantic` |
| Ingest (S admin) | `POST /ingest/ndp {mode: sample\|mock, s3_key?, url?}`, `POST /ingest/ndp/upload` (NDP or ODRS file, sniffed), `POST /ingest/odrs {max_pages}`, `GET /ingest/runs[/{id}]`, `GET /quarantine`, `POST /quarantine/{source}/{run_id}/{service_id}/retry` |
| Records (S) | `POST /records`, `/records/batch` (admin), `GET /records?status=&reason=&source=&zip=&publish_state=`, `GET /records/{id}`, `PATCH /records/{id}` (JSON-Patch-lite, `If-Match`), `GET /records/{id}/history`, `GET /exports/hsds` (admin) |
| Verify / findings (S) | `POST /verify/{id}`, `POST /verify/batch` (admin), `GET /verify/runs/{id}`, `GET /findings`, `GET /findings/{id}`, `POST /findings/{id}/apply \| edit \| dismiss` |
| Tasks (S) | `POST /tasks/crawl` (admin), `GET /tasks/zips`, `GET /tasks?zip=&status=&priority=&category=&assignee=me`, `GET /tasks/{id}`, `POST /tasks/{id}/claim \| release \| resolve \| snooze`, `POST /tasks/{id}/assign` (lead) |
| Discovery (S lead) | `POST /discovery/runs`, `GET /discovery/runs/{id}`, `GET /discovery/candidates?state=`, `POST /discovery/candidates/{id}/publish \| reject` |
| Agent tools (T) | `GET /tools/records/{id}`, `POST /tools/records/match`, `POST /tools/records`, `POST /tools/findings`, `POST /tools/flags`, `POST /tools/schedules/parse` |
| Ops | `GET /`, `/health`, `/ready`, `/metrics` (admin), `/auth/login`, `/auth/callback` |

`PATCH /records/{id}` body: `[{"op": "replace", "path": "/service/<id>/eligibility_description", "value": "…"}]` (ops `add | replace | remove` on `/<table>/<row id>[/<field>]`).

---

## 7. AWS services used (all through boto3 / the AWS CLI)

| Service | Where |
|---|---|
| EC2 (AL2023, SSM, IMDSv2) | the server — `infra/deploy.sh server`, `infra/ec2/user-data.sh` |
| ECR | `re-server`, `re-agents` images |
| S3 | raw, HSDS documents, quarantine, evidence, web cache, exports, deploy files |
| DynamoDB | six tables above |
| SQS (+ DLQs, redrive 3) | `re-verify-jobs`, `re-jobs` |
| EventBridge Scheduler | crawl 15 min, nightly recheck, weekly discovery, daily NDP pull |
| Bedrock (Claude, Titan embeddings) | agents; `parse_schedule` fallback; semantic search |
| Bedrock AgentCore Runtime / Gateway / Policy / Identity / Memory / Browser / Web Search / Observability | see `ai/ai.md` |
| Lambda | `re-web-tools` Gateway target |
| Amazon Location Service (`geo-places`) | geocode, reverse geocode for ZIP buckets, places near a ZIP |
| Textract | `pdf_text` |
| Cognito | specialist login, groups specialist / lead / admin |
| Secrets Manager | ODRS key, tools key, API-key pepper, NDP mock token |
| KMS | one CMK for S3, DynamoDB, SQS, Secrets, Lambda env, Gateway, Memory |
| CloudWatch | run metrics (`ResourceEngine` namespace), logs, DLQ / quarantine alarms (SNS) |
| OpenSearch Service | optional search index |
| IAM | least-privilege roles in `infra/iam/` |

---

## 8. Run it

### Local, no AWS

```bash
cd backend
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest                                    # 86 tests (memory stores + moto)

STORE_BACKEND=memory AUTH_MODE=dev AGENT_MODE=off RUN_WORKERS=true uvicorn app.main:app --reload
open http://127.0.0.1:8000/docs
curl -F file=@ingestion/tests/fixtures/national-data-platform-sample-1.json http://127.0.0.1:8000/ingest/ndp/upload
curl -X POST http://127.0.0.1:8000/tasks/crawl && curl http://127.0.0.1:8000/tasks/zips
```

In `AUTH_MODE=dev`, `X-Dev-User` / `X-Dev-Groups` headers stand in for Cognito and the HSDS API needs no key. Never use it outside a laptop.

CLI equivalents (any backend):

```bash
python -m app.cli ingest ndp --file ingestion/tests/fixtures/national-data-platform-sample-1.json
python -m app.cli ingest odrs --file ingestion/tests/fixtures/BayesExport_20250729.json
python -m app.cli ingest odrs --max-pages 3           # ODRS staging, key from Secrets Manager
python -m app.cli verify --source ndp --drain
python -m app.cli crawl
python -m app.cli discover --zip 94590 --categories food,shelter --now
python -m app.cli issue-key --owner one-degree --source ndp
```

### LocalStack

```bash
export RE_AWS_ENDPOINT_URL=http://localhost:4566
./infra/create-local-resources.sh            # prints RE_SQS_*_URL
STORE_BACKEND=aws RE_SQS_VERIFY_URL=… RE_SQS_JOBS_URL=… AUTH_MODE=dev AGENT_MODE=off uvicorn app.main:app
```

### AWS (us-west-2)

```bash
export AWS_REGION=us-west-2 RE_PUBLIC_URL=https://re.example.org SG_ID=sg-… SUBNET_ID=subnet-…
cd backend
./infra/deploy.sh core secrets iam cognito lambda memory gateway agents scheduler server alarms
# or: ./infra/deploy.sh all
POLICY_MODE=ENFORCE ./infra/deploy.sh gateway-enforce     # after reading the LOG_ONLY decisions
./infra/deploy.sh env                                      # the server's environment
STORE_BACKEND=aws … python scripts/aws_smoke.py            # reachability + one ingest round trip
```

`deploy.sh` keeps created ids in `infra/.state.json` (git-ignored). Each step can be re-run.

---

## 9. Configuration

All variables of design §15 plus:

| Var | Default | Meaning |
|---|---|---|
| `STORE_BACKEND` | `aws` | `memory` = in-process fakes (dev/tests) |
| `RE_AWS_ENDPOINT_URL` | – | LocalStack endpoint for S3/DynamoDB/SQS/Secrets |
| `RE_KMS_KEY_ID` | – | SSE-KMS key for S3 writes (empty → SSE-S3) |
| `AUTH_MODE` | `cognito` | `dev` for local only |
| `COGNITO_DOMAIN` | – | hosted-UI domain for `/auth/login` |
| `AGENT_MODE` | `agentcore` | `agentcore` \| `inprocess` \| `off` |
| `AGENTCORE_MEMORY_ID` | – | empty → notes kept in `re-findings` |
| `TOOLS_API_KEY_SECRET` / `TOOLS_API_KEY` | `re/tools-api-key` / – | Gateway → `/tools/*` key (the plain var is for dev only) |
| `API_KEY_PEPPER_SECRET` | `re/api-key-pepper` | consumer key hashing |
| `RE_LOCAL_TZ` | `America/Los_Angeles` | schedule offsets |
| `LEASE_HOURS` | `2` | task claim lease |
| `OPENSEARCH_ENDPOINT`, `OPENSEARCH_INDEX`, `EMBEDDING_MODEL_ID` | –, `re-services`, `amazon.titan-embed-text-v2:0` | search |
| `RE_METRICS_NAMESPACE` | `ResourceEngine` | CloudWatch metrics |
| `RUN_WORKERS` | `false` | run the worker loop inside the API process |

---

## 10. Tests

`backend/tests/` (pytest, no AWS):

| File | Covers |
|---|---|
| `test_pipeline.py` | NDP/ODRS counts (R1.7), raw byte-for-byte archive, every stored doc valid + S3 == DynamoDB rebuild, re-ingest skip, each §11 quirk, clash reconciliation, verify jobs |
| `test_intake.py` | handoff contract (insert/update/skip/possible duplicate/quarantine, batch counts) |
| `test_hsds.py` | schema checks, schedule parsing, model-output checks, DST offsets (March/November), row ⇄ item round trip |
| `test_verify.py` | verdict → index table, bad output → `check_failed`, two failures → P2 finding, memory notes, recheck |
| `test_tasks.py` | one task per facility, idempotent crawl, ZIP + county buckets, priorities, leases, resolve → commit + history + verified + memory, escalation, invalid edits refused |
| `test_discovery.py` | held → published after independent check, failed check → `new_facility`, every fence (verifier, evidence bar, unknown run, duplicate, cap, kill switch), reject, tool endpoint |
| `test_api.py` | HSDS Page shape + validity + offsets, filters, held not served, Cognito/API-key auth, roles, tool key, finding rules, apply, `If-Match`, ops |
| `test_aws_backend.py` | `DynamoTable`/`S3Blobs`/`SqsQueue` on moto through the full pipeline, conditional writes, generated infra/OpenAPI files are current |
| `test_agents.py` | HTML→text, sample websites + actively-bad signals, Lambda dispatch, prompt/skill wiring, Cedar fence, tool budget |

The `ingestion/` and `transform/` packages keep their own `unittest` suites (`python -m unittest discover -s tests` in each).
