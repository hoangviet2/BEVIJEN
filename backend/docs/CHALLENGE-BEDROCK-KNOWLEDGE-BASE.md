# Challenge: Ingest Your Own Data into a Bedrock Knowledge Base and Connect It to Your Agent

> **The task:** "Ingest your own data into a Bedrock Knowledge Base and connect
> it to your agent. Proof: a live answer with the source document cited."

This guide explains the challenge in plain English, defines every technical
term, shows the architecture and flow as diagrams, and gives you the exact
step-by-step commands. Written for a beginner, but complete enough to use as an
interview talking point.

---

## 1. What the challenge is really asking (layman terms)

Imagine you have some documents (PDFs, text files, markdown) and you want an AI
assistant that can **answer questions using those documents** - and, crucially,
**tell you which document it got the answer from** (like a footnote/citation).

You are being asked to:

1. **Put your own documents somewhere AWS can read them** (an S3 bucket).
2. **Create a "Knowledge Base"** - AWS reads your docs, chops them into pieces,
   and converts each piece into numbers (a "vector") so the AI can search by
   *meaning*, not just keywords.
3. **Ask a question** through the AI. It finds the most relevant pieces, writes
   an answer, and **cites the source document**.

The "proof" is a screenshot or log showing: your question -> the AI's answer ->
the name of the source document it used.

This pattern has a name: **RAG = Retrieval-Augmented Generation**. The AI
"retrieves" relevant text from your docs, then "generates" an answer grounded in
that text.

---

## 2. Why this matters (the problem it solves)

A plain LLM (like Claude) only knows what it was trained on. It does **not** know
*your* data (your org's records, your PDFs), and if you ask it anyway it may
**make things up** (a "hallucination").

RAG fixes this by giving the model your actual documents at question time, so:
- Answers are based on **your** data.
- Every answer can be **traced back to a source** (the citation).
- You don't have to retrain or fine-tune a model.

---

## 3. Key terms, defined simply

| Term | Plain English | Technical meaning |
|------|---------------|-------------------|
| **LLM / Foundation Model** | The AI that writes answers (e.g. Claude). | A large language model served via Amazon Bedrock. |
| **RAG** | "Look it up, then answer." | Retrieval-Augmented Generation: retrieve relevant context, then generate an answer grounded in it. |
| **Knowledge Base (KB)** | The searchable library built from your docs. | A Bedrock resource that manages chunking, embedding, storage, and retrieval. |
| **Data source** | Where your docs live. | An input connector to the KB - here, an S3 bucket. |
| **Chunking** | Cutting big docs into bite-sized pieces. | Splitting documents into chunks so each fits the model and is individually searchable. |
| **Embedding** | Turning text into numbers that capture meaning. | A vector (list of floats) produced by an embedding model (default: `amazon.titan-embed-text-v2:0`). |
| **Vector / Vector store** | A "meaning fingerprint" and the database that holds them. | High-dimensional vectors stored in a vector database for similarity search. |
| **Similarity search** | Find pieces that *mean* the same thing as the question. | Nearest-neighbor search over embeddings (cosine/dot-product distance). |
| **Ingestion job** | The step where AWS reads + processes your docs. | A job that chunks, embeds, and indexes the data source into the vector store. |
| **Retrieve** | Get the matching raw pieces. | `bedrock-agent-runtime retrieve` - returns chunks, no answer written. |
| **RetrieveAndGenerate** | Get pieces AND a written answer with citations. | `bedrock-agent-runtime retrieve-and-generate` - the full RAG call. |
| **Citation** | The footnote saying which doc was used. | `citations[].retrievedReferences[]` in the API response (includes source S3 URI + text). |
| **Agent** | The thing the user talks to that uses the KB. | A Bedrock Agent (or your own app) configured to query the KB. |
| **IAM role** | A permission badge AWS components wear. | An execution role granting the KB access to S3, the embedding model, and the vector store. |
| **Embedding model vs generation model** | One turns text into vectors; the other writes the answer. | They can be different models - that is normal, not a mismatch. |

---

## 4. The architecture (diagram)

```
                         INGESTION (one-time / on update)
  ┌───────────┐      ┌──────────────────────────────────────────────┐
  │ Your docs │      │            Amazon Bedrock Knowledge Base       │
  │ PDF/TXT/MD│──┐   │                                                │
  └───────────┘  │   │   chunk ──► embed (Titan) ──► store vectors    │
                 ▼   │                                   │            │
            ┌─────────┐  read  │                         ▼            │
            │   S3     │───────►│                   ┌───────────┐     │
            │ (bucket) │        │                   │  Vector   │     │
            └─────────┘         │                   │  store    │     │
                                └───────────────────┴───────────┴─────┘
                                                        ▲
                         QUERY (every question)         │ similarity search
  ┌──────┐   question   ┌───────────────┐   retrieve    │
  │ User │─────────────►│  Agent / App  │──────────────►│
  │      │◄─────────────│ (RetrieveAnd  │◄──────────────┘
  └──────┘  answer +    │  Generate)    │  top chunks + source refs
            citation    └───────┬───────┘
                                │ sends chunks + question to
                                ▼
                        ┌───────────────┐
                        │  LLM (Claude) │  writes grounded answer
                        └───────────────┘
```

Two phases:
- **Ingestion** (left/top): happens once (and again when docs change). Turns
  documents into searchable vectors.
- **Query** (bottom): happens on every question. Finds relevant chunks, the LLM
  writes the answer, the response carries the citation.

---

## 5. The end-to-end flow (flowchart)

```
         START
           │
           ▼
  ┌───────────────────┐
  │ 1. Put documents  │  (PDF/TXT/MD/HTML/DOC/CSV...)
  │    in an S3 bucket│
  └─────────┬─────────┘
            ▼
  ┌───────────────────┐
  │ 2. Create IAM     │  role Bedrock assumes to read S3,
  │    service role   │  call the embedding model, write vectors
  └─────────┬─────────┘
            ▼
  ┌───────────────────┐
  │ 3. Pick chunking  │  fixed_size / semantic / hierarchical
  │    + vector store │  (S3 Vectors = simplest)
  └─────────┬─────────┘
            ▼
  ┌───────────────────┐
  │ 4. Create the     │  embedding model = titan-embed-text-v2
  │    Knowledge Base │
  └─────────┬─────────┘
            ▼
  ┌───────────────────┐
  │ 5. Create the     │  points KB at your S3 bucket/prefix
  │    data source    │
  └─────────┬─────────┘
            ▼
  ┌───────────────────┐
  │ 6. Run ingestion  │  chunk → embed → index
  │    job, poll till │  (poll until COMPLETE)
  │    COMPLETE        │
  └─────────┬─────────┘
            ▼
  ┌───────────────────┐    no results
  │ 7. Verify:        │────────────────┐
  │    test retrieve  │                │
  └─────────┬─────────┘                ▼
            │ results OK         check ingestion,
            ▼                    query relevance,
  ┌───────────────────┐          vector store config
  │ 8. Connect to     │
  │    agent / app    │
  └─────────┬─────────┘
            ▼
  ┌───────────────────┐
  │ 9. Ask a question │  → answer + citation (THE PROOF)
  └─────────┬─────────┘
            ▼
          DONE
```

---

## 6. Prerequisites

- **AWS CLI** installed and configured (`aws sts get-caller-identity` works).
- **Model access enabled** in Bedrock for:
  - an **embedding model** (default `amazon.titan-embed-text-v2:0`), and
  - a **generation model** (e.g. an Anthropic Claude model) for the answer.
  - Check: `aws bedrock list-foundation-models --region <region>`
- Some **documents** to ingest (PDF, TXT, MD, HTML, DOC/DOCX, CSV, XLS/XLSX).
- A **region** that supports Bedrock KBs (e.g. `us-west-2` or `us-east-1`).

> Supported S3 file formats: PDF, TXT, MD, HTML, DOC, DOCX, CSV, XLS, XLSX.
> Unsupported formats are silently skipped during ingestion.

---

## 7. Step-by-step with commands

Replace `<...>` placeholders. Example values: region `us-west-2`,
embedding model `amazon.titan-embed-text-v2:0`.

### Step 1 - Put your documents in S3

```bash
aws s3 mb s3://my-kb-docs --region us-west-2
aws s3 cp ./my-docs/ s3://my-kb-docs/docs/ --recursive
aws s3 ls s3://my-kb-docs/docs/          # confirm they are there
```

### Step 2 - Choose chunking + vector store (decisions)

- **Chunking** (how docs get split). *Irreversible after data-source creation.*
  - `fixed_size` - FAQs/short uniform docs (simple; 200-300 tokens, 10-20% overlap).
  - `semantic` - long technical docs (better quality, slower).
  - `hierarchical` - structured docs with chapters/sections (best, most complex).
  - If your docs have **tables/complex figures**, enable **advanced parsing
    (FM-based)** so tables aren't broken across chunks.
- **Vector store** (where the number-fingerprints live):
  - **S3 Vectors** = simplest, AWS-managed, Bedrock can auto-create. Good default.
  - **OpenSearch Serverless** = most common for production, advanced filtering
    (create collection -> create index -> then create KB, in that order).

For this challenge, pick **`fixed_size`** chunking + **S3 Vectors** unless your
docs are long/structured.

### Step 3 - Create the IAM service role

The role lets Bedrock (a) read your S3 docs, (b) call the embedding model, (c)
write/read the vector store. Trust policy (who may wear the badge):

```bash
aws iam create-role \
  --role-name AmazonBedrockExecutionRoleForKB-mykb \
  --assume-role-policy-document '{
    "Version":"2012-10-17",
    "Statement":[{
      "Effect":"Allow",
      "Principal":{"Service":"bedrock.amazonaws.com"},
      "Action":"sts:AssumeRole",
      "Condition":{
        "StringEquals":{"aws:SourceAccount":"<account-id>"},
        "ArnLike":{"aws:SourceArn":"arn:aws:bedrock:us-west-2:<account-id>:knowledge-base/*"}
      }
    }]
  }'
```

Model-invocation permission (call the embedding model):

```bash
aws iam put-role-policy \
  --role-name AmazonBedrockExecutionRoleForKB-mykb \
  --policy-name BedrockModelInvocation \
  --policy-document '{
    "Version":"2012-10-17",
    "Statement":[
      {"Effect":"Allow","Action":["bedrock:ListFoundationModels","bedrock:ListCustomModels"],"Resource":"*"},
      {"Effect":"Allow","Action":["bedrock:InvokeModel"],
       "Resource":["arn:aws:bedrock:us-west-2::foundation-model/amazon.titan-embed-text-v2:0"]}
    ]
  }'
```

Data-source permission (read your S3 bucket):

```bash
aws iam put-role-policy \
  --role-name AmazonBedrockExecutionRoleForKB-mykb \
  --policy-name S3DataSource \
  --policy-document '{
    "Version":"2012-10-17",
    "Statement":[{"Effect":"Allow","Action":["s3:GetObject","s3:ListBucket"],
      "Resource":["arn:aws:s3:::my-kb-docs","arn:aws:s3:::my-kb-docs/*"]}]
  }'
```

Plus **vector-store permission** matching your choice (e.g. for S3 Vectors:
`s3vectors:PutVectors/GetVectors/QueryVectors/...` on the vector index ARN; for
OpenSearch Serverless: `aoss:APIAccessAll` on the collection ARN).

> IAM is eventually consistent - after creating the role, wait a few seconds; if
> KB creation says it can't assume the role, retry with backoff.

### Step 4 - Create the Knowledge Base

```bash
aws bedrock-agent create-knowledge-base \
  --name mykb \
  --role-arn arn:aws:iam::<account-id>:role/AmazonBedrockExecutionRoleForKB-mykb \
  --knowledge-base-configuration '{"type":"VECTOR","vectorKnowledgeBaseConfiguration":{"embeddingModelArn":"arn:aws:bedrock:us-west-2::foundation-model/amazon.titan-embed-text-v2:0"}}' \
  --storage-configuration '<storage-config-for-your-vector-store>'
```

Note the returned **knowledge base ID** (`<kb-id>`).

### Step 5 - Create the data source (point it at S3)

```bash
aws bedrock-agent create-data-source \
  --knowledge-base-id <kb-id> \
  --name mykb-s3 \
  --data-source-configuration '{"type":"S3","s3Configuration":{"bucketArn":"arn:aws:s3:::my-kb-docs","inclusionPrefixes":["docs/"]}}' \
  --vector-ingestion-configuration '{"chunkingConfiguration":{"chunkingStrategy":"FIXED_SIZE","fixedSizeChunkingConfiguration":{"maxTokens":300,"overlapPercentage":20}}}'
```

Note the returned **data source ID** (`<ds-id>`).

### Step 6 - Run the ingestion job (and wait)

```bash
aws bedrock-agent start-ingestion-job \
  --knowledge-base-id <kb-id> --data-source-id <ds-id>

# poll until status is COMPLETE (or FAILED)
aws bedrock-agent get-ingestion-job \
  --knowledge-base-id <kb-id> --data-source-id <ds-id> \
  --ingestion-job-id <job-id>
```

> Do NOT query before this says COMPLETE - you'll get empty results. If FAILED:
> check S3 permissions, file formats, and that the vector index dimension
> matches the embedding model.

### Step 7 - Verify retrieval

```bash
# just the matching chunks:
aws bedrock-agent-runtime retrieve \
  --knowledge-base-id <kb-id> \
  --retrieval-query '{"text":"<a question your docs answer>"}'
```

### Step 8 + 9 - Connect to the agent and get the cited answer (THE PROOF)

The simplest "agent" is the managed RAG call `RetrieveAndGenerate` - it
retrieves, asks the LLM, and returns the answer **with citations**:

```bash
aws bedrock-agent-runtime retrieve-and-generate \
  --input '{"text":"<your question>"}' \
  --retrieve-and-generate-configuration '{
    "type":"KNOWLEDGE_BASE",
    "knowledgeBaseConfiguration":{
      "knowledgeBaseId":"<kb-id>",
      "modelArn":"arn:aws:bedrock:us-west-2::foundation-model/<claude-model-id>"
    }
  }'
```

If you built a full **Bedrock Agent**, attach the KB to the agent (associate the
knowledge base with the agent, prepare the agent, create an alias) and call
`invoke-agent` - the agent decides when to query the KB and returns citations
the same way.

---

## 8. Where the "source document cited" proof comes from

The `retrieve-and-generate` response has this shape (trimmed):

```json
{
  "output": { "text": "Oakland residents with past-due rent can get help from..." },
  "citations": [
    {
      "generatedResponsePart": { "textResponsePart": { "text": "..." } },
      "retrievedReferences": [
        {
          "content": { "text": "The Keep People Housed - Oakland program provides..." },
          "location": { "type": "S3", "s3Location": { "uri": "s3://my-kb-docs/docs/bacs.pdf" } }
        }
      ]
    }
  ]
}
```

- `output.text` = the **answer**.
- `citations[].retrievedReferences[].location.s3Location.uri` = the **source
  document** (e.g. `s3://my-kb-docs/docs/bacs.pdf`).
- `citations[].retrievedReferences[].content.text` = the exact snippet used.

**Your proof = a screenshot/log of the question, the `output.text` answer, and
the `s3Location.uri` of the cited document.** In the Bedrock console's KB "Test"
panel, citations show as numbered footnotes you can click - screenshot that.

---

## 9. Common mistakes (and how to avoid them)

| Mistake | Symptom | Fix |
|---------|---------|-----|
| Querying before ingestion finishes | Empty results | Poll `get-ingestion-job` until COMPLETE |
| Missing S3 permission on the role | Ingestion FAILED | Add `s3:GetObject` + `s3:ListBucket` |
| Unsupported file format | Doc silently missing | Use PDF/TXT/MD/HTML/DOC/CSV/XLS |
| Embedding dim != index dim | Ingestion FAILED | Match vector index dimension to the embedding model |
| Using role before IAM propagates | "cannot assume role" | Wait and retry with backoff |
| Wrong API for the goal | No citations | Use `retrieve-and-generate` (not plain `retrieve`) for answers+citations |
| Changing chunking later | Not allowed | Chunking is fixed at data-source creation; delete + recreate to change |

---

## 10. Security notes (good to mention in an interview)

- **PII/PHI**: once ingested, sensitive text lives in the vector store and comes
  back in `retrievedReferences`. Redact **before** ingesting; apply **guardrails**
  on the generated answer; use **metadata filtering** for per-user doc access.
- **Least privilege**: the KB service role should only reach the specific bucket,
  the specific embedding model, and the specific vector store.
- **Encryption**: customer-managed KMS keys can encrypt ingestion temp data, the
  vector store, source S3 data, and retrieval session data.
- **Audit**: `Retrieve`/`RetrieveAndGenerate` are CloudTrail **data events** (not
  logged by default) - enable them to audit who queried what.

---

## 11. Interview-ready summary (say this)

"I built a RAG pipeline on Amazon Bedrock. I stored source documents in S3,
created a Knowledge Base with an IAM service role, chose a chunking strategy and
a vector store, and ran an ingestion job that chunked the docs, embedded them
with Titan, and indexed the vectors. At query time I used `RetrieveAndGenerate`:
it embeds the question, does a similarity search to pull the most relevant
chunks, and an Anthropic model writes an answer grounded in them. The response
includes citations - each with the source S3 URI - so every answer is traceable
to the document it came from. That citation is the proof the answer is grounded,
not hallucinated."

---

## 12. One-page cheat sheet (the 9 commands)

```bash
# 1. docs to S3
aws s3 mb s3://my-kb-docs --region us-west-2
aws s3 cp ./my-docs/ s3://my-kb-docs/docs/ --recursive
# 2-3. IAM role (create-role + put-role-policy x3)  [see Step 3]
# 4. KB
aws bedrock-agent create-knowledge-base --name mykb --role-arn <role> \
  --knowledge-base-configuration '<...titan...>' --storage-configuration '<...>'
# 5. data source
aws bedrock-agent create-data-source --knowledge-base-id <kb-id> --name mykb-s3 \
  --data-source-configuration '<...S3...>' --vector-ingestion-configuration '<...chunking...>'
# 6. ingest + poll
aws bedrock-agent start-ingestion-job --knowledge-base-id <kb-id> --data-source-id <ds-id>
aws bedrock-agent get-ingestion-job   --knowledge-base-id <kb-id> --data-source-id <ds-id> --ingestion-job-id <job-id>
# 7. verify
aws bedrock-agent-runtime retrieve --knowledge-base-id <kb-id> --retrieval-query '{"text":"..."}'
# 8-9. cited answer (THE PROOF)
aws bedrock-agent-runtime retrieve-and-generate --input '{"text":"..."}' \
  --retrieve-and-generate-configuration '{"type":"KNOWLEDGE_BASE","knowledgeBaseConfiguration":{"knowledgeBaseId":"<kb-id>","modelArn":"<claude-arn>"}}'
```
