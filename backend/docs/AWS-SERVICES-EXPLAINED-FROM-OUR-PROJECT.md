# AWS Services, Explained Through Our Project

A beginner's guide to the AWS services we use, learned from **our own code**.
For each service: what it is (plain English), why we used it, and exactly where
it shows up in the repo. Lots of diagrams. A one-liner summary of every step is
at the very end.

---

## 1. The 60-second overview

Our project (the Resource Engine) takes community-service records, cleans and
stores them, and uses AI to check them. To do that it leans on four AWS
services:

```
        ┌──────────────────────── OUR APP (FastAPI, Python) ────────────────────────┐
        │                                                                            │
  data ─┤  validate → dedup → STORE → AI verify                                      │
        │                      │  │        │                                         │
        └──────────────────────┼──┼────────┼─────────────────────────────────────────┘
                               │  │        │
                     ┌─────────┘  │        └──────────────┐
                     ▼            ▼                        ▼
                ┌─────────┐  ┌──────────┐        ┌───────────────────┐
                │   S3    │  │ DynamoDB │        │ Bedrock / AgentCore│
                │ (files) │  │ (index)  │        │  (the AI brain)    │
                └─────────┘  └──────────┘        └───────────────────┘
                 full docs    fast lookups         reads docs, answers,
                                                   verifies records
```

| Service | One line | Our use |
|---------|----------|---------|
| **Amazon S3** | An online hard drive for files. | Stores the full HSDS JSON document per record. |
| **Amazon DynamoDB** | A super-fast lookup table (NoSQL). | Stores a small "index card" per record for instant lookups + dedup. |
| **Amazon Bedrock** | Rent an AI model (Claude) via an API. | The model that reads web content and drafts/verifies answers. |
| **Bedrock AgentCore** | Run + secure AI "agents" that can use tools + memory. | Our verification agent that checks records; has memory + identity. |
| **IAM** | The permission system ("who can do what"). | Gives each part only the access it needs. |

---

## 2. Amazon S3 (Simple Storage Service)

### What it is (layman)
A giant online folder. You put **files** ("objects") in a **bucket** and get them
back by their **key** (the path/name). Virtually unlimited, cheap, durable.

### Why we used it
Our records are big nested JSON documents. S3 is perfect for storing whole files
of any size cheaply. It's our **canonical copy** of each record.

### How it looks
```
Bucket:  team20-s3-test-bucket
└── hsds/
    ├── odrs/
    │   └── 41b9ce52-....json      ← one full HSDS document
    └── ndp/
        └── 9e86cd6c-....json
```
Notice the **second folder = the source** (odrs, ndp). That is how we categorize
data by where it came from, just from the key.

### Where it is in our code
- `backend/app/store/backend.py` → class **`S3Blobs`** (`put_json`, `get_json`,
  `list`, `presign`) wraps the S3 client.
- `backend/app/store/__init__.py` → `S3Blobs(settings.s3_bucket, aws.client("s3", ...))`
- `backend/app/store/commit.py` → `s3_key(source, service_id)` builds
  `hsds/{source}/{id}.json` and writes the document.

### Key terms
- **Bucket**: the top-level container.
- **Object / key**: a file and its path-like name.
- **Object storage**: store whole files by name (vs a database of fields).

---

## 3. Amazon DynamoDB

### What it is (layman)
A lookup table where you hand it a **key** and get the matching row back in
milliseconds. It is **NoSQL** (no SQL joins/tables-with-schemas) and scales
automatically - just as fast with 100 or 100 million rows.

### Why we used it
We need **instant** answers to "do I already have this record?" and "show me all
unverified records." S3 is bad at that (it's for files, not queries). DynamoDB
is built for exactly these key lookups.

### S3 + DynamoDB together (the key design idea)
```
     one incoming record
             │
      ┌──────┴───────┐
      ▼              ▼
 ┌─────────┐   ┌───────────────────────────────┐
 │   S3    │   │          DynamoDB             │
 │ full    │   │  small "index card":          │
 │ JSON    │   │   service_id (key)            │
 │ document│   │   content_hash, match_key     │  ← used to detect duplicates
 │         │   │   verification_status         │  ← verified / unverified
 │         │   │   s3_key ──────────────────┐  │  ← points back to the S3 file
 └─────────┘   └────────────────────────────┼──┘
      ▲                                      │
      └──────────────────────────────────────┘
```
S3 holds the heavy document; DynamoDB holds a light card that is fast to search
and points back to S3. Each does what it is good at.

### Indexes (GSI) - how dedup stays fast
A **Global Secondary Index** is an extra way to look up rows by a different
field. We store a precomputed **content hash** and **match key** as index keys,
so checking for a duplicate is a direct lookup, not a scan of every record.
```
Primary key:        service_id        → "is this exact record here?"
GSI content-hash:   hash_pk           → "identical content already stored?"
GSI match-key:      match_pk          → "same real-world place, maybe a dup?"
GSI status:         status_pk         → "list all unverified records"
```

### Where it is in our code
- `backend/app/store/backend.py` → classes **`DynamoTable`** / `MemoryTable`,
  and the **`SPECS`** dict defining every table + its GSIs.
- `backend/app/store/commit.py` → writes the index item (status, hashes, s3_key).
- `backend/app/dedup.py` + `backend/app/pipeline.py` → the 3 fast lookups.

### Key terms
- **Partition key**: the main lookup key; decides where the item lives.
- **GSI (Global Secondary Index)**: look rows up by a non-primary field.
- **Query vs Scan**: Query = jump to matching keys (fast); Scan = read everything
  (slow). We only Query on the dedup path.

---

## 4. Amazon Bedrock

### What it is (layman)
A service to **use powerful AI models (like Anthropic Claude) through a simple
API** - no training, no servers. You send text, you get text back.

### Why we used it
The AI maintenance part of the project reads an organization's website content
and compares it to our stored record (eligibility, hours, languages, etc.), then
drafts findings. Bedrock gives us the model to do that.

### How a call works
```
 our code ──► Bedrock ──► Claude model ──► answer text ──► back to our code
            (InvokeModel / Converse)
```

### Where it is in our code
- `settings.bedrock_model_id` is the model id we call (see `routers/tools.py`,
  `verify/worker.py` which records `last_check_model`).
- The verification layer sends content to the model and stores the result as a
  "finding."

### Key terms
- **Foundation model / LLM**: a large pre-trained model (Claude).
- **Hallucination**: when a model makes something up - why we ground answers in
  real data and keep evidence.
- **Embedding model**: a different kind of model that turns text into vectors
  (used for search / Knowledge Bases).

---

## 5. Bedrock AgentCore

### What it is (layman)
A place to **run and secure AI "agents"**. An agent is an AI that can do more
than chat - it can call **tools**, remember things across turns (**memory**),
and must prove **who it is** to be allowed in (**identity**).

### The three pieces we touch
```
            ┌──────────────── AgentCore ────────────────┐
 caller ───►│  Identity (who can call) → Runtime (runs   │
  (+ token) │  the agent) → the agent uses Tools + Memory │
            └────────────────────────────────────────────┘
```
- **Runtime**: where the agent code actually runs (a managed container).
- **Identity / inbound auth**: the agent endpoint requires a valid token
  (OAuth/JWT); a call with no token is rejected. (This is the "inbound OAuth"
  challenge.)
- **Memory**: lets the agent remember context across requests.
- **Observability**: traces each request end-to-end (incl. tool calls) so you
  can see what the agent did.

### Why we used it
Our record-verification agent benefits from memory (context across checks) and
needs to be secured (only authorized callers). AgentCore provides both without
us building that plumbing.

### Where it is in our code
- `backend/app/verify/agent_client.py` → `aws.client("bedrock-agentcore", ...)`
  and the runtime invoke URL
  `https://bedrock-agentcore.<region>.amazonaws.com/runtimes/...`.
- `backend/app/verify/memory.py` → uses the `bedrock-agentcore` client for the
  Memory service.

### Key terms
- **Agent**: AI that can use tools + memory, not just reply.
- **Tool call**: the agent invoking a function/API to get real data.
- **Inbound auth (JWT/OAuth)**: the "pass" a caller must show; no pass = 401/403.
- **Trace / observability**: a step-by-step record of one request (incl. tool
  calls).

---

## 6. IAM (Identity and Access Management)

### What it is (layman)
The **rulebook of permissions** in AWS: who (or which service) is allowed to do
what, on which resource.

### Why we used it
Security. Each component should only touch what it needs - the store code can
read/write its bucket and table; the KB role can read docs + call the embedder.
Nothing has more power than necessary ("least privilege").

### Core ideas
```
  Role  = a "badge" a service wears temporarily
  Policy = the list of allowed actions on the badge
  Trust  = who is allowed to wear the badge
```

### Where it is in our code / infra
- The DynamoDB/S3 access the app uses runs under an execution role.
- The Knowledge Base used a dedicated role
  `AmazonBedrockExecutionRoleForKB-team20` with 3 policies (model invoke, S3
  read, vector store).

### Key terms
- **Role vs user**: services use roles (temporary creds), not long-lived users.
- **Least privilege**: grant only what is needed.
- **Trust policy**: defines who may assume the role (e.g. `bedrock.amazonaws.com`).

---

## 7. boto3 - how Python talks to AWS

### What it is
**boto3** is the official AWS SDK for Python - the library our code uses to call
S3, DynamoDB, and Bedrock.

### Where it is in our code
- `backend/app/aws.py` → a small factory: `aws.client(...)` / `aws.resource(...)`
  creates the boto3 clients everyone else uses. Centralizing it means region,
  retries, and (for tests) a fake endpoint are set in one place.

```
 our code ──► app/aws.py (boto3 factory) ──► boto3 ──► AWS service
```

---

## 8. The whole flow, with the services marked

```
      ┌────────────────┐
      │ incoming record│  (already HSDS)
      └───────┬────────┘
              ▼
      ┌────────────────┐
      │ validate HSDS  │  (pure Python, no AWS)
      └───────┬────────┘
              ▼
      ┌────────────────┐    3 fast lookups
      │ dedup check    │───────────────────────►  DynamoDB (GSIs)
      └───────┬────────┘
              ▼
      ┌────────────────┐    write full document ►  S3
      │ store record   │    write index card    ►  DynamoDB
      └───────┬────────┘    (status = unverified)
              ▼
      ┌────────────────┐    reads web content,
      │ AI verify      │    drafts findings      ►  Bedrock (Claude)
      │ (agent)        │    runs as an agent     ►  AgentCore (runtime/
      └───────┬────────┘                            memory/identity)
              ▼
      ┌────────────────┐
      │ human reviews  │  flips unverified → verified
      └────────────────┘
      (everything is permissioned by IAM throughout)
```

---

## 9. Where to look in the repo (quick map)

| To study... | Open... |
|-------------|---------|
| S3 access | `backend/app/store/backend.py` (`S3Blobs`) |
| DynamoDB access + table/GSI design | `backend/app/store/backend.py` (`DynamoTable`, `SPECS`) |
| How a record is written to both | `backend/app/store/commit.py` |
| Dedup logic (the 3 lookups) | `backend/app/dedup.py`, `backend/app/pipeline.py` |
| boto3 client factory | `backend/app/aws.py` |
| Bedrock model id usage | `backend/app/routers/tools.py`, `backend/app/verify/worker.py` |
| AgentCore (runtime + memory) | `backend/app/verify/agent_client.py`, `backend/app/verify/memory.py` |
| The Knowledge Base challenge | `backend/docs/CHALLENGE-BEDROCK-KNOWLEDGE-BASE.md` |

---

## 10. Interview soundbites (say these)

- **S3 vs DynamoDB:** "S3 stores the full document cheaply; DynamoDB stores a
  small index item for millisecond lookups and dedup. Neither alone does both
  well, so we use both."
- **Dedup efficiency:** "We precompute a content hash and a match key at write
  time and store them as DynamoDB GSI keys, so duplicate detection is a
  constant-time indexed lookup, not a full-table scan."
- **Bedrock:** "Bedrock lets us call Claude via an API to verify records against
  web content, with evidence kept for every finding so nothing is a blind
  hallucination."
- **AgentCore:** "AgentCore runs and secures the verification agent - inbound
  OAuth so only authorized callers reach it, memory for cross-request context,
  and observability to trace each request including tool calls."
- **IAM:** "Every component runs under a least-privilege role - it can only touch
  the specific bucket, table, or model it needs."

---

## 11. SUMMARY - every step in one line

**AWS services**
1. **S3** = online folder for files → we store the full HSDS JSON document per record.
2. **DynamoDB** = instant key-lookup NoSQL table → we store a small index card (status, hashes, pointer) per record.
3. **GSI** = extra index on DynamoDB → makes duplicate detection a fast lookup, not a scan.
4. **Bedrock** = use Claude via an API → the AI that verifies records against web content.
5. **AgentCore** = run + secure AI agents → runtime + inbound OAuth + memory + observability for our verify agent.
6. **IAM** = AWS permission rulebook → least-privilege roles so each part only touches what it needs.
7. **boto3** = Python AWS SDK → `app/aws.py` is our one factory that creates all AWS clients.

**What our code does (pipeline)**
8. A record arrives already in **HSDS** format.
9. We **validate** it (pure Python); invalid ones are quarantined, never stored.
10. We compute a **deterministic ID**, a **content hash**, and a **fuzzy match key**.
11. We do up to **3 DynamoDB lookups** to detect duplicates (no full scan).
12. We write the **full document to S3** and the **index card to DynamoDB**.
13. New records start **unverified**; the AI verify agent (Bedrock + AgentCore) checks them.
14. A human flips **unverified → verified**; everything is secured by **IAM**.

**Knowledge Base challenge (RAG)**
15. Put documents in **S3**.
16. Create an **IAM role** so Bedrock can read docs + call the embedder + write vectors.
17. Create a **vector store** (S3 Vectors) sized to the embedding model (1024 dims).
18. Create the **Knowledge Base** (ties embedder + vector store together).
19. Create a **data source** pointing at the S3 docs, with a **chunking** strategy.
20. Run an **ingestion job**: chunk → embed → index (wait until COMPLETE).
21. Ask a question with **RetrieveAndGenerate** → get an answer **plus a citation** (the proof).
