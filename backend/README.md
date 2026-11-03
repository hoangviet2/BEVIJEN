# Resource Engine - HSDS Intake Service

This service receives data that has **already been converted to HSDS** (Open Referral
Human Services Data Specification), checks it for duplicates, and stores it:

- the full HSDS JSON document goes to **Amazon S3** (durable canonical copy)
- a lightweight index/metadata item goes to **Amazon DynamoDB** (fast lookups, dedup, status)

Every record is marked `verification_status = "unverified"` on insert. A later
maintenance layer flips it to verified / discrepancy / etc.

HSDS version pinned: **v3.2.3**.

## Pipeline

```
HSDS record (JSON)
  -> validate against pinned HSDS schema        (invalid -> quarantined, not stored)
  -> compute deterministic id (UUIDv5)           (source + native id)
  -> compute content hash (dedup of identical payloads)
  -> compute fuzzy match key (org name + location/phone)
  -> check DynamoDB for duplicates
       exact id match          -> upsert (update existing), status preserved
       content hash match      -> skip (identical payload already stored)
       fuzzy match key match    -> store, mark possible_duplicate
       no match                 -> store, mark unverified
  -> write full JSON to S3
  -> write index item to DynamoDB
```

## Why both S3 and DynamoDB

- **S3** stores the full nested HSDS graph cheaply, at any size, keyed by
  `hsds/{source}/{service_id}.json`. This is the canonical copy.
- **DynamoDB** holds only the fields needed to look records up fast and run the
  duplicate checks (ids, hashes, match key, status, S3 pointer). DynamoDB is bad
  at storing/scanning large blobs; S3 is bad at fast queries. Each does its job.

This also follows the HSDS guidance: operational data (status, provenance,
dedup state) is kept separate from the HSDS records themselves.

## Layout

```
backend/
  app/
    __init__.py
    config.py          # env-driven settings (bucket, table, region, HSDS version)
    models.py          # pydantic request/response models
    hsds_schema.py     # minimal pinned HSDS validation (required fields per object)
    identity.py        # deterministic id, content hash, fuzzy match key
    dedup.py           # duplicate classification against DynamoDB
    storage.py         # S3 + DynamoDB read/write
    intake.py          # orchestrates validate -> dedup -> store
    main.py            # FastAPI app and endpoints
  tests/
    test_identity.py
    test_intake.py
  requirements.txt
  infra/
    dynamodb-table.json
```

## Run locally

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# point at LocalStack or real AWS via env vars (see app/config.py)
export RE_S3_BUCKET=resource-engine-hsds
export RE_DDB_TABLE=resource-engine-index
export AWS_REGION=us-west-2

uvicorn app.main:app --reload
```

Tests (no AWS needed - storage is mocked):

```bash
cd backend
pip install -r requirements.txt
pytest
```

## API

- `GET /` - service info and pinned HSDS version
- `POST /records` - intake one HSDS service record. Returns the classification
  (`inserted` | `updated` | `skipped_duplicate` | `possible_duplicate` | `quarantined`).
- `POST /records/batch` - intake a list of records; returns per-record results
  and R1.7 counts (received = stored + quarantined).
- `GET /records/{service_id}` - fetch the stored HSDS document from S3.

## Decisions / notes

- Duplicate handling has two layers:
  1. **Exact re-ingest** (same source + native id) is an upsert on the
     deterministic UUIDv5 id. This is the hard guarantee (R1.6).
  2. **Cross-source same-real-world-resource** is detected with a fuzzy match
     key (normalized org name + first location postal code / phone) and only
     *flagged* `possible_duplicate` for a human - never auto-merged. This is the
     stretch "org deduplication" and is intentionally conservative.
- Validation here is a pragmatic subset of HSDS v3.2.3 required fields. Swap in
  the official JSON Schema files from a pinned openreferral/specification tag for
  full validation (see `hsds_schema.py` docstring).
