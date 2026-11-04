# HSDS Intake Service - Team Handoff

Status: working end to end against real AWS (us-west-2). Committed and pushed on
branch `feature/hsds`.

This document is for teammates who need to understand, run, extend, or integrate
with the intake component.

---

## 1. What this component does

It is the **storage + deduplication + verification** stage of the Resource
Engine. It receives data that has **already been converted to HSDS**
(Open Referral Human Services Data Specification v3.2.3), and then:

1. Validates the record against HSDS required fields.
2. Computes a deterministic id and duplicate-detection keys.
3. Checks for duplicates (exact re-ingest and possible cross-source duplicates).
4. Stores the full HSDS document in **Amazon S3** and a lightweight index item
   in **Amazon DynamoDB**.
5. Marks each stored record **verified** or **unverified** (with a reason).

It does NOT convert source data to HSDS. The source adapters (ODRS -> HSDS,
211/NDP -> HSDS, spreadsheets -> HSDS) are a separate, upstream component. This
service sits after them.

```
source adapters (ODRS / NDP / spreadsheets)  ->  [THIS SERVICE]  ->  API / search / review UI
   (convert to HSDS)                              validate, dedup,
                                                  store, mark status
```

---

## 2. The pipeline (what happens to each record)

```
HSDS record (JSON) + source + native_id
  |
  v
1. Validate against HSDS v3.2.3 required fields
     invalid -> QUARANTINED (not stored, reason returned)
  |
  v
2. Derive keys
     - deterministic_id : UUIDv5(source + native_id)   -> stable, enables upsert
     - content_hash     : SHA-256 of record content     -> detect unchanged re-sends
     - fuzzy_match_key  : normalized org name + postal/phone -> possible cross-source dup
  |
  v
3. Classify against DynamoDB
     - same id, same content   -> SKIPPED_DUPLICATE
     - same id, changed content-> UPDATED (upsert)
     - same content, diff id    -> SKIPPED_DUPLICATE
     - fuzzy key collides        -> POSSIBLE_DUPLICATE (flag, never auto-merge)
     - nothing matches           -> NEW
  |
  v
4. Decide verification status
     verified   : not a (possible) duplicate AND no important fields missing
     unverified : otherwise, with unverified_reason =
                    possible_duplicate | missing_information
  |
  v
5. Store
     - S3        : full HSDS JSON  at  hsds/{source}/{service_id}.json
     - DynamoDB  : index item (ids, hashes, status, reason, s3 pointer)
```

---

## 3. Storage design: why both S3 and DynamoDB

| Store | Holds | Why |
|-------|-------|-----|
| **S3** | the full nested HSDS document, one object per record | cheap, no size limit, canonical copy |
| **DynamoDB** | a small index item per record | fast lookups + dedup + status queries |

DynamoDB is poor at storing/scanning large nested blobs; S3 is poor at fast
queries. Splitting them lets each do its job. This also follows the HSDS
guidance to keep operational data (status, provenance, dedup state) separate
from the HSDS records themselves.

### S3 layout (source is in the key)

```
hsds/
  odrs/<service_id>.json     <- all ODRS records
  ndp/<service_id>.json      <- all 211 / NDP records
  <future-source>/...
```

The second path segment is the **source/category**. To get all records from one
source, list with that prefix (e.g. `Prefix="hsds/ndp/"`). No need to open files.

### DynamoDB item shape

| Attribute | Example | Purpose |
|-----------|---------|---------|
| `service_id` (PK) | `41b9ce52-...` | deterministic UUIDv5 |
| `source` | `odrs` | which source it came from |
| `native_id` | `22560` | source's own id |
| `content_hash` / `hash_pk` | `a1b2...` | detect unchanged re-sends (GSI) |
| `fuzzy_match_key` / `match_pk` | `c3d4...` | possible cross-source dup (GSI) |
| `verification_status` / `status_pk` | `verified` or `unverified` | status (GSI) |
| `unverified_reason` | `missing_information` / `possible_duplicate` / null | why unverified |
| `missing_fields` | `["eligibility","schedule",...]` | detail for missing_information |
| `s3_key` | `hsds/odrs/41b9ce52-....json` | pointer to the S3 document |
| `org_name`, `service_name` | ... | human-readable lookup |
| `first_seen_at`, `last_seen_at` | ISO timestamps | provenance |

### GSIs (DynamoDB global secondary indexes)

- `content-hash-index` (hash_pk) - find a record by its content hash
- `match-key-index` (match_pk) - find a possible duplicate by fuzzy key
- `status-index` (status_pk + last_seen_at) - list records by status, e.g. the
  "unverified" review queue

---

## 4. Verification status (important - recently changed)

`verification_status` has **only two values**: `verified` and `unverified`.
The reason a record is unverified lives in a **separate** field.

| Field | Values |
|-------|--------|
| `verification_status` | `verified` \| `unverified` |
| `unverified_reason` | `possible_duplicate` \| `missing_information` \| null |
| `missing_fields` | list of missing important fields (detail) |

Rule for auto-marking:
- **verified**: the record is not a (possible) duplicate AND has all the
  important optional fields.
- **unverified / possible_duplicate**: looks like an existing record.
- **unverified / missing_information**: lacks important optional fields.

"Important optional fields" (what One Degree specialists verify today), defined
in `app/completeness.py`:
- eligibility (`eligibility_description`)
- hours (`schedules`)
- languages (`languages`)
- accessibility (`accessibility`)
- contact (`phones` or `email`)

Note: HSDS *required* fields are enforced separately in step 1 - a record
missing those is quarantined and never stored. "missing_information" is only
about the important *optional* fields above. The exact list is a judgment call
and is trivial to change in one place (`app/completeness.py`).

---

## 5. Duplicate handling (two layers)

1. **Exact re-ingest** (hard guarantee): same `source` + `native_id` always
   yields the same `service_id` (UUIDv5), so re-ingesting updates the record
   instead of duplicating it. Unchanged content is skipped; changed content
   updates.
2. **Cross-source same real-world resource** (conservative flag): a fuzzy key
   built from normalized org name + postal/phone. If it collides with an
   existing record, the new one is stored but flagged `possible_duplicate` for a
   human. It is **never auto-merged**. This is the "org deduplication" the
   project docs call a stretch goal.

---

## 6. AWS resources (already provisioned, us-west-2)

| Resource | Name | Notes |
|----------|------|-------|
| S3 bucket | `team20-s3-test-bucket` | created by the team; records under `hsds/` |
| DynamoDB table | `team20-resource-engine-index` | PAY_PER_REQUEST, 3 GSIs, SSE on |
| Region | `us-west-2` | select Oregon in the console or pages look empty |

Console links (make sure region = us-west-2):
- S3: https://us-west-2.console.aws.amazon.com/s3/buckets/team20-s3-test-bucket?region=us-west-2&prefix=hsds/
- DynamoDB: https://us-west-2.console.aws.amazon.com/dynamodbv2/home?region=us-west-2#tables
  (click the table -> "Explore table items")

The table schema lives in `backend/infra/dynamodb-table.json`. To recreate:

```bash
aws dynamodb create-table \
  --cli-input-json file://backend/infra/dynamodb-table.json \
  --table-name team20-resource-engine-index \
  --region us-west-2
```

`backend/infra/create-local-resources.sh` creates both the bucket and table
(reads `RE_S3_BUCKET` / `RE_DDB_TABLE`), and supports LocalStack via
`RE_AWS_ENDPOINT_URL`.

---

## 7. Configuration (environment variables)

All in `app/config.py`, overridable by env var:

| Env var | Default | Meaning |
|---------|---------|---------|
| `RE_S3_BUCKET` | `team20-s3-test-bucket` | S3 bucket for documents |
| `RE_DDB_TABLE` | `team20-resource-engine-index` | DynamoDB index table |
| `RE_S3_PREFIX` | `hsds` | top-level key prefix |
| `AWS_REGION` | `us-west-2` | region |
| `RE_HSDS_VERSION` | `3.2.3` | pinned HSDS version |
| `RE_AWS_ENDPOINT_URL` | (empty) | set for LocalStack/moto |
| `RE_ID_NAMESPACE` | (fixed UUID) | UUIDv5 namespace - do NOT change, it would change every id |

---

## 8. HTTP API

Run: `uvicorn app.main:app` then open `/docs` for interactive Swagger UI.

| Endpoint | Purpose |
|----------|---------|
| `GET /` | service info + pinned HSDS version |
| `POST /records` | intake one HSDS record; body = `{source, native_id, record}` |
| `POST /records/batch` | intake a list; returns received/stored/quarantined counts |
| `GET /records/{service_id}` | fetch the stored HSDS document from S3 |

`POST /records` response example (a complete, unique record):

```json
{
  "outcome": "inserted",
  "service_id": "41b9ce52-1622-572e-98e3-cbc55c7ed134",
  "verification_status": "verified",
  "unverified_reason": null,
  "missing_fields": [],
  "s3_key": "hsds/odrs/41b9ce52-1622-572e-98e3-cbc55c7ed134.json"
}
```

Outcomes: `inserted`, `updated`, `skipped_duplicate`, `possible_duplicate`,
`quarantined`.

---

## 9. How to run and test

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### a) Unit tests (no AWS)
```bash
pytest           # 18 tests: validation, identity, dedup, completeness, intake
```

### b) Live API, mocked storage (no AWS)
```bash
python dev_server.py
# open http://127.0.0.1:8000/docs
```

### c) Demo with the real sample data (no AWS)
```bash
python scripts/demo_ingest.py
# maps the ai/resources sample files to HSDS (demo mapping) and runs them
# through the pipeline; prints outcome + status breakdown
```

### d) End-to-end against real AWS
```bash
export RE_S3_BUCKET=team20-s3-test-bucket
export RE_DDB_TABLE=team20-resource-engine-index
export AWS_REGION=us-west-2
python scripts/aws_smoke.py        # pushes records, reads them back from S3 + DynamoDB
# or run the live API:
uvicorn app.main:app --reload
```

Needs AWS credentials in the environment (the Workshop Studio role works).

---

## 10. Code map

```
backend/
  app/
    main.py          FastAPI app + endpoints
    intake.py        orchestration: validate -> dedup -> store (core logic)
    hsds_schema.py   HSDS v3.2.3 required-field validation
    identity.py      deterministic id, content hash, fuzzy match key
    dedup.py         duplicate classification
    completeness.py  "important fields" check (drives verified vs missing_information)
    storage.py       S3 + DynamoDB access (boto3)
    models.py        pydantic request/response models + enums
    config.py        env-driven settings
  tests/             18 unit tests (no AWS needed)
  infra/             dynamodb-table.json + create-local-resources.sh
  scripts/           dev_server.py, demo_ingest.py, aws_smoke.py, inspect_samples.py
  requirements.txt
  README.md
```

---

## 11. Decisions made (and where to change them)

| Decision | Current choice | Change where |
|----------|---------------|--------------|
| HSDS version | pinned to 3.2.3 | `RE_HSDS_VERSION`, `hsds_schema.py` |
| Storage | S3 (documents) + DynamoDB (index) | `storage.py` |
| Validation | pragmatic required-field subset | `hsds_schema.py` (swap in official JSON Schema for full validation) |
| Org description gap (ODRS has none) | demo uses a fallback string | demo adapter in `scripts/demo_ingest.py` |
| Important fields for verification | eligibility, hours, languages, accessibility, contact | `completeness.py` |
| Cross-source dedup | flag only, never auto-merge | `dedup.py`, `identity.py` |

---

## 12. Known gaps / next steps

- **Validation is a subset** of HSDS v3.2.3 required fields. For strict
  validation, pin the official `openreferral/specification` JSON Schema files
  (tag v3.2.3) and validate with `jsonschema`. The structure in
  `hsds_schema.py` mirrors those lists so it is a drop-in change.
- **No source adapters here** - converting raw ODRS/NDP/spreadsheet data to HSDS
  is upstream. `scripts/demo_ingest.py` has a *demo* mapping only.
- **No listing-by-status / listing-by-source API** yet. The DynamoDB GSIs
  (`status-index`, and source via scan/filter) support it; just needs endpoints,
  e.g. `GET /records?status=unverified` or `GET /records?source=ndp`.
- **No "mark verified" review action** yet (a `PATCH /records/{id}/verify` that
  flips status and writes an HSDS metadata audit entry).
- **Reconciliation (Priority 4)** is designed for (repeatable ingest, history in
  DynamoDB) but clash-detection is not implemented.
```
