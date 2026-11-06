# Knowledge Base Build Log - What We Actually Did (and Why)

This is a record of the real steps taken to build a Bedrock Knowledge Base in
the team's AWS account, why each step exists, what succeeded, what failed, and
the exact current state. It is the companion to
`CHALLENGE-BEDROCK-KNOWLEDGE-BASE.md` (which is the general how-to); this file is
the actual run log with real names and ARNs.

> Note on screenshots: the assistant has no browser access, so this doc gives
> the exact console locations/URLs and ASCII mockups of each screen. Open those
> URLs yourself and screenshot them for your challenge proof.

---

## 0. Environment

| Item | Value |
|------|-------|
| AWS account | `017820703243` (Workshop Studio, `WSParticipantRole`) |
| Region | `us-west-2` (Oregon) |
| Embedding model | `amazon.titan-embed-text-v2:0` (1024 dims, FLOAT32) - access verified |
| Generation model (for answers) | an Anthropic Claude model (e.g. `anthropic.claude-sonnet-4-5-...`) |
| Vector store choice | **S3 Vectors** (simplest, AWS-managed) |
| Chunking choice | **fixed_size** (300 tokens, 20% overlap) |

Why these choices: S3 Vectors needs no separate cluster to run, so it is the
lowest-setup vector store. fixed_size chunking is the simplest and fine for
text-heavy markdown docs.

---

## 1. Resources we created (real names / ARNs)

| Resource | Name / ID | ARN or location |
|----------|-----------|-----------------|
| Docs S3 bucket | `team20-kb-docs-017820703243` | `s3://team20-kb-docs-017820703243/docs/` |
| S3 Vectors bucket | `team20-kb-vectors` | `arn:aws:s3vectors:us-west-2:017820703243:bucket/team20-kb-vectors` |
| Vector index | `kb-index` | `arn:aws:s3vectors:us-west-2:017820703243:bucket/team20-kb-vectors/index/kb-index` |
| IAM service role | `AmazonBedrockExecutionRoleForKB-team20` | `arn:aws:iam::017820703243:role/AmazonBedrockExecutionRoleForKB-team20` |
| Knowledge Base | `team20-resource-engine-kb` | KB id `KXYSILWLN3` |
| Data source | `onedegree-docs` | ds id `LYLGHMPJ0P` |

Documents ingested (3 markdown files from `ai/resources/`):
- `One-Degree-technical-document.md`
- `One-Degree-vision-document.md`
- `OneDegree-additional-tech-background.md`

---

## 2. Step-by-step: what we did and why

### Step A - Checked the account and model access
**What:** confirmed identity (`sts get-caller-identity`), listed Bedrock models,
test-invoked the Titan embedding model, and confirmed S3 Vectors + OpenSearch
Serverless are both available.
**Why:** a KB needs (1) an embedding model you can actually invoke and (2) a
vector store that exists in the region. Listing a model is not the same as having
access, so we test-invoked Titan to be sure. Result: embedding invoke = OK,
S3 Vectors = available.

### Step B - Uploaded documents to S3
**What:** created bucket `team20-kb-docs-017820703243` and uploaded the 3
markdown docs under `docs/`.
**Why:** the KB reads source documents from an S3 "data source." The docs must
live in S3 before the KB can ingest them. We chose the 3 markdown docs because
they are text-rich and we know their content, so we can verify the cited answer.

### Step C - Created the S3 Vectors bucket + index
**What:** created vector bucket `team20-kb-vectors` and index `kb-index` with
dimension 1024, distance metric cosine.
**Why:** embeddings are 1024-dimension vectors for Titan v2, so the index
dimension MUST be 1024 or ingestion fails. The vector store is where the
"meaning fingerprints" of each chunk are saved for similarity search.

### Step D - Created the IAM service role
**What:** created role `AmazonBedrockExecutionRoleForKB-team20` with:
- a **trust policy** letting `bedrock.amazonaws.com` assume it (with
  `aws:SourceAccount` + `aws:SourceArn` confused-deputy protection),
- **model** permission (`bedrock:InvokeModel` on the Titan embedding model),
- **S3** permission (`s3:GetObject`, `s3:ListBucket` on the docs bucket),
- **S3 Vectors** permission (`s3vectors:PutVectors/GetVectors/QueryVectors/...`).

**Why:** Bedrock performs ingestion and retrieval on your behalf by "wearing"
this role. Without it, the KB cannot read your docs, call the embedder, or write
vectors. Least-privilege: it can only reach these specific resources.

### Step E - Created the Knowledge Base
**What:** `create-knowledge-base` with the Titan embedding model and S3 Vectors
storage config. KB id = `KXYSILWLN3`. Waited until status = ACTIVE.
**Why:** the KB is the resource that ties the embedder + vector store together
and manages ingestion/retrieval.

### Step F - Created the data source
**What:** `create-data-source` pointing the KB at
`s3://team20-kb-docs-017820703243/docs/` with fixed_size chunking (300 tokens,
20% overlap). ds id = `LYLGHMPJ0P`.
**Why:** the data source tells the KB WHERE the docs are and HOW to chunk them.
Chunking config is fixed at creation time (irreversible), which is why we chose
it deliberately.

### Step G - Ran the ingestion job  ->  FAILED (first attempt)
**What:** `start-ingestion-job`, then polled `get-ingestion-job`.
**Result:** status **FAILED**. Statistics: 3 documents scanned, 3 failed.
**Error:**
```
Invalid record ...: Filterable metadata must have at most 2048 bytes
(Service: S3Vectors, Status Code: 400)
```
**Why it failed (root cause):** S3 Vectors treats stored metadata as
"filterable" by default, and filterable metadata is capped at **2048 bytes per
record**. Bedrock stores each chunk's source TEXT as metadata on the vector. Our
chunks (300 tokens) are larger than 2048 bytes, so every record was rejected.
This is a known S3 Vectors limit, not a mistake in the documents.

### Step H - Fix attempted: recreate the index with non-filterable text key
**What (ATTEMPTED, STATE UNCONFIRMED):** delete `kb-index` and recreate it with
`metadataConfiguration.nonFilterableMetadataKeys =
["AMAZON_BEDROCK_TEXT","AMAZON_BEDROCK_METADATA"]`.
**Why:** marking the large text key as **non-filterable** exempts it from the
2048-byte filterable limit, so the chunk text can be stored alongside the vector
without rejection. This is the standard fix for exactly this error.
**Status:** this command was **interrupted before it completed**, so we do NOT
know whether the index was recreated. See "Current state" below.

---

## 3. Current state (as of this log)

| Resource | State |
|----------|-------|
| Docs in S3 | DONE - 3 files uploaded |
| S3 Vectors bucket | DONE |
| Vector index `kb-index` | UNCONFIRMED - may be the original (filterable) index, may have been deleted/recreated, or may be deleted with nothing in its place. Must be checked before continuing. |
| IAM role | DONE |
| Knowledge Base `KXYSILWLN3` | ACTIVE (created), but **has no successfully ingested data yet** |
| Data source `LYLGHMPJ0P` | AVAILABLE (created) |
| Ingestion | FAILED once (metadata size). Fix attempted but unconfirmed. |

**Bottom line:** everything is built EXCEPT a successful ingestion. The KB is not
yet queryable because no vectors are indexed. The remaining work is: confirm the
index exists with the non-filterable text key, then re-run the ingestion job and
poll to COMPLETE, then run the cited-answer query for the proof.

---

## 4. Remaining steps to finish the challenge

1. **Verify / fix the index.** Check `s3vectors get-index` on
   `team20-kb-vectors/kb-index`. It must exist with dimension 1024, cosine, and
   `nonFilterableMetadataKeys` including `AMAZON_BEDROCK_TEXT`. If it is missing
   or still filterable-only, delete and recreate it with that metadata config.
2. **Re-run ingestion.** `start-ingestion-job` on KB `KXYSILWLN3` / ds
   `LYLGHMPJ0P`; poll `get-ingestion-job` until `COMPLETE`. Expect 3 docs
   indexed, 0 failed.
3. **Verify retrieval.** `bedrock-agent-runtime retrieve` with a question the
   docs answer (e.g. "What HSDS version does the Resource Engine use?").
4. **Get the cited answer (the proof).** `bedrock-agent-runtime
   retrieve-and-generate` with a Claude model ARN. The response's
   `citations[].retrievedReferences[].location.s3Location.uri` is the cited
   source document.

---

## 5. How to view each resource in the AWS Console (for screenshots)

Make sure the region selector (top-right) says **Oregon / us-west-2** or pages
look empty.

### 5.1 The source documents (S3)
URL:
`https://us-west-2.console.aws.amazon.com/s3/buckets/team20-kb-docs-017820703243?region=us-west-2&prefix=docs/`

What you should see (screenshot this):
```
S3 > Buckets > team20-kb-docs-017820703243 > docs/
┌───────────────────────────────────────────────┬──────────┐
│ Name                                           │ Size     │
├───────────────────────────────────────────────┼──────────┤
│ One-Degree-technical-document.md               │ 8.6 KB   │
│ One-Degree-vision-document.md                  │ 18.0 KB  │
│ OneDegree-additional-tech-background.md        │ 13.7 KB  │
└───────────────────────────────────────────────┴──────────┘
```

### 5.2 The Knowledge Base
URL (KB console list):
`https://us-west-2.console.aws.amazon.com/bedrock/home?region=us-west-2#/knowledge-bases`
Then open **team20-resource-engine-kb** (id `KXYSILWLN3`).

What the KB detail page shows (screenshot this):
```
Amazon Bedrock > Knowledge bases > team20-resource-engine-kb
┌──────────────────────────────────────────────────────────────┐
│ Knowledge Base overview                                        │
│   Knowledge Base ID : KXYSILWLN3                               │
│   Status            : Available                                │
│   Service role      : AmazonBedrockExecutionRoleForKB-team20   │
│   Embeddings model  : Titan Text Embeddings V2                 │
│   Vector store      : Amazon S3 Vectors                        │
│                                                                │
│ Data source: onedegree-docs (LYLGHMPJ0P)                       │
│   S3 URI    : s3://team20-kb-docs-017820703243/docs/           │
│   Sync status : <Failed before fix / Available after fix>      │
│   [ Sync ] button  <- this runs an ingestion job               │
└──────────────────────────────────────────────────────────────┘
```

Tip: the console calls "ingestion job" a **Sync**. Clicking **Sync** and showing
the data source turn green is the easiest console proof of ingestion.

### 5.3 The ingestion job result
On the KB page, click the data source `onedegree-docs`, then the **Sync history**
tab. Each sync shows: source docs scanned, added, failed.
```
Sync history
┌────────────┬─────────┬───────┬────────┬────────┐
│ Status     │ Scanned │ Added │ Failed │ When   │
├────────────┼─────────┼───────┼────────┼────────┤
│ Failed     │ 3       │ 0     │ 3      │ ...    │   (first attempt)
│ Completed  │ 3       │ 3     │ 0      │ ...    │   (after the fix)
└────────────┴─────────┴───────┴────────┴────────┘
```

### 5.4 The cited answer (the actual proof)
On the KB page, use the **Test knowledge base** panel on the right:
1. Toggle **Generate responses** ON and pick a model (a Claude model).
2. Ask: *"What HSDS version does the Resource Engine use?"*
3. The answer appears with a numbered **citation**; click it to see the source
   chunk and the source file name.
```
Test knowledge base
┌────────────────────────────────────────────────────────────┐
│ Q: What HSDS version does the Resource Engine use?           │
│                                                              │
│ A: The Resource Engine uses HSDS version 3.2.3. [1]          │
│                                                              │
│ Sources                                                      │
│   [1] One-Degree-technical-document.md                       │
│       s3://team20-kb-docs-.../docs/One-Degree-technical-...  │
│       "...HSDS version | v3.2.3 ..."                         │
└────────────────────────────────────────────────────────────┘
```
Screenshot this panel - the question + answer + citation with the source file is
exactly the "live answer with the source document cited" the challenge asks for.

### 5.5 The IAM role (optional, shows permissions)
URL:
`https://us-east-1.console.aws.amazon.com/iam/home#/roles/details/AmazonBedrockExecutionRoleForKB-team20`
(IAM is global; it opens in us-east-1 but the role applies everywhere.)
Shows the trust policy (bedrock.amazonaws.com) and the 3 inline policies
(model invoke, S3 read, S3 Vectors access).

### 5.6 The vector store (S3 Vectors)
S3 Vectors is newer and may not have a rich console page; you can show it via the
S3 console vector-buckets view if present, or just reference the ARN
`arn:aws:s3vectors:us-west-2:017820703243:bucket/team20-kb-vectors`.

---

## 6. Why the first ingestion failing is actually a good story for the challenge

It demonstrates real debugging: the error was precise
("filterable metadata must have at most 2048 bytes"), the root cause was the S3
Vectors per-record filterable-metadata limit colliding with Bedrock storing
chunk text as metadata, and the fix is to declare the text key non-filterable so
it is exempt from that limit. Mentioning this in a write-up or interview shows
you understand how the vector store actually stores chunks, not just that you
clicked "create."

---

## 7. Cleanup (when you are done, to avoid leaving resources)

Delete in this order (reverse of creation):
1. Data source, then Knowledge Base (`delete-data-source`, `delete-knowledge-base`).
2. Vector index, then vector bucket (`s3vectors delete-index`, `delete-vector-bucket`).
3. IAM role inline policies, then the role.
4. Empty and delete the docs S3 bucket.
Only do this after you have captured your proof screenshots.
