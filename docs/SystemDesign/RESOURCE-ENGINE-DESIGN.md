# The Resource Engine — Specialist Verification Platform

Team 20 · Design, endpoints, API, commands and notes · 2026-11-04
Builds on `HSDS-INTAKE-HANDOFF.md` (branch `feature/hsds`). Companion files: `SKILL.md` (verification procedure the agents follow) and `AGENT.md` (agent definitions, prompts, tools, permissions).

---

## 0. TL;DR

One FastAPI server on one EC2 instance runs three pipelines. Everything else is a managed AWS service.

| Pipeline | What it does | Ends in |
|---|---|---|
| **A. Data processing** | Ingest NDP (sample file, later the mock API) and ODRS → keep raw in S3 → map to HSDS 3.2.3 → validate → dedup → store in S3 + DynamoDB → **Verifier Agent** (Bedrock AgentCore) checks each record on the web | Each record is `verified` or carries open **flags** |
| **B. Specialist tasks** | Crawl every open flag → group by **facility (HSDS location)** → bucket by **ZIP code** → prioritized to-do list | Specialist claims a task, calls / checks, applies a fix; change is written with their name |
| **C. Discovery** (the headline feature) | **Discovery Agent** searches the web for facilities that are *not* in the store, builds HSDS records with evidence, and **writes them** (it has write authority, fenced by AgentCore Policy) | New records enter the store; an independent Verifier pass decides whether they publish or go to a specialist |

Key decisions:

1. **HSDS is the exchange format, not the storage format.** We store HSDS objects flattened in DynamoDB (one item per HSDS object, single-table design) and the assembled, validated HSDS document in S3. Anything leaving the system is rebuilt from DynamoDB and validated against the pinned JSON Schema (section 4).
2. **The AI never silently changes existing records.** Verifier writes *findings*. Only people (and the Discovery Agent, for brand-new records only) write HSDS data.
3. **Facilities are the unit of specialist work.** One task per location, holding every flagged service at that location, filed under the location's ZIP.

---

## 1. Users and jobs

| User | Job in this system |
|---|---|
| Resource specialist (staff, volunteers) | Opens their ZIP queue, works tasks the agent could not close online (call, visit, check Facebook), applies fixes |
| Specialist lead | Assigns ZIPs, reviews agent-discovered facilities, watches counts |
| Engineer at One Degree | Reads HSDS from the API; adds sources by writing an adapter |
| Verifier Agent (AgentCore) | Checks stored records against the web, writes findings, flags what it can't settle |
| Discovery Agent (AgentCore) | Finds new facilities online, writes new HSDS records |

---

## 2. Architecture

```
                    ┌──────────────────────── EC2 (one server, Docker Compose) ────────────────────────┐
 NDP sample/mock ──►│ adapters ─► raw S3 ─► mapper ─► validator ─► intake/dedup ─► commit ─► S3 + DDB   │
 ODRS API ─────────►│     (Pipeline A)                       │ quarantine                  │             │
 Spreadsheets* ────►│                                        ▼                             ▼             │
                    │                                   S3 quarantine/           SQS re-verify-jobs      │
                    │                                                                      │             │
                    │  verify-worker ◄─────────────────────────────────────────────────────┘             │
                    │     │ InvokeAgentRuntime                                                          │
                    │     ▼                                                                             │
                    │  ┌─────────── Bedrock AgentCore ───────────┐                                      │
                    │  │ Runtime: verifier-agent, discovery-agent│── Gateway (MCP) ──► tools:            │
                    │  │ Memory · Identity · Observability       │    web_search (managed connector)     │
                    │  │ Policy (Cedar, ENFORCE on Gateway)      │    browser (AgentCore Browser)        │
                    │  └─────────────────────────────────────────┘    fetch_page / check_site (Lambda)   │
                    │                                                 geocode (Amazon Location)          │
                    │                                                 pdf_text (Textract)                │
                    │                                                 /tools/* on THIS server (OpenAPI)  │
                    │                                                                                    │
                    │  flag-crawler (Pipeline B) ─► DDB re-tasks (zip-index) ─► Specialist UI (/ui)      │
                    │  discovery-scheduler (Pipeline C) ─► discovery-agent ─► /tools/records (create)    │
                    │                                                                                    │
                    │  HSDS API (/services, /organizations, …) ─► consumers (bearer token)               │
                    └────────────────────────────────────────────────────────────────────────────────────┘
       EventBridge Scheduler ─► SQS (cron jobs)      Cognito (specialist login)      Secrets Manager (keys)
       CloudWatch Logs/Metrics/Alarms                 OpenSearch (search, Priority 5) KMS (encryption)
```
`*` spreadsheets are the stretch source.

"One server" means one EC2 instance runs the API, the UI, the workers, the crawler and the schedulers' consumers. The agents themselves run on **AgentCore Runtime** (managed), because that is what gives us Gateway, Policy, Memory and Observability. If the team must keep even the agent loop on EC2, run the same Strands agent in-process (`AGENT_MODE=inprocess`) and keep Gateway/Browser/Memory as remote services. Nothing else changes.

---

## 3. AWS services used

| Service | Used for | Notes |
|---|---|---|
| **EC2** | The one server (API, UI, workers) | Amazon Linux 2023, `t3.large`, instance profile, SSM Session Manager (no SSH keys) |
| **S3** | Raw payloads, HSDS documents, evidence, web cache, quarantine, exports | Versioning on `hsds/`, SSE-KMS, Block Public Access |
| **DynamoDB** | HSDS entity store, index, findings, tasks, runs, API keys | On-demand, SSE, PITR on |
| **SQS** | `re-verify-jobs`, `re-jobs` (crawl/ingest/discovery), DLQs | Decouples ingest from slow agent calls |
| **EventBridge Scheduler** | Daily NDP pull, nightly re-check of stale records, crawl every 15 min, weekly discovery | Targets SQS, so the server just consumes messages |
| **Bedrock** | Claude models (Anthropic) | Record the model ID on every finding |
| **Bedrock AgentCore Runtime** | Hosts `verifier-agent` and `discovery-agent` | Serverless, session-isolated |
| **AgentCore Gateway** | One MCP endpoint for all tools; semantic tool search | Enable semantic search **at creation**; it can't be turned on later |
| **AgentCore Policy** | Cedar rules: who may call which tool with which args | Attach in ENFORCE mode; this is how Discovery's write authority is fenced |
| **AgentCore Identity** | Outbound credentials from Gateway to our `/tools/*` API (API key provider) | No secrets in agent code |
| **AgentCore Browser** | JS-heavy sites, Facebook pages, screenshots as evidence | |
| **AgentCore Web Search (Gateway connector)** | Web search without a third-party API key | Announced June 2026 (per an aws-samples PR); **confirm it's enabled in us-west-2 / Workshop Studio** before relying on it. Fallback: Browser + a search page, or a Lambda target |
| **AgentCore Memory** | Per-organization long-term notes ("site blocks bots", "confirmed by phone 2026-10-12") | Supports R2.4 annotations later |
| **AgentCore Observability** | Traces of every agent run, tool calls, tokens | Lands in CloudWatch |
| **AgentCore Evaluations** | Scores the seeded test set (section 13) | Optional |
| **Lambda** | `fetch_page`, `check_site` (DNS/TLS/HTTP), `pdf_text` Gateway targets | Keeps network egress for agents off the EC2 box |
| **Amazon Location Service** | Geocode addresses, validate ZIPs, find places near a ZIP (discovery), maps in the UI | |
| **Textract** | PDFs found on org websites | |
| **Cognito** | Specialist login (user pool + groups `specialist`, `lead`, `admin`) | JWT checked by FastAPI |
| **Secrets Manager** | `ODRS_API_KEY`, NDP mock token, API-key pepper | Never in git, never in logs |
| **KMS** | Keys for S3, DynamoDB, SQS, Gateway | |
| **CloudWatch** | Logs (agent + app), metrics (received/stored/quarantined, flags by category), alarms | |
| **OpenSearch Service** | Keyword + semantic search (Priority 5) | Rebuilt from DynamoDB, droppable |
| **ECR** | Container images for the server and agents | Also what One Degree needs for ECS Fargate later |

---

## 4. Storing HSDS in S3 + DynamoDB

### 4.1 Principle

HSDS governs data when it is *shared*. Internally we keep it in a shape that is easy to query and edit, and we can always round-trip it back to valid HSDS 3.2.3 JSON.

| Store | Holds | Role |
|---|---|---|
| S3 `raw/` | Every payload exactly as received | Immutable evidence (R1.3) |
| DynamoDB `re-hsds` | One item per HSDS object (organization, service, location, address, phone, schedule, …), fields as HSDS names | **Source of truth for current state**; what the editor writes |
| S3 `hsds/` | Assembled nested HSDS service document, validated | What the API serves; versioned for history |
| DynamoDB `team20-resource-engine-index` | One item per service (from the handoff) | Dedup, status, last check |
| DynamoDB `re-findings`, `re-tasks`, `re-runs`, `re-api-keys` | Operational data | Kept apart from HSDS tables (tech doc 3.2.4) |

Every write goes through one function, `commit(entity_changes, actor)`:

1. Apply the change to the in-memory entity set.
2. Assemble the affected service document(s) and validate against the pinned JSON Schema.
3. If invalid → reject (editor gets the error; ingest quarantines).
4. In one `TransactWriteItems`: put the entity items, put HSDS `metadata` items, update the index item.
5. Write the assembled document to `hsds/{source}/{service_id}.json` (S3 versioning keeps history).

### 4.2 S3 layout (bucket `team20-s3-test-bucket`)

```
raw/{source}/{yyyy}/{mm}/{dd}/{run_id}/page-{n}.json      as received, never modified
hsds/{source}/{service_id}.json                           current nested HSDS service (from handoff)
exports/{yyyy-mm-dd}/hsds-services.ndjson                 full export, validated before write
quarantine/{source}/{run_id}/{native_id}.json             payload + mapped output + validation errors
evidence/{service_id}/{finding_id}/{page.html|shot.png}   what the agent saw
webcache/{sha256(url)}.json                               fetched page text + hash + status (skip unchanged pages)
agent-logs/{yyyy-mm-dd}/{session_id}.jsonl                model inputs/outputs (if not using Observability only)
```

### 4.3 DynamoDB table `re-hsds` (single-table HSDS)

| Attribute | Example | Notes |
|---|---|---|
| `PK` | `SERVICE#41b9ce52-…` | `{TYPE}#{uuid}`; TYPE ∈ ORGANIZATION, SERVICE, LOCATION, ADDRESS, PHONE, SCHEDULE, SERVICE_AT_LOCATION, LANGUAGE, ACCESSIBILITY, SERVICE_AREA, REQUIRED_DOCUMENT, URL, TAXONOMY, TAXONOMY_TERM, ATTRIBUTE, CONTACT, COST_OPTION |
| `SK` | `CURRENT` | the object itself |
| `SK` | `META#2026-11-04T09:12:00Z#<metadata_id>` | HSDS `metadata` rows for this object (change history, R2.10) |
| `entity_type` | `service` | |
| `org_pk` (GSI1 PK) | `ORGANIZATION#9a…` | every object carries its organization |
| `entity_sk` (GSI1 SK) | `SERVICE#41b9…` | one Query on GSI1 returns the whole organization graph |
| `zip_pk` (GSI2 PK) | `ZIP#94060` | only on LOCATION and ADDRESS items |
| `parent` | `{"service_id": "…", "location_id": "…"}` | foreign keys as HSDS names |
| `data` | `{ "name": "...", "status": "active", … }` | HSDS fields, null/empty omitted |
| `x_resource_engine` | `{source, last_check, …}` | the one documented extension |
| `source`, `native_id`, `updated_at`, `updated_by`, `version` | | `version` for optimistic locking on edits |

Why single-table: the review UI reads "this facility and everything at it" in one query (GSI1), the task crawler reads "everything in ZIP 94060" in one query (GSI2), and editing a single field touches one small item instead of rewriting a nested blob. Items stay far under the 400 KB limit.

Rebuild rule: `render_service(service_id)` = Query GSI1 for the organization → pick the service, its SALs, locations, addresses, phones, schedules, languages, areas, documents, attributes and taxonomy terms → nest them per the HSDS service schema → validate.

### 4.4 Index table (existing, extended)

Keep `team20-resource-engine-index` exactly as in the handoff, and add:

| New attribute | Purpose |
|---|---|
| `org_id`, `location_ids`, `primary_zip` | lets the crawler and UI work without opening S3 |
| `last_check_at`, `last_check_category`, `last_check_run` | R2.6 |
| `open_flag_count` | quick badge in the UI |
| `publish_state` | `published` \| `held` (held = invalid edit or agent-discovered and not yet confirmed) |
| `verified_by`, `verified_method` | `agent:verifier-v1` / `user:jane@…`; `web` \| `phone` \| `visit` \| `email` |

Extend `unverified_reason` with: `discrepancy`, `actively_bad`, `check_failed`, `needs_manual`, `agent_discovered`, `stale`.

### 4.5 Operational tables

**`re-findings`** — one item per proposed change or problem
`PK=SERVICE#{id}`, `SK=F#{checked_at}#{finding_id}`; GSI `category-status-index` (`cat_status`=`discrepancy#open`, `checked_at`).
Fields: `category`, `field` (HSDS path, e.g. `service_areas[].name`), `current_value`, `proposed_value`, `evidence[] {url, snippet, fetched_at, s3_key}`, `confidence` (high/medium/low), `model_id`, `agent_session_id`, `status` (open, applied, edited, dismissed, superseded), `resolved_by`, `resolved_at`.

**`re-tasks`** — specialist to-do items
`PK=TASK#{task_id}`; GSI `zip-index` (`zip_status`=`ZIP#94060#open`, `sort`=`P1#2026-11-04T…`); GSI `assignee-index`.
Fields: `location_id`, `org_id`, `service_ids[]`, `finding_ids[]`, `categories[]`, `priority` (1–5), `status` (open, claimed, snoozed, done), `assignee`, `lease_until`, `facility` (name, address, lat/long, phones), `what_to_ask[]` (agent-drafted questions), `notes`, `resolution`.
`dedupe_key = LOC#{location_id}#gen{n}` with a conditional put so the crawler never makes two open tasks for one facility.

**`re-runs`** — ingest, verify and discovery runs, with received/stored/quarantined counts and quarantine reasons.

**`re-api-keys`** — `sha256(key+pepper)` → `{owner, scopes, source_filter}` (R3.4).

---

## 5. Pipeline A — data processing

```
fetch ─► raw S3 ─► map to HSDS ─► validate ─┬─► quarantine (reason)          R1.5
                                            └─► identity + dedup ─► commit ─► enqueue verify job
verify-worker ─► Verifier Agent ─► findings ─► index: verified / unverified(reason)
```

1. **Fetch (adapter).** `fetch(since)` yields pages. NDP: sample file now, mock API later (`skip`/`take` paging, `countAvailable`). ODRS: `/v1/opportunities` and `/v1/organizations`, follow `next_page`. Each page goes to `raw/` untouched before anything else.
2. **Map.** `map(payload)` → HSDS objects for one organization (NDP) or one opportunity (ODRS). Ids are `uuid5(RE_ID_NAMESPACE, f"{source}:{type}:{native_id}")`. Mapping tables in sections 11 and 12.
3. **Validate.** Official HSDS 3.2.3 JSON Schema files pinned in `app/hsds/schema/3.2.3/` with `jsonschema`. Failures → `quarantine/` + a row in `re-runs`. Never stored invalid.
4. **Identity + dedup.** Unchanged from the handoff (content hash, fuzzy key, never auto-merge).
5. **Commit.** Section 4.1.
6. **Heuristic pre-flags (no AI, free).** Missing important fields (handoff `completeness.py`), schedule only in free text, no physical address, `assured_date` older than `STALE_DAYS` (default 365), phone numbers that aren't numbers. These become findings with `model_id = "rules"`.
7. **Enqueue.** New or changed services → `re-verify-jobs`. EventBridge also drops nightly jobs for records whose `last_check_at` is older than `RECHECK_DAYS`.
8. **Verify.** The worker calls `InvokeAgentRuntime` for `verifier-agent` with `{service_id}`. The agent follows `SKILL.md`, reads the record through `/tools/records/{id}`, researches with Gateway tools, writes findings through `/tools/findings`, and returns a JSON verdict (`AGENT.md` §2.5). The worker writes `last_check_*` and the verdict to the index.

Verdict → index status:

| Agent verdict | `verification_status` | `unverified_reason` | Creates task? |
|---|---|---|---|
| verified | verified | – | no |
| discrepancy | unverified | discrepancy | yes, P3 |
| actively_bad | unverified | actively_bad | yes, **P1** |
| check_failed | unverified | check_failed | yes, P2 (after 2 consecutive failures) |
| missing_information | unverified | missing_information | yes, P4 |
| needs_manual | unverified | needs_manual | yes, P2 |
| (dedup) possible_duplicate | unverified | possible_duplicate | yes, P5 |

Counts (R1.7) are written per run: `received = stored + quarantined`, per source and per entity type.

---

## 6. Pipeline B — specialist tasks by ZIP

### 6.1 Flag crawler

Runs every 15 minutes (EventBridge → SQS → server) and after each verify batch.

1. Query `re-findings` `category-status-index` for every `*#open` category, plus index items with `verification_status = unverified` and no open task.
2. Resolve each finding to **facilities**: the service's locations via `service_at_location`. Organization-level problems (dead website) attach to every location of that organization.
3. Pick the ZIP for each facility:
   1. physical `address.postal_code` of the location;
   2. else postal address ZIP;
   3. else reverse-geocode the location's lat/long with Amazon Location;
   4. else `COUNTY#{county}` (NDP "Various Locations" rows land here, e.g. `COUNTY#Solano`, `COUNTY#San Francisco`);
   5. virtual locations → `VIRTUAL`.
4. Upsert one task per facility (conditional on `dedupe_key`): merge finding ids, recompute priority = lowest P of its findings, refresh `what_to_ask` (agent-drafted questions copied from findings).
5. Close tasks whose findings were all resolved elsewhere.

### 6.2 What the specialist sees

1. **ZIP board** — list of ZIPs (and county buckets) with open counts by priority, optional map (Amazon Location). Filter by category, source, assignee.
2. **ZIP queue** — tasks sorted P1 → P5, then oldest first.
3. **Task view** — facility header (name, address, phones with click-to-call `tel:` links), then per service: current value | proposed value | evidence (URL, snippet, screenshot) | Apply · Edit · Dismiss. A "What to ask on the call" checklist. A notes box.
4. **Record grid** — spreadsheet-style editor for any field (AG Grid), validation on save (R2.8).

### 6.3 Resolving a task

`POST /tasks/{id}/resolve` with one outcome per finding plus an overall method:

| Outcome | Effect |
|---|---|
| `confirmed` (record was right) | finding dismissed; `assured_date = today`; `verified_method = phone` |
| `apply` / `edit` | `commit()` with the value; HSDS `metadata` row with `updated_by = user email` |
| `closed` | `service.status = defunct` or `temporarily closed`; facility stays for history |
| `unreachable` | task snoozed `SNOOZE_DAYS`, attempt counter +1; after 3 → escalate to lead |

When every finding on the task is resolved, the index goes to `verified` with `verified_by = user:…`, and the note "confirmed by phone" goes into AgentCore Memory for that organization so the next agent run sees it (R2.4).

Lease: `claim` sets `assignee` and `lease_until = now + 2h` so two volunteers don't call the same place.

---

## 7. Pipeline C — discovering new facilities

### 7.1 Flow

1. **Trigger.** Weekly per ZIP/county (EventBridge), or a lead clicks "Find new resources in 94590 — food".
2. **Search.** Discovery Agent runs queries from a category list (food pantry, shelter, legal aid, benefits enrollment, senior meals, clothing, showers…) × place (ZIP, city, county). Sources: web search, Amazon Location places near the ZIP, county and city resource pages, Browser for pages that need JS.
3. **Dedup first.** For each candidate, `/tools/records/match` (name + phone + address + website domain against the fuzzy key and GSI2). If it matches an existing org → it is not new; if the service is new for that org, write a `discrepancy` finding instead ("site lists a service we don't have").
4. **Build HSDS.** Organization, service, location, address, phone, schedule, eligibility, languages, with an evidence list per field.
5. **Evidence bar.** A record may be created only if: the organization's own page (or official government page) supports name, address and what the service is; at least **two independent sources** agree on the address or phone; and nothing suggests it closed.
6. **Write.** `POST /tools/records` (Discovery-only tool). Server validates, dedups, commits with `source = agent-discovery`, `updated_by = agent:discovery-v1`, `x_resource_engine.discovery` = queries + evidence, `publish_state = held`, `unverified_reason = agent_discovered`.
7. **Independent check.** The record is queued for the **Verifier Agent** in a fresh session (different prompt, no shared memory of the discovery). If the verifier returns `verified`, `publish_state = published`. Otherwise a P2 task goes to the specialist ZIP queue with category `new_facility`.

### 7.2 Write authority and its fence

You asked for the Discovery Agent to have write authorization. That is a deliberate exception to R2.9 ("AI proposes, a person applies"), so it is fenced:

| Fence | How |
|---|---|
| Create only, never update or delete | Cedar policy on Gateway: `discovery-agent` may call `create_record`; nobody but the server may call update paths; Verifier has no write tool at all |
| Own namespace | Records are `source = agent-discovery`; Discovery can't touch `ndp`/`odrs` records |
| Rate limit | `DISCOVERY_MAX_CREATES_PER_RUN` (default 25) enforced server-side and in a Gateway interceptor |
| Publish gate | `DISCOVERY_AUTOPUBLISH = after_verifier` (default) \| `immediate` \| `never` |
| Undo | `POST /discovery/candidates/{id}/reject` sets `publish_state = held`, status `inactive`, metadata row; nothing is hard-deleted |
| Kill switch | `DISCOVERY_ENABLED=false`, or detach the policy |
| Audit | every create has evidence in `x_resource_engine.discovery`, a metadata row, and an Observability trace |

Recommendation: confirm with Steffi and Craig that agent-created records may be served before a person sees them. The default above (publish only after an independent verifier pass) is the safe middle.

---

## 8. Agent tools (AgentCore Gateway targets)

| Tool | Target type | Used by | Does |
|---|---|---|---|
| `get_record(service_id)` | OpenAPI → `/tools/records/{id}` | both | HSDS service + open findings + memory notes |
| `match_existing(name, phone, address, domain)` | OpenAPI → `/tools/records/match` | discovery | Candidate duplicates with scores |
| `web_search(query)` | AgentCore managed Web Search connector | both | Results with URL, title, snippet |
| `fetch_page(url)` | Lambda | both | Text, status, final URL, content hash; robots.txt, honest UA, per-site rate limit, S3 cache |
| `check_site(url)` | Lambda | both | DNS, HTTP status chain, TLS expiry date, parked-domain heuristics → "actively bad" signals |
| `browse(url, goal)` | AgentCore Browser | both | JS pages, Facebook, screenshots saved to `evidence/` |
| `geocode(address)` / `places_near(zip, query)` | Lambda → Amazon Location | both | Validate address/ZIP; find places for discovery |
| `pdf_text(url)` | Lambda → Textract | both | Flyers and schedules in PDF |
| `parse_schedule(text)` | OpenAPI → `/tools/schedules/parse` | both | Free text → RFC 5545 rows (Bedrock + deterministic check) |
| `record_finding(...)` | OpenAPI → `/tools/findings` | verifier | Writes to `re-findings` (not HSDS) |
| `flag_for_specialist(service_id, reason, what_to_ask[])` | OpenAPI → `/tools/flags` | verifier | Creates a `needs_manual` finding |
| `create_record(hsds, evidence)` | OpenAPI → `/tools/records` (POST) | **discovery only** | Validated create, held for verification |

Website source component (tech doc 7.3): `fetch_page` reads `WEBSITE_SOURCE=sample|live`. `sample` serves files from `s3://…/samples/websites/{domain}/…` (including seeded failures); `live` makes real requests. Agents don't know which is active.

---

## 9. Server endpoints

Base URL (EC2): `https://<host>`; local: `http://127.0.0.1:8000`. Interactive docs at `/docs`.
Auth: **C** = consumer bearer API key · **S** = specialist Cognito JWT (`lead`/`admin` where noted) · **T** = tool key from AgentCore Identity (Gateway only) · **–** = none.

### 9.1 HSDS API (consumers; HSDS 3.2 API reference)

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/` | – | API info, HSDS version `3.2.3`, links |
| GET | `/services` | C | HSDS Page of services. `page`, `per_page`, `modified_after`, `organization_id`, `taxonomy_term_id`, `taxonomy_id`, `postal_code`, `minimal`, `full`, `search` (P5) |
| GET | `/services/{id}` | C | One service, fully nested |
| GET | `/organizations`, `/organizations/{id}` | C | |
| GET | `/service_at_locations`, `/service_at_locations/{id}` | C | |
| GET | `/taxonomies`, `/taxonomies/{id}`, `/taxonomy_terms`, `/taxonomy_terms/{id}` | C | |
| GET | `/search?q=&postal_code=&mode=keyword\|semantic` | C | OpenSearch (Priority 5) |

Only `publish_state = published` records are served. Keys with `source_filter` see only that source (R3.4).

### 9.2 Ingest and records (internal)

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/ingest/ndp` | S admin | Body `{mode: "sample"\|"mock", s3_key?, url?}`; starts a run |
| POST | `/ingest/ndp/upload` | S admin | Multipart JSON file (the sample) |
| POST | `/ingest/odrs` | S admin | Pull from ODRS API, all pages; `{since?, max_pages?}` |
| GET | `/ingest/runs`, `/ingest/runs/{run_id}` | S | Counts: received / stored / quarantined / skipped / updated, per entity type |
| GET | `/quarantine?source=&run_id=` | S | Quarantined items with errors |
| POST | `/quarantine/{id}/retry` | S admin | Re-map and re-validate (after an adapter fix) |
| POST | `/records`, `/records/batch` | S admin | From the handoff (already-HSDS intake) |
| GET | `/records?status=&reason=&source=&zip=` | S | Index listing for the UI |
| GET | `/records/{service_id}` | S | Stored HSDS + internal fields |
| PATCH | `/records/{service_id}` | S | JSON Patch on HSDS fields; validated; `If-Match: <version>` |
| GET | `/records/{service_id}/history` | S | HSDS metadata rows |
| GET | `/exports/hsds` | S admin | Validated NDJSON export |

### 9.3 Verification and findings

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/verify/{service_id}` | S | Queue one check now |
| POST | `/verify/batch` | S admin | `{source?, zip?, older_than_days?}` |
| GET | `/verify/runs/{run_id}` | S | Progress and verdict counts |
| GET | `/findings?category=&status=&zip=&source=` | S | |
| GET | `/findings/{id}` | S | With evidence links (pre-signed S3 URLs) |
| POST | `/findings/{id}/apply` | S | Apply proposed value |
| POST | `/findings/{id}/edit` | S | `{value}` then apply |
| POST | `/findings/{id}/dismiss` | S | `{reason}` |

### 9.4 Specialist tasks

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/tasks/crawl` | S admin | Run the flag crawler now |
| GET | `/tasks/zips` | S | `[{zip, open, p1, p2, …}]` |
| GET | `/tasks?zip=&status=&priority=&category=&assignee=me` | S | Queue |
| GET | `/tasks/{id}` | S | Facility, services, findings, evidence, what to ask |
| POST | `/tasks/{id}/claim` · `/release` | S | 2-hour lease |
| POST | `/tasks/{id}/resolve` | S | Outcomes per finding (6.3) |
| POST | `/tasks/{id}/snooze` | S | `{days, reason}` |
| POST | `/tasks/{id}/assign` | S lead | `{assignee}` |

### 9.5 Discovery

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/discovery/runs` | S lead | `{zip? , county?, categories[]}` |
| GET | `/discovery/runs/{id}` | S | Queries made, candidates found, created, matched existing |
| GET | `/discovery/candidates?state=held\|published\|rejected` | S | Agent-created records |
| POST | `/discovery/candidates/{id}/publish` · `/reject` | S lead | Manual override |

### 9.6 Agent tool endpoints (Gateway OpenAPI target only)

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/tools/records/{service_id}` | T | `get_record` |
| POST | `/tools/records/match` | T | `match_existing` |
| POST | `/tools/records` | T + policy | `create_record` (discovery) |
| POST | `/tools/findings` | T | `record_finding` |
| POST | `/tools/flags` | T | `flag_for_specialist` |
| POST | `/tools/schedules/parse` | T | `parse_schedule` |

Served from `gateway/openapi-tools.json`. Bound to a separate path prefix so the security group / reverse proxy can restrict it.

### 9.7 Ops and UI

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/health` | – | Liveness |
| GET | `/ready` | – | DynamoDB, S3, SQS reachable |
| GET | `/metrics` | S admin | Counts for the dashboard |
| GET | `/ui/*` | S | Specialist web app |
| GET | `/auth/login`, `/auth/callback` | – | Cognito hosted UI flow |

---

## 10. API examples

### List services (HSDS Page)
```bash
curl -H "Authorization: Bearer $RE_API_KEY" \
  "$RE_HOST/services?postal_code=94060&per_page=2"
```
```json
{
  "total_items": 4, "total_pages": 2, "page_number": 1, "size": 2,
  "first_page": true, "last_page": false, "empty": false,
  "contents": [
    { "id": "…", "name": "Community Development Programs", "status": "active", "organization": { "id": "…", "name": "Puente De La Costa Sur", "description": "…" }, "…": "…" }
  ]
}
```

### Resolve a task
```bash
curl -X POST -H "Authorization: Bearer $JWT" -H "Content-Type: application/json" \
  "$RE_HOST/tasks/$TASK_ID/resolve" -d '{
    "method": "phone",
    "notes": "Spoke with front desk 10:15am",
    "findings": [
      {"finding_id": "f-1", "outcome": "apply"},
      {"finding_id": "f-2", "outcome": "edit", "value": "Mon-Thu 9:00-16:00"},
      {"finding_id": "f-3", "outcome": "confirmed"}
    ]
  }'
```

### Finding written by the Verifier (`POST /tools/findings`)
```json
{
  "service_id": "…",
  "category": "discrepancy",
  "field": "service_areas[].name",
  "current_value": ["San Mateo"],
  "proposed_value": ["San Mateo", "Santa Clara"],
  "evidence": [{"url": "https://law.stanford.edu/community-law-clinic/", "snippet": "<exact sentence from the clinic page naming the counties served>", "fetched_at": "2026-11-04T09:30:11Z"}],
  "confidence": "medium",
  "model_id": "<bedrock model id>",
  "agent_session_id": "…"
}
```
(Illustrative. The NDP record itself is internally inconsistent here — the org description says two counties, the service area says one — which makes it a good first test.)

---

## 11. NDP → HSDS mapping (from the sample)

Sample: `national-data-platform-sample-1.json`, 211 Bay Area, `countAvailable 1068`, `skip 0`, `take 10`. Contents: **10 organizations, 20 services, 13 locations, 21 service-at-locations.**

| NDP | HSDS | Notes |
|---|---|---|
| `data[].id` | organization.id | uuid5 of `ndp:organization:{id}` |
| `name`, `description` | organization.name, description | **strip HTML** (`<p>…</p>`); NDP has org descriptions, unlike ODRS |
| `alternateNames[]` | organization.alternate_name | first value; e.g. Commute.org |
| `url`, `email` | organization.website, email | |
| `yearIncorporated`, `taxId`, `taxStatus`, `legalStatus` | year_incorporated, tax_id, tax_status, legal_status | |
| `phones[]` (org level) | phone with organization_id | |
| `services[].name/description/url/email` | service.* | strip HTML |
| `services[].meta.status` | service.status | `active`/`inactive` |
| `meta.lastVerified`, `meta.lastUpdated` | service.assured_date, last_modified | |
| `applicationProcess` | service.application_process | |
| `fees.description` | service.fees_description | keep line breaks as `; ` |
| `eligibility.description` | service.eligibility_description | |
| `languages.description` ("English, Spanish") | language rows (name + ISO 639-1 code) | `codes[]` is always empty; map names with a lookup |
| `serviceAreas[]` | service_area (name = value, extent_type from `type`) | |
| `documents.description` | required_document rows | split on commas; skip "None" |
| `taxonomy[]` | taxonomy "211 / AIRS" + taxonomy_term (code, name, parent from levels) + attribute link | `targets[]` → extra attribute ("Older Adults") |
| `schedules[].open[]` | schedule rows, `freq=WEEKLY`, `byday`, `opens_at`, `closes_at` | group days with identical hours into one row |
| `schedules[].description` | schedule.description | when `open[]` is empty → `parse_schedule` (Bedrock) → RFC 5545 |
| `locations[]` | location (`location_type=physical`, name, latitude, longitude) | |
| `addresses[] type=physical` | address `address_type=physical` | split "…, Suite 4" into address_2; `country "United States" → "US"` |
| `addresses[] type=mailing` | address `address_type=postal` | |
| `accessibility.description` | accessibility (location_id, description) | |
| `servicesAtLocations[]` | service_at_location | |

Quirks found in the sample and how they're handled (each one is an adapter test):

| # | Record | Quirk | Handling |
|---|---|---|---|
| 1 | Puente · "Zzz_ Holiday Programs" (`211bayarea-10613743`) | No organization, no description, inactive, "Zzz_" prefix | **Quarantine** (`orphan_service`). Expected run result: 20 received, 19 stored, 1 quarantined |
| 2 | Puente SAL `211bayarea-94101833` | All ids null | Quarantine (`invalid_link`); 21 received, 20 stored, 1 quarantined |
| 3 | Puente "Satellite Office", La Honda 94020 | Location with no services | Store under the org; no SAL; it still gets a task bucket if flagged |
| 4 | Commute.org services `10613212`, `87794043` | Phone named "Website" with number `my.commute.org` | Not a phone → HSDS `url` row (`https://my.commute.org`), logged as a mapping fix |
| 5 | Catholic Worker org phone | Name "Toll Free", type null, local number, duplicate of main | Dedup phones by number; type `voice` |
| 6 | Phone `type` values | `main`, `other`, `fax`, null | `main/other/null → voice`, `fax → fax`; `name`/`description` → phone.description |
| 7 | Food Bank "Various Locations" (Solano), SF Food Not Bombs "Various Locations" | Physical address with no city and no ZIP | HSDS address requires both → **don't emit the address**; keep the location (lat/long) and put "Various locations in Solano County" in location.description; task bucket `COUNTY#…`; pre-flag `needs_manual` |
| 8 | Clothes Closet, Senior Food Program, Vegetarian Meals | Hours only as text ("2nd Sunday of the month 1:30pm-3pm", "2nd and 4th Wednesday…", "Wednesday 6:30pm / Friday 6:45pm") | `parse_schedule` → `freq=MONTHLY;byday=2SU`, `byday=2WE,4WE`; times with no close time stay text-only + `missing_information` on hours |
| 9 | Sparrow Project | Schedule "Call for schedule" | No structured hours; pre-flag `missing_information` (hours) |
| 10 | Florence Douglas Senior Center Programs | Friday closes earlier | Two schedule rows (MO–TH 09:00–14:00, FR 09:00–13:00) |
| 11 | Every time | Local Pacific times, no offset | See 14.3 |
| 12 | Catholic Worker "Homeless Shelter" | `lastVerified 2024-12-13` (~23 months old) | Pre-flag `stale` |
| 13 | Stanford CLC | Org says San Mateo + Santa Clara; service area says San Mateo only | Good first verifier test |
| 14 | `input.lastUpdated 1970-01-01` | Full pull | Use as `since` for incremental pulls later |

Physical ZIPs in the sample (the first task buckets): 94020, 94044, 94060, 94063, 94066, 94080, 94520, 94533, 94590, plus `COUNTY#Solano` and `COUNTY#San Francisco`.

---

## 12. ODRS access

Staging host and paths (from Craig):

```bash
export ODRS_HOST=https://rbg.1degree.org:3002
export ODRS_API_KEY="$(aws secretsmanager get-secret-value --secret-id re/odrs-api-key \
  --query SecretString --output text --region us-west-2)"

curl -s "$ODRS_HOST/v1/organizations?api_key=$ODRS_API_KEY"         # list organizations
curl -s "$ODRS_HOST/v1/organizations/123?api_key=$ODRS_API_KEY"     # organization by id
curl -s "$ODRS_HOST/v1/opportunities?api_key=$ODRS_API_KEY"         # list opportunities (paging object, follow next_page)
curl -s "$ODRS_HOST/v1/opportunities/123?api_key=$ODRS_API_KEY"     # opportunity by id
```

Notes:

1. **The key goes in the query string**, so it lands in any URL that gets logged. The adapter redacts `api_key=` from logs, traces and error messages, and never stores request URLs in `raw/` metadata.
2. Store the key only in Secrets Manager (`re/odrs-api-key`). The key was shared in a chat; ask Craig to rotate it after the event.
3. ODRS organizations have no description (tech doc 5.1.1). Our choice: option 1 — the Verifier Agent writes a short description from the org website (One Degree style: 7th-grade level, under four sentences), marked `x_resource_engine.ai_generated = ["organization.description"]`. Until it exists, the organization uses fallback option 3 ("<name>. Description not available.") with a `missing_information` finding, so it can be stored valid and shows up in a task.
4. Strip any credential-bearing attachment URLs before data reaches the repo (tech doc 5.1.3).
5. Mapping table: tech doc section 4 (unchanged).

---

## 13. Evaluation (tech doc 7.4)

1. `samples/websites/` holds fake site content for each sample organization, with seeded changes: area served 1 → 9 counties, an eligibility rule removed, hours changed, a site returning 404, an expired certificate, a parked domain, a JS-only page, a page with only wording changes (must **not** be flagged).
2. `scripts/eval_verifier.py` runs the verifier with `WEBSITE_SOURCE=sample` and reports caught / missed / false flags per category and per field type, plus cost and latency.
3. Optional: register the same set in AgentCore Evaluations for online scoring.
4. Discovery eval: hide 3 sample orgs from the store, run discovery on their ZIPs, check they're found, matched correctly when present, and that no invented facility passes the evidence bar.

---

## 14. Notes and decisions

1. **HSDS version** 3.2.3, schema files pinned from `openreferral/specification` tag `v3.2.3`. Field names in this doc come from documentation pages; the schema files win.
2. **Extensions** (documented list): `x_resource_engine.source`, `.last_check`, `.evidence`, `.ai_generated`, `.discovery`, `.local_times`. Nothing else.
3. **Schedule offsets.** We keep local times plus the IANA zone (`America/Los_Angeles`) in `x_resource_engine.local_times`, and render `opens_at`/`closes_at` with the offset that applies on the date of the response (−08:00 in winter, −07:00 in summer). If the pinned schema has a `timezone` property on schedule, fill it too. Tests cover the March and November DST changes.
4. **service.status from ODRS `unavailable_at`**: date in the past → `inactive`; we never infer `defunct` from source data — only a person or an `actively_bad` finding applied by a person sets `defunct`.
5. **Reconciliation (Priority 4).** Each field's last writer is known from metadata rows (`updated_by` = `source:ndp`, `user:…`, `agent:…`). On re-ingest, if the incoming source value differs from the stored value **and** the stored value's last writer is not that source, write a `clash` finding (P3 task) instead of overwriting.
6. **Search (Priority 5).** Index builder reads DynamoDB, writes OpenSearch; Bedrock Titan or Cohere embeddings for semantic mode; `POST /admin/search/rebuild` drops and recreates.
7. **The UI never shows raw model output**, only findings with evidence. A proposed value without supporting text is dropped or marked low confidence (tech doc 7.2.2).
8. **Cost guards**: web cache by content hash, skip re-checks of unchanged pages, cap tool calls per session (`AGENT.md`), nightly budget alarm.
9. **Region**: us-west-2 for everything. AgentCore features roll out unevenly; check the console for Web Search and Policy in us-west-2 on day one.
10. **ECS later**: the same images run on Fargate; split `api`, `worker`, `crawler` into services when One Degree moves it.

---

## 15. Configuration (env vars)

| Var | Default | Meaning |
|---|---|---|
| `AWS_REGION` | `us-west-2` | |
| `RE_S3_BUCKET` | `team20-s3-test-bucket` | |
| `RE_DDB_INDEX_TABLE` | `team20-resource-engine-index` | handoff's `RE_DDB_TABLE` |
| `RE_DDB_HSDS_TABLE` | `re-hsds` | |
| `RE_DDB_FINDINGS_TABLE` / `RE_DDB_TASKS_TABLE` / `RE_DDB_RUNS_TABLE` / `RE_DDB_KEYS_TABLE` | `re-findings` / `re-tasks` / `re-runs` / `re-api-keys` | |
| `RE_HSDS_VERSION` | `3.2.3` | |
| `RE_ID_NAMESPACE` | fixed UUID | never change |
| `RE_SQS_VERIFY_URL`, `RE_SQS_JOBS_URL` | | |
| `ODRS_HOST` | `https://rbg.1degree.org:3002` | |
| `ODRS_API_KEY_SECRET` | `re/odrs-api-key` | Secrets Manager id (not the key) |
| `NDP_MODE` | `sample` | `sample` \| `mock` |
| `NDP_MOCK_URL`, `NDP_TOKEN_SECRET` | | when Craig's mock is up |
| `WEBSITE_SOURCE` | `sample` | `sample` \| `live` |
| `AGENT_MODE` | `agentcore` | `agentcore` \| `inprocess` |
| `VERIFIER_AGENT_ARN`, `DISCOVERY_AGENT_ARN` | | from `agentcore launch` |
| `GATEWAY_URL` | | AgentCore Gateway MCP URL |
| `BEDROCK_MODEL_ID` | (set on day one) | check enabled models in Workshop Studio |
| `STALE_DAYS` / `RECHECK_DAYS` / `SNOOZE_DAYS` | `365` / `30` / `3` | |
| `DISCOVERY_ENABLED` | `true` | kill switch |
| `DISCOVERY_AUTOPUBLISH` | `after_verifier` | `after_verifier` \| `immediate` \| `never` |
| `DISCOVERY_MAX_CREATES_PER_RUN` | `25` | |
| `COGNITO_USER_POOL_ID`, `COGNITO_CLIENT_ID` | | |

---

## 16. Commands

### 16.1 Local

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt            # fastapi, boto3, jsonschema, strands-agents, bedrock-agentcore, …
pytest                                      # handoff's 18 tests + adapters, render round-trip, counts, crawler

# pin HSDS schemas
git clone --depth 1 --branch v3.2.3 https://github.com/openreferral/specification /tmp/hsds
cp /tmp/hsds/schema/*.json app/hsds/schema/3.2.3/

# run everything locally (api + worker + crawler + ui), LocalStack optional
docker compose up --build
open http://127.0.0.1:8000/docs
```

### 16.2 Get the data in

```bash
# NDP sample (the attached file)
aws s3 cp national-data-platform-sample-1.json s3://$RE_S3_BUCKET/samples/ndp/national-data-platform-sample-1.json
curl -X POST -H "Authorization: Bearer $JWT" -H "Content-Type: application/json" \
  "$RE_HOST/ingest/ndp" -d '{"mode":"sample","s3_key":"samples/ndp/national-data-platform-sample-1.json"}'
curl -H "Authorization: Bearer $JWT" "$RE_HOST/ingest/runs/$RUN_ID"
# expect services: received 20, stored 19, quarantined 1

# ODRS (staging)
curl -X POST -H "Authorization: Bearer $JWT" -H "Content-Type: application/json" \
  "$RE_HOST/ingest/odrs" -d '{"max_pages": 3}'

# CLI equivalents (no HTTP)
python -m app.cli ingest ndp --file national-data-platform-sample-1.json
python -m app.cli ingest odrs --max-pages 3
python -m app.cli verify --source ndp
python -m app.cli crawl
python -m app.cli discover --zip 94590 --categories food,shelter
```

### 16.3 AWS resources (CLI; Terraform in `infra/terraform` mirrors this)

```bash
export AWS_REGION=us-west-2
# tables (JSON specs in infra/dynamodb/)
for t in re-hsds re-findings re-tasks re-runs re-api-keys; do
  aws dynamodb create-table --cli-input-json file://infra/dynamodb/$t.json
done
aws s3api put-bucket-versioning --bucket $RE_S3_BUCKET --versioning-configuration Status=Enabled

# queues
aws sqs create-queue --queue-name re-verify-dlq
aws sqs create-queue --queue-name re-verify-jobs --attributes file://infra/sqs/verify.json   # redrive → DLQ, visibility 900s
aws sqs create-queue --queue-name re-jobs

# schedules → SQS
aws scheduler create-schedule --name re-crawl --schedule-expression "rate(15 minutes)" \
  --flexible-time-window Mode=OFF --target file://infra/scheduler/crawl-target.json
aws scheduler create-schedule --name re-recheck-nightly --schedule-expression "cron(17 9 * * ? *)" \
  --flexible-time-window Mode=OFF --target file://infra/scheduler/recheck-target.json
aws scheduler create-schedule --name re-discovery-weekly --schedule-expression "cron(23 10 ? * MON *)" \
  --flexible-time-window Mode=OFF --target file://infra/scheduler/discovery-target.json

# secrets
aws secretsmanager create-secret --name re/odrs-api-key --secret-string "<paste staging key>"
aws secretsmanager create-secret --name re/tools-api-key --secret-string "$(openssl rand -hex 32)"
```

### 16.4 AgentCore

The AgentCore CLI changed during 2025–2026; check `agentcore --help` for the current verbs.

```bash
pip install bedrock-agentcore bedrock-agentcore-starter-toolkit strands-agents strands-agents-tools

# agents
cd agents/verifier && agentcore configure -e app.py --region us-west-2 && agentcore launch
agentcore invoke '{"service_id": "<uuid>"}'
cd ../discovery && agentcore configure -e app.py --region us-west-2 && agentcore launch
agentcore dev          # local Agent Inspector UI: chat, traces, tool calls

# gateway with semantic search (must be set at creation), JWT inbound, policy engine attached
aws bedrock-agentcore-control create-gateway --name re-gateway \
  --role-arn $GATEWAY_ROLE_ARN --protocol-type MCP \
  --authorizer-type CUSTOM_JWT --authorizer-configuration file://gateway/authorizer.json \
  --protocol-configuration '{"mcp":{"searchType":"SEMANTIC"}}'
# targets: OpenAPI (our /tools/*), Lambda (fetch_page, check_site, geocode, pdf_text), Web Search connector
aws bedrock-agentcore-control create-gateway-target --gateway-identifier $GW_ID \
  --name re-tools --target-configuration file://gateway/targets/openapi-tools.json \
  --credential-provider-configurations file://gateway/targets/tools-api-key.json
# policies: gateway/policies/*.cedar (see AGENT.md §4), attach in ENFORCE mode
```

### 16.5 EC2 deploy

```bash
# one-time: instance with profile re-server-role, SG 443 from your IPs/ALB only, SSM agent
aws ec2 run-instances --image-id resolve:ssm:/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64 \
  --instance-type t3.large --iam-instance-profile Name=re-server-profile \
  --security-group-ids $SG_ID --subnet-id $SUBNET_ID --user-data file://infra/ec2/user-data.sh \
  --metadata-options HttpTokens=required \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=resource-engine}]'

# deploy / update (GitHub Actions does this on push to main)
aws ecr get-login-password | docker login --username AWS --password-stdin $ECR
docker build -t $ECR/resource-engine:$(git rev-parse --short HEAD) backend && docker push $ECR/resource-engine:$(git rev-parse --short HEAD)
aws ssm send-command --instance-ids $INSTANCE_ID --document-name AWS-RunShellScript \
  --parameters commands="cd /opt/re && IMAGE_TAG=$TAG docker compose pull && docker compose up -d"

# logs
aws ssm start-session --target $INSTANCE_ID
docker compose logs -f api worker crawler
```

`infra/ec2/user-data.sh` installs Docker + compose, the CloudWatch agent, Caddy for TLS, pulls `/opt/re/docker-compose.yml` from S3, and starts `api`, `worker`, `crawler`, `ui`.

---

## 17. Repository layout

```
backend/
  app/
    main.py                 FastAPI app, routers
    routers/                hsds_api.py ingest.py records.py findings.py tasks.py discovery.py tools.py auth.py
    adapters/               base.py (contract 5.4), ndp.py, odrs.py, spreadsheet.py
    hsds/                   schema/3.2.3/*.json, validate.py, render.py (DDB ⇄ nested), schedules.py
    intake.py identity.py dedup.py completeness.py     (from handoff)
    store/                  s3.py, dynamo.py, commit.py
    verify/                 worker.py, agent_client.py, rules.py (heuristic pre-flags)
    tasks/                  crawler.py, zip_resolver.py, service.py
    discovery/              service.py
    auth/                   cognito.py, api_keys.py
    cli.py config.py
  tests/
agents/
  verifier/                 app.py, prompt.md
  discovery/                app.py, prompt.md
  skills/hsds-record-verification/SKILL.md
gateway/                    openapi-tools.json, targets/*.json, policies/*.cedar, authorizer.json
lambdas/                    fetch_page/, check_site/, geocode/, pdf_text/
ui/                         React + Vite + AG Grid (built into /ui)
samples/                    ndp/, websites/ (seeded)
infra/                      terraform/, dynamodb/, sqs/, scheduler/, ec2/
docs/                       RESOURCE-ENGINE-DESIGN.md, AGENT.md, SKILL.md
.github/workflows/          ci.yml, deploy.yml
```

---

## 18. Open questions

1. May agent-discovered records be served before a person or second check sees them? (Default: after an independent Verifier pass.)
2. Steffi's final category list — we added `needs_manual`, `stale`, `clash`, `new_facility`.
3. NDP mock URL, auth, and whether it keeps `skip`/`take` paging.
4. Is AgentCore Web Search enabled in the Workshop Studio account? If not, the fallback is Browser-based search.
5. Does One Degree want ZIP buckets or county buckets as the primary queue for volunteers?
