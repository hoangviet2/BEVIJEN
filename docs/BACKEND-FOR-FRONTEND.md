# Resource Engine backend: guide for building the frontend

Read this first when you start working on the UI. It covers what the backend does,
how to run it, how sign-in works, every endpoint a UI needs (with real response
examples), and the gotchas. Everything here was checked against the running code
on 2026-11-05.

The deeper docs are only needed if you change the backend:
- `backend/backend.md`: pipelines, storage, configuration
- `ai/ai.md`: AI agents, tools, Gateway
- `docs/SystemDesign/RESOURCE-ENGINE-DESIGN.md`: the design

---

## 1. What the backend is, in one paragraph

The backend is one FastAPI server, written in Python, that stores community-resource
records (food banks, shelters, senior centers…) in the **HSDS 3.2.3** format. It
imports them from two data feeds (NDP and One Degree's ODRS). AI agents then check
each record against the organization's website and write **findings** (problems, with
evidence) instead of editing records. The crawler groups open findings into
**tasks**, one per facility, bucketed by ZIP code. Human **specialists** work these
tasks: they claim one, phone the place, then resolve each finding. A separate
**discovery** agent finds new facilities and creates held records that a lead
publishes or rejects. Consumers (One Degree's apps) read the clean data through
the public **HSDS API** (`/services`, `/organizations`…).

The UI you build is mainly for **specialists, leads and admins**:
- the task queue
- record review
- findings
- discovery candidates
- ingest and admin screens

---

## 2. Where it runs

| | Base URL | Notes |
|---|---|---|
| **Deployed (AWS)** | `https://52-41-36-135.sslip.io` | EC2 + Caddy (Let's Encrypt). Real data: 20 NDP records; ~24k ODRS records loading (re-ingest started 2026-11-05) |
| Interactive API docs | `<base>/docs` (Swagger), `<base>/openapi.json` | Always current: generate client types from `openapi.json` |
| Specialist UI | `<base>/ui/` | the React app in `frontend/`, built into the server image (`UI_DIR=/srv/ui`) |
| **Local** | `http://127.0.0.1:8000` | In-memory stores, no AWS, fake sign-in (see §3) |

### Run the backend locally (recommended for UI work)

```bash
cd backend
python3.12 -m venv .venv && source .venv/bin/activate && pip install -r requirements-dev.txt
STORE_BACKEND=memory AUTH_MODE=dev AGENT_MODE=off RUN_WORKERS=true \
  CORS_ORIGINS=http://localhost:5173 uvicorn app.main:app --reload
# load sample data and build the task queue (in another terminal):
curl -F file=@ingestion/tests/fixtures/national-data-platform-sample-1.json http://127.0.0.1:8000/ingest/ndp/upload
curl -F file=@ingestion/tests/fixtures/BayesExport_20250729.json            http://127.0.0.1:8000/ingest/ndp/upload  # ODRS file; the format is detected automatically
curl -X POST http://127.0.0.1:8000/tasks/crawl
```

- Memory mode loses all data when the server restarts.
- `AGENT_MODE=off` means no AI runs locally, so `POST /verify/{id}` only queues a job.
- `CORS_ORIGINS` is only needed if your dev server runs on another origin. If you build
  `frontend/` (`npm run build`), the backend serves `frontend/dist` at `/ui/` from the same
  origin, so no CORS is needed.

---

## 3. Sign-in and roles

### Roles (Cognito groups)

`specialist` < `lead` < `admin`. A higher group includes every lower one.

| Role | Can do |
|---|---|
| any signed-in user | read records, findings, tasks, runs; claim, resolve, snooze or release tasks; apply, edit or dismiss findings; `PATCH` records; queue a re-verify |
| `lead` | + assign tasks to people, start discovery runs, publish or reject discovered records |
| `admin` | + ingest, upload files, retry quarantine, intake API, exports, `POST /tasks/crawl`, `POST /verify/batch`, `/metrics` |

The UI should hide actions the user's groups don't allow. The server enforces them
anyway, answering 403 `{"detail": "requires group lead"}`.

### Local: dev headers (`AUTH_MODE=dev`)

There's no login locally. Send these headers instead:

```
X-Dev-User: ana@example.org
X-Dev-Groups: specialist          # or lead / admin, comma-separated
```

Without them you're `dev@local` with `admin`. The public HSDS API needs no key in dev
mode.

### Deployed: Cognito Hosted UI

| Setting | Value |
|---|---|
| Region | `us-west-2` |
| User pool | `us-west-2_JyQS3sDGH` |
| App client (public, no secret) | `53jeuan70vp2hsri6c1tmh21nm` |
| Hosted UI domain | `re-364103436311.auth.us-west-2.amazoncognito.com` |

The flow (implemented in `backend/app/routers/ops.py`):

1. Send the browser to **`<base>/auth/login`**. The server redirects to the Cognito login
   page (authorization code flow, with a `state` cookie).
2. Cognito redirects back to `<base>/auth/callback`. The server exchanges the code and
   **redirects the browser to `UI_URL`** (default `/ui/`) with the tokens in the URL
   fragment: `/ui/#id_token=…&access_token=…&expires_in=3600`.
3. The UI reads `location.hash`, stores `id_token` (the existing UI uses
   `localStorage['re_token']`), then clears the hash with `history.replaceState`.
4. Send `Authorization: Bearer <id_token>` on every call. The server checks the
   signature, issuer and client. It takes the user's email and `cognito:groups` from
   the token.
5. Tokens last **1 hour**, and refresh is **not implemented**. On any 401, send the user
   to `/auth/login` again. The groups are in the token: decode its payload (base64 JSON)
   to show the user's name and roles.

- `UI_URL` is an env var on the server. For a separately hosted UI, set it to that UI's
  URL and add the UI origin to `CORS_ORIGINS`. Both go in `/opt/re/.env` via
  `infra/deploy.sh env` / `server`.
- The only registered Cognito callback is `https://52-41-36-135.sslip.io/auth/callback`.
  For a different flow (e.g. PKCE directly from the SPA) you must add callback URLs to
  the app client.
- **Accounts come from sign-up requests.** `POST /auth/signup {email, name, organization, reason}`
  (public, 202) stores a request; an admin approves it in the UI's Team tab
  (`GET /admin/signups`, `POST /admin/signups/{email}/approve {role}` / `reject`), which creates the
  Cognito user and emails a temporary password. `GET /admin/users` and
  `POST /admin/users/{email}/role {role|null}` manage roles; `null` revokes access.
  **A signed-in user with no group gets 403 on every staff endpoint.**
  The first admin is created on the server with `python -m app.cli grant --email … --role admin`,
  or with the AWS CLI:
  ```bash
  aws cognito-idp admin-create-user --region us-west-2 --user-pool-id us-west-2_JyQS3sDGH --username you@example.org
  aws cognito-idp admin-add-user-to-group --region us-west-2 --user-pool-id us-west-2_JyQS3sDGH --username you@example.org --group-name admin
  ```

### Public HSDS API (consumers)

- `/services`, `/organizations`, `/search`… take `Authorization: Bearer re_…`, a consumer
  API key: `python -m app.cli issue-key --owner <name>`. That's not a Cognito token.
- A staff UI normally uses the `/records` endpoints instead.

---

## 4. Concepts and the values you will display

### Record = HSDS service + index item

Each **service** is one HSDS 3.2.3 document: the service, its organization, locations,
addresses, phones, schedules, languages, and taxonomy terms (`attributes`). Next to it,
an **index item** (`GET /records`) holds the workflow state:

| Field | Values / meaning |
|---|---|
| `verification_status` | `verified` \| `unverified` |
| `unverified_reason` | most severe open problem: `actively_bad`, `check_failed`, `discrepancy`, `missing_information`, `needs_manual`, `possible_duplicate`, `clash`, `stale`, `agent_discovered`, `new_facility` |
| `publish_state` | `published` (served to consumers) \| `held` (agent-discovered, waiting for review) |
| `source` | `ndp`, `odrs`, `agent-discovery`, or an intake source |
| `primary_zip`, `org_name`, `service_name`, `service_status` (`active`/`inactive`/`defunct`/`temporarily closed`) | for list views |
| `open_flag_count`, `missing_fields[]` | badge counts |
| `last_check_at`, `verified_by` (`agent:verifier-v1` / `user:<email>`), `verified_method` (`web`/`phone`/`visit`/`email`) | after verification |

### Finding = one problem with evidence

| Field | Meaning |
|---|---|
| `finding_id`, `service_id` | ids |
| `category` | `actively_bad` (P1), `check_failed` (P2), `needs_manual` (P2), `new_facility` (P2), `discrepancy` (P3), `missing_information` (P4), `stale`, `clash`, `possible_duplicate` (P5) |
| `field` | HSDS path, e.g. `schedules`, `eligibility_description`, `locations[].addresses`, `organization.website` |
| `current_value` / `proposed_value` | what the record says vs. what the source says (may be absent) |
| `evidence[]` | `{url, snippet, fetched_at}`. **Always show it**: the UI never shows raw AI output |
| `confidence` | `high` / `medium` / `low` |
| `model_id` | `rules` = heuristic pre-flag (free); a Claude model id = AI agent |
| `what_to_ask[]` | short questions for the phone call |
| `note` | short explanation |
| `status` | `open`, `applied`, `edited`, `dismissed`, `superseded`, `confirmed` |

### Task = one facility to call (groups the open findings of every service at it)

| Field | Meaning |
|---|---|
| `task_id`, `location_id`, `org_id`, `service_ids[]`, `finding_ids[]`, `categories[]` | |
| `zip` | **bucket key**: `ZIP#94044`, `COUNTY#San Francisco` (no street ZIP, e.g. "Various Locations") or `VIRTUAL`. Contains `#`, so **URL-encode it** (`encodeURIComponent`) |
| `priority` | 1 (urgent) … 5 |
| `status` | `open` → `claimed` (by `assignee`, lease until `lease_until`, 2 h) → `resolved`; or `snoozed` (until a date; 3 unreachable attempts escalate to P1); `closed` when its findings were resolved elsewhere |
| `facility` | `{name, organization, address, latitude, longitude, description, phones[]}`, ready to display |
| `what_to_ask[]`, `attempts` | |

---

## 5. Screens → endpoints (suggested flows)

### Specialist: work the queue
1. **ZIP list**: `GET /tasks/zips` → `[{zip, open, p1…p5}]`.
2. **Tasks in a ZIP**: `GET /tasks?zip=<encoded>` (`status` defaults to `open`; also `claimed`,
   `snoozed`, `resolved`; filters `priority`, `category`, `assignee=me`) → array of tasks,
   sorted by priority and then age.
3. **Task detail**: `GET /tasks/{id}` → task + `findings[]` (full) + `services{service_id: HSDS doc}`
   + `memory[]` (notes from earlier checks, e.g. "Confirmed by phone …").
4. **Claim**: `POST /tasks/{id}/claim` (409 if someone else holds it) · **Release**:
   `POST /tasks/{id}/release` · **Snooze**: `POST /tasks/{id}/snooze {days: 1-90, reason}`.
5. **Resolve**: `POST /tasks/{id}/resolve`
   ```json
   {"method": "phone",                       // phone | visit | email | web
    "notes": "spoke to front desk",
    "findings": [
      {"finding_id": "…", "outcome": "confirmed"},                 // record is right
      {"finding_id": "…", "outcome": "apply"},                      // accept proposed_value
      {"finding_id": "…", "outcome": "edit", "value": "Mon–Fri 9–5"}, // your own value
      {"finding_id": "…", "outcome": "dismiss", "reason": "wording only"},
      {"finding_id": "…", "outcome": "closed", "value": "defunct"}, // or "temporarily closed"
      {"finding_id": "…", "outcome": "unreachable"}]}               // snooze + attempt count
   ```
   Returns `{task, results[], open_findings[]}`. When every finding is resolved, the
   services become `verified`.

### Record review (data management)
- **List**: `GET /records?status=&reason=&source=&zip=&publish_state=&limit=500`. Returns index
  items sorted by service name, max 5000.
- **Detail**: `GET /records/{id}` → `{index, hsds, rows, versions, findings}`:
  - `hsds`: the nested HSDS document to display.
  - `rows`: flat rows by table.
  - `versions`: `{ "SERVICE#<id>": 3, … }`, used for `If-Match`.
- **Edit**: `PATCH /records/{id}`, header `If-Match: <versions["SERVICE#<id>"]>`, body JSON-Patch-lite:
  `[{"op": "replace", "path": "/service/<id>/eligibility_description", "value": "…"}]`
  (ops `add|replace|remove` on `/<table>/<row id>[/<field>]`). Results:
  - 412: someone else saved first; reload and retry.
  - 422: the edit would make invalid HSDS; the errors are in `detail`.
- **History**: `GET /records/{id}/history` → HSDS metadata rows (`last_action_date`,
  `last_action_type`, `field_name`, `previous_value`, `replacement_value`, `updated_by`).
- **Findings**: `GET /findings?category=&status=open&zip=&source=` · `GET /findings/{id}` ·
  `POST /findings/{id}/apply` · `POST /findings/{id}/edit {"value": …}` ·
  `POST /findings/{id}/dismiss {"reason": "…"}`.
- **Re-check with AI**: `POST /verify/{id}` → `{"queued": id}`. This is a **paid AI run**
  (~$0.5–2, ~1.5 min) and it runs asynchronously: poll `GET /records/{id}` until
  `index.last_check_at` changes. Ask the user to confirm before starting it.

### Lead
- `POST /tasks/{id}/assign {"assignee": "email"}`.
- **Discovery**: `POST /discovery/runs {"zip": "94590", "categories": ["food", "shelter"]}`
  (or `county`). This is a paid AI run, up to ~20 min. Poll `GET /discovery/runs/{run_id}`.
- **Candidates**: `GET /discovery/candidates?state=held|published|rejected` → index items of
  agent-created records, each with `state` and `evidence`. Then `POST /discovery/candidates/{service_id}/publish` or
  `/reject`.

### Admin
- **Upload a file**: `POST /ingest/ndp/upload`, multipart field `file`. NDP or ODRS JSON
  is detected automatically.
- **Pull from the feeds**: `POST /ingest/ndp {"mode": "sample"|"mock"}` ·
  `POST /ingest/odrs {"max_pages": 3}`.
- **Runs**: `GET /ingest/runs` (`?kind=ingest|intake|verify|discovery`) ·
  `GET /ingest/runs/{run_id}` → counts, `fixes{code: n}`, `errors[]`.
- **Quarantine** (records rejected as invalid HSDS, with the reasons): `GET /quarantine?source=&run_id=` ·
  `POST /quarantine/{source}/{run_id}/{service_id}/retry`.
- `POST /tasks/crawl` (rebuild the task queue; it also runs every 15 min) ·
  `POST /verify/batch` · `GET /exports/hsds` · `GET /metrics` (dashboard numbers, below).

### Public / consumer HSDS API (paged)
- `GET /services` takes `page`, `per_page`, `modified_after`, `organization_id`,
  `taxonomy_term_id`, `taxonomy_id`, `postal_code`, `search`, `minimal`, `full`.
- It returns an HSDS Page: `{total_items, total_pages, page_number, size, first_page, last_page, empty, contents[]}`.
- Also: `/services/{id}`, `/organizations[/{id}]`, `/service_at_locations[/{id}]`,
  `/taxonomies[/{id}]`, `/taxonomy_terms[/{id}]`, and `/search?q=&postal_code=&mode=keyword|semantic`.
- Only `published` records are served.

### Ops (no auth)
`GET /health` → `{"ok": true}` · `GET /ready` → per-store status (503 if one is down) ·
`GET /` → links.

---

## 6. Real responses (trimmed; from the NDP sample)

`GET /tasks/zips`
```json
[{"zip": "COUNTY#San Francisco", "open": 1, "p1": 0, "p2": 1, "p3": 0, "p4": 0, "p5": 0},
 {"zip": "ZIP#94044", "open": 1, "p1": 0, "p2": 0, "p3": 0, "p4": 1, "p5": 0}]
```

`GET /tasks?zip=COUNTY%23San%20Francisco` (one item)
```json
{"task_id": "0fc71bc8-…", "location_id": "6380b286-…", "org_id": "f0c9a71c-…",
 "zip": "COUNTY#San Francisco", "status": "open", "priority": 2, "attempts": 0,
 "facility": {"name": "Various Locations", "organization": "San Francisco Food Not Bombs",
              "address": "PO Box 422, Santa Cruz, CA, 95061", "latitude": 37.77355, "longitude": -122.41193,
              "description": "Various Locations (San Francisco)", "phones": ["(415) 484-3288"]},
 "service_ids": ["1b0ceac4-…"], "finding_ids": ["2763e981-…", "66d1bd15-…", "…"],
 "categories": ["missing_information", "needs_manual", "stale"],
 "what_to_ask": ["What is the street address where people go?", "What are the exact opening and closing times?"],
 "created_at": "2026-11-05T03:03:30Z", "updated_at": "2026-11-05T03:03:30Z"}
```
After claiming, it adds `"status": "claimed"`, `"assignee": "ana@example.org"` and
`"lease_until": "2026-11-05T05:03:30Z"`.
`GET /tasks/{id}` adds `findings[]`, `services{}` (full HSDS docs) and `memory[]`.

A finding
```json
{"finding_id": "2763e981-…", "service_id": "1b0ceac4-…", "category": "missing_information",
 "field": "schedules", "current_value": ["Wednesday 6:30pm\r\nFriday 6:45pm"],
 "confidence": "high", "model_id": "rules", "what_to_ask": ["What are the exact opening and closing times?"],
 "note": "hours only as text", "status": "open", "checked_at": "2026-11-05T03:03:30Z"}
```
An AI finding also has `proposed_value`, `evidence: [{"url", "snippet", "fetched_at"}]`,
`model_id: "us.anthropic.claude-sonnet-4-5-…"` and `agent_session_id`.

`GET /records?limit=…` (one item)
```json
{"service_id": "e785ed25-…", "source": "ndp", "org_id": "2874b41e-…",
 "org_name": "Food Bank Of Contra Costa And Solano (FBCCS)", "service_name": "Agency Partners",
 "service_status": "active", "location_ids": ["beb5e9e5-…", "e7af2b36-…"], "primary_zip": "94520",
 "verification_status": "unverified", "unverified_reason": "stale", "publish_state": "published",
 "open_flag_count": 1, "first_seen_at": "2026-11-05T03:03:30Z", "last_seen_at": "2026-11-05T03:03:30Z",
 "updated_by": "source:ndp", "s3_key": "hsds/ndp/e785ed25-….json"}
```
Ignore `PK`, `SK`, `*_pk`, `hash_pk`, `match_pk`, `content_hash`, `status_pk`, `cat_status`,
`zip_status` and `sort`. They're storage keys and may change.

HSDS service document (abridged; this is what `hsds` / `services{}` hold)
```json
{"id": "1b0ceac4-…", "name": "Vegetarian Meals", "description": "Serves prepared vegetarian meals.",
 "url": "http://www.sffnb.org", "status": "active", "eligibility_description": "Open to all",
 "fees_description": "No fee", "application_process": "…", "assured_date": "2025-07-29",
 "phones": [{"id": "…", "number": "(415) 484-3288", "type": "voice", "description": "Wednesday"}],
 "schedules": [{"id": "…", "description": "Wednesday 6:30pm\r\nFriday 6:45pm"}],
 "service_areas": [{"id": "…", "name": "San Francisco"}], "languages": [{"id": "…", "name": "English"}],
 "service_at_locations": [{"id": "…", "location": {"id": "…", "name": "Various Locations", "location_type": "physical",
     "latitude": 37.77355, "longitude": -122.41193,
     "addresses": [{"address_1": "PO Box 422", "city": "Santa Cruz", "state_province": "CA",
                    "postal_code": "95061", "country": "US", "address_type": "postal"}],
     "accessibility": [{"description": "Not Applicable."}]}}],
 "organization": {"id": "…", "name": "San Francisco Food Not Bombs", "website": "http://www.sffnb.org",
                  "phones": [], "locations": []},
 "attributes": [{"taxonomy_term": {"code": "BD-5000.8300", "name": "Soup Kitchens"}}]}
```
- Structured hours look like `{"freq": "WEEKLY", "byday": "MO", "opens_at": "09:00",
  "closes_at": "17:00"}`, or `"byday": "2SU"` for "2nd Sunday".
- A schedule with only `description` means the hours are free text.
- Field reference: `docs/hsds/objects/*.md`.

`GET /metrics` (admin)
```json
{"services": 20, "by_source": {"ndp": 20}, "by_status": {"unverified": 20},
 "by_reason": {"stale": 15, "missing_information": 2, "needs_manual": 2}, "by_publish_state": {"published": 20},
 "open_findings": {"needs_manual": 2, "missing_information": 6, "stale": 18}, "open_tasks": 11,
 "recent_ingest_runs": [{"run_id": "inr-2026-11-05-048472e9", "status": "completed",
                         "counts": {"services_received": 20, "services_stored": 20}, "started_at": "…"}]}
```

---

## 7. Errors

The body is always `{"detail": <string | object | list>}` (FastAPI).

| Code | When |
|---|---|
| 401 | missing, invalid or expired token: send to `/auth/login` |
| 403 | wrong role (`"requires group lead"`) |
| 404 | unknown id |
| 409 | task claimed by someone else / duplicate discovery candidate / kill switch |
| 412 | `If-Match` version mismatch on `PATCH /records/{id}` |
| 422 | validation: FastAPI's `[{"loc", "msg", "type"}]`, or HSDS schema errors on edits |
| 429 | discovery create cap reached |
| 503 | `/ready`: a store is down |

---

## 8. Gotchas

- **`#` in ZIP buckets**: always `encodeURIComponent(zip)`. Show `ZIP#94044` as "94044",
  `COUNTY#Solano` as "Solano County" and `VIRTUAL` as "Online / phone only".
- **`GET /records` and `GET /findings` scan the whole table** on every call, with no cursor.
  That's fine for hundreds of records, but with ~24k ODRS records it takes seconds. Filter
  server-side (`source`, `zip`, `status`, `reason`), cache on the client, and ask for
  backend paging if it becomes a problem. `/services` (HSDS API) *is* paged.
- **AI actions cost money**: `POST /verify/{id}`, `POST /verify/batch` and
  `POST /discovery/runs` start Bedrock agent runs. Confirm before starting, and never
  call them automatically or in loops.
- **AI runs are asynchronous**: the API returns at once (`queued`). Poll, don't wait.
- **IDs are UUIDs and stable** across re-imports (derived from the source ids).
- **Only `published` records** appear in `/services`. Held discovery records appear in
  `/records?publish_state=held` and `/discovery/candidates`.
- **Times are UTC ISO-8601** (`2026-11-05T03:03:30Z`). Schedule `opens_at`/`closes_at` are
  local times (America/Los_Angeles).
- **Token lifetime is 1 h and there's no refresh**. Handle 401 everywhere.
- **The ODRS data is reloading** (2026-11-05). Counts will grow; the earlier duplicate-address
  bug is fixed.

---

## 9. Changing the backend from the frontend session

- Code: `backend/app/` (routers in `app/routers/`, request models in `app/models.py`).
- Tests: `cd backend && .venv/bin/pytest -q` (95 tests, no AWS needed). Add a test with
  any change.
- Deploy to AWS (needs AWS credentials in `~/.aws`):
  ```bash
  cd backend && ./infra/deploy.sh server
  ```
  This builds the image and restarts the API and worker on EC2 over SSM. Deploy-time
  settings (`UI_URL`, `CORS_ORIGINS`, …) are env vars read by `infra/deploy.sh env`.
- `deploy.sh server` also builds `frontend/` and copies `frontend/dist` into the image, served at
  `/ui/` (same origin, so no CORS or extra Cognito callback URLs). Set `RE_PUBLIC_URL` and
  `WEBSITE_SOURCE=live` when you run it, or the regenerated `.env` changes the live server's settings.
