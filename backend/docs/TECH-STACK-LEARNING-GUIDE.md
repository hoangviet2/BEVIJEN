# Resource Engine - Tech Stack Learning Guide

A beginner-friendly guide to every tool and concept used in the HSDS intake
work. Each item is explained twice: in **plain English** (the idea) and in
**technical terms** (what an interviewer wants to hear), plus **why we used it**.

Read top to bottom once. For interviews, the "Technical" column and the
"Interview soundbite" at the end of each section are the bits to remember.

---

## Part 1 - The big picture (what we built, in one breath)

We built the part of a system that takes community-service records that are
already in a standard format (HSDS), checks whether we have seen them before
(deduplication), stores them in the cloud, and marks each one "verified" or
"unverified." Teammates' adapters convert raw data into HSDS before it reaches
us; a later AI layer verifies records after us.

```
raw data -> [adapters convert to HSDS] -> [OUR PART: validate, dedup, store, mark status] -> API / AI verify
```

---

## Part 2 - Languages and frameworks

| Tool | Layman terms | Technical terms | Why we used it / advantage |
|------|--------------|-----------------|----------------------------|
| **Python** | The programming language everything is written in. Readable, beginner-friendly. | General-purpose, dynamically typed language; huge ecosystem for data and AWS (boto3). | One Degree's stated default; fast to write; great AWS + JSON support. |
| **FastAPI** | A toolkit to build a web API - the "front door" other programs call over the internet. | Modern async Python web framework; auto-generates OpenAPI/Swagger docs; validates requests via type hints. | Minimal boilerplate, built-in validation, free interactive `/docs` page, fast. |
| **Pydantic** | Defines the "shape" of data and automatically rejects anything that doesn't fit. | Data-validation library using Python type hints; parses/validates/serializes models. | Guarantees requests/responses have the right fields and types before our code runs. |
| **Uvicorn** | The engine that actually runs the FastAPI app and listens for web requests. | ASGI server that hosts async Python web apps. | Standard, fast way to serve FastAPI. |
| **pytest** | The tool that runs our automated tests to prove the code works. | Python testing framework with fixtures and simple assert-based tests. | Catches bugs early; our 18 tests run with no AWS needed. |

**Interview soundbite:** "We used Python with FastAPI for the API, Pydantic for
request/response validation, and pytest for testing. FastAPI gives you
validation and OpenAPI docs almost for free."

---

## Part 3 - AWS (cloud) services

| Tool | Layman terms | Technical terms | Why we used it / advantage |
|------|--------------|-----------------|----------------------------|
| **Amazon S3** | A giant online folder that stores files (here, one JSON file per record). | Object storage; stores arbitrary blobs by key, virtually unlimited size, versioning, encryption. | Cheap, unlimited, perfect for the full nested HSDS document; no size limits. |
| **Amazon DynamoDB** | A super-fast lookup table - give it a key, get the answer instantly. | Managed NoSQL key-value/document DB; single-digit-millisecond reads; scales automatically. | Fast exact lookups + dedup queries without managing servers; pay-per-request. |
| **DynamoDB GSI** (Global Secondary Index) | An extra "index card" so you can look records up by a different field, not just the main ID. | Secondary index with its own partition/sort key; enables queries on non-primary attributes. | Lets us find duplicates by content hash or match key in one fast lookup, not a full scan. |
| **AWS Bedrock** (context) | Amazon's service for using AI models (like Claude). | Managed API for foundation models; used by the AI verification layer. | Standard AWS way to call LLMs; no model hosting. (Used by the verify layer, not our intake part.) |
| **AgentCore** (context) | A way to run AI "agents" and secure who can call them. | Bedrock AgentCore Runtime + Identity (inbound JWT/OAuth auth). | Mentioned for the OAuth challenge; secures the agent endpoint. |
| **IAM** | The rulebook for "who is allowed to do what" in AWS. | Identity and Access Management; roles/policies granting least-privilege permissions. | Keeps each component locked down to only what it needs. |

**Interview soundbite:** "We split storage: S3 holds the full JSON document
because it's cheap and size-unlimited; DynamoDB holds a small index item for
millisecond lookups. S3 is bad at fast queries, DynamoDB is bad at big blobs -
using both lets each do what it's good at."

---

## Part 4 - Key libraries

| Tool | Layman terms | Technical terms | Why we used it / advantage |
|------|--------------|-----------------|----------------------------|
| **boto3** | The Python "remote control" for AWS - how our code talks to S3 and DynamoDB. | Official AWS SDK for Python; client + resource interfaces. | The standard, well-documented way to use AWS from Python. |
| **UUID (uuid5)** | A way to make a unique ID that's always the same for the same input. | RFC 4122 UUIDv5 = deterministic hash of a namespace + name. | Same source record always maps to the same ID, so re-imports update instead of duplicating. |
| **SHA-256 (hashlib)** | Turns a record into a short "fingerprint"; identical records get the same fingerprint. | Cryptographic hash; fixed-length digest of the content. | Detects unchanged re-sends instantly by comparing fingerprints. |

**Interview soundbite:** "We generate IDs with UUIDv5, so they're deterministic -
the same source + record ID always produces the same UUID. That's what makes
re-ingestion an idempotent upsert instead of creating duplicates."

---

## Part 5 - Standards and data concepts

| Concept | Layman terms | Technical terms | Why it matters |
|---------|--------------|-----------------|----------------|
| **HSDS** | A common format so everyone describes services (food, shelter, etc.) the same way. | Open Referral Human Services Data Specification v3.2.3; defines objects (organization, service, location...) and required fields. | Lets different sources (ODRS, 211/NDP, spreadsheets) become one consistent format. |
| **JSON** | A simple text format for structured data (keys and values). | JavaScript Object Notation; the serialization format for HSDS records and API payloads. | Human-readable, universal, what HSDS and REST APIs use. |
| **REST API** | A standard way for programs to ask for or send data over the web. | Representational State Transfer; resources addressed by URL, verbs GET/POST, returns JSON. | Simple, universal integration for any consumer. |
| **Bearer token / OAuth / JWT** | A "pass" you show to prove you're allowed in; no pass = rejected. | OAuth 2.0 access token (often a JWT) sent in the Authorization header; validated before access. | Secures the API so only authorized callers get through. |
| **Schema validation** | Checking a record has all the required fields before saving it. | Validating JSON against required-field rules (subset of HSDS schema). | Guarantees we never store or serve an invalid record. |
| **Deduplication** | Not storing the same thing twice. | Exact (by ID/content hash) + fuzzy (normalized name+location) duplicate detection. | Keeps data clean; the same pantry from two sources is flagged, not doubled. |
| **Idempotency** | Doing the same thing twice has the same result as doing it once. | Re-running ingest upserts by deterministic ID instead of inserting again. | Safe to re-run imports without creating messes. |

**Interview soundbite:** "HSDS is the interchange standard. We validate every
record against its required fields and quarantine anything invalid, so the store
and API are always schema-valid."

---

## Part 6 - Engineering practices and tools

| Tool/Practice | Layman terms | Technical terms | Why we used it / advantage |
|---------------|--------------|-----------------|----------------------------|
| **Git / GitHub** | Saves versions of the code and lets a team work together. | Distributed version control; branches, commits, pull requests, merges. | History, collaboration, safe parallel work via branches. |
| **Feature branch** | A separate workspace so your changes don't break others. | A branch (`feature/hsds`) merged into `main` via PR. | Isolates work; `main` stays stable. |
| **.gitignore** | A list of files Git should NOT save (junk, secrets, caches). | Patterns excluding `.venv/`, caches, `.env`, `.DS_Store`. | Keeps the repo clean and avoids committing secrets. |
| **Virtual environment (venv)** | A private sandbox for this project's Python packages. | Isolated Python environment so dependencies don't clash globally. | Reproducible installs; no version conflicts. |
| **requirements.txt** | The shopping list of packages the project needs. | Pinned dependency manifest for `pip install`. | Anyone can recreate the exact environment. |
| **Docker** (context) | Packages the app + everything it needs into one portable box. | Containerization; image runs identically anywhere (target: ECS Fargate). | Consistent deploys; "works on my machine" solved. |
| **Mocking / in-memory fakes** | Fake versions of S3/DynamoDB so tests run without the cloud. | Test doubles implementing the same interface as real storage. | Fast, free, offline tests; no AWS credentials needed. |

**Interview soundbite:** "Storage is behind an interface, so tests run against an
in-memory fake and production runs against S3 + DynamoDB with zero code change.
That keeps the 18 tests fast and offline."

---

## Part 7 - How the pieces fit (the flow, simply)

1. A record arrives at the **FastAPI** endpoint (already in **HSDS/JSON**).
2. **Pydantic** checks the request shape; **schema validation** checks HSDS
   required fields (invalid -> quarantined).
3. We compute a **UUIDv5** ID, a **SHA-256** content hash, and a fuzzy match key.
4. We do up to 3 fast **DynamoDB** lookups (primary key + 2 **GSIs**) to detect
   duplicates - no full scan.
5. We store the full document in **S3** and a small index item in **DynamoDB**,
   marked **verified** or **unverified**.
6. **boto3** is what talks to S3 and DynamoDB throughout.
7. **Git/GitHub** tracks all of it; **pytest** proves it works.

---

## Part 8 - Quick "why these choices" cheat sheet (great for interviews)

- **Why S3 AND DynamoDB?** Different jobs: S3 = cheap, unlimited document
  storage; DynamoDB = fast indexed lookups. Neither alone does both well.
- **Why deterministic UUIDs?** To make re-ingestion idempotent (update, not
  duplicate).
- **Why precompute hashes/keys and store them as GSI keys?** So duplicate
  detection is a constant-time indexed lookup instead of scanning every record -
  it stays fast whether the table has 100 or 100M rows.
- **Why split status and reason into two fields?** `verification_status` stays
  simple (verified/unverified); the reason (possible_duplicate /
  missing_information) lives separately so queries and UI stay clean.
- **Why FastAPI?** Validation + auto docs + speed with little code.
- **Why quarantine instead of dropping bad records?** You never silently lose
  data; you keep the reason for a human to fix.

---

## Part 9 - Glossary (one-liners)

- **NoSQL**: a database that isn't tables-with-rows-and-SQL; optimized for scale
  and fast key lookups (DynamoDB).
- **Partition key**: the main lookup key in DynamoDB; decides where an item is
  stored.
- **Object storage**: store whole files by name (S3), vs a database of fields.
- **Serialization**: turning an object into text (JSON) to store or send it.
- **Upsert**: insert if new, update if it already exists.
- **Least privilege**: give each component only the permissions it truly needs.
- **ASGI**: the async standard FastAPI/Uvicorn use to handle web requests.
- **SDK**: Software Development Kit - a library to use a service from code (boto3
  is the AWS SDK for Python).
