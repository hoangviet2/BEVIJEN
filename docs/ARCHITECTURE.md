# Resource Engine: AWS architecture

Account `364103436311`, region `us-west-2`. Diagrams are Mermaid; they render on GitHub and in VS Code (Markdown Preview Mermaid).

## 1. AWS services map

```mermaid
flowchart LR
  subgraph EXT["External"]
    ODRS["ODRS API<br/>rbg.1degree.org:3002"]
    NDP["NDP feed"]
    WEB["Provider websites"]
    USER["Reviewer / Frontend"]
  end

  subgraph EC2["EC2 t3.large  ·  EIP 52.41.36.135  ·  52-41-36-135.sslip.io"]
    CADDY["Caddy<br/>HTTPS / Let's Encrypt"]
    API["api<br/>FastAPI: HSDS API, review UI, /tools/*"]
    WORKER["worker<br/>SQS consumer"]
    INGEST["ingest CLI<br/>app.cli ingest"]
  end

  subgraph DATA["Data (all encrypted with KMS CMK alias/re-data)"]
    S3[("S3 team20-resource-engine-364103436311<br/>raw/ · hsds/&lt;source&gt;/")]
    DDB[("DynamoDB<br/>re-hsds · re-findings · re-tasks<br/>re-runs · re-api-keys · index")]
    SQSV[["SQS re-verify-jobs<br/>+ re-verify-dlq"]]
    SQSJ[["SQS re-jobs<br/>+ re-jobs-dlq"]]
    SM["Secrets Manager<br/>ODRS key · tools key · m2m secret"]
    VEC[("S3 Vectors<br/>re-kb-vectors / re-kb-index")]
  end

  subgraph AC["Bedrock AgentCore"]
    VER["Runtime re_verifier_agent<br/>endpoint live · SigV4"]
    DISC["Runtime re_discovery_agent<br/>endpoint live · Cognito JWT"]
    GW["Gateway re-gateway (MCP)<br/>AWS_IAM + Cedar policy engine"]
    ID["Identity<br/>API-key provider · JWT inbound"]
    MEM["Memory re_org_notes"]
    BR["Browser"]
  end

  subgraph TOOLS["Gateway targets"]
    T1["re-tools  →  EC2 /tools/*"]
    T2["web-tools  →  Lambda re-web-tools"]
    T3["assist-tools  →  Lambda re-web-tools<br/>search_knowledge · translate_text · send_message"]
    T4["web-search connector"]
  end

  BEDROCK["Bedrock models<br/>Claude Sonnet 4.5 · Titan embed v2"]
  COG["Cognito user pool<br/>hosted login + M2M client"]
  SCHED["EventBridge Scheduler<br/>crawl 15 min · weekly discovery"]
  OBS["CloudWatch<br/>logs · Transaction Search spans · alarms → SNS"]
  MSG["SES email · End User Messaging SMS"]
  TR["Amazon Translate"]

  USER -->|HTTPS| CADDY --> API
  USER -->|login| COG
  ODRS --> INGEST
  NDP --> INGEST
  INGEST --> S3
  INGEST --> DDB
  INGEST -->|enqueue| SQSV
  SCHED --> SQSJ --> WORKER
  SQSV --> WORKER
  API <--> S3
  API <--> DDB
  WORKER -->|invoke| VER
  WORKER -->|JWT from Cognito| DISC
  DISC -.inbound auth.-> ID
  VER --> BEDROCK
  DISC --> BEDROCK
  VER --> GW
  DISC --> GW
  VER --> BR
  DISC --> MEM
  GW --> T1 & T2 & T3 & T4
  T1 -->|x-api-key via Identity| CADDY
  T3 --> VEC
  T3 --> TR
  T3 --> MSG
  T3 --> BEDROCK
  T2 --> WEB
  SM -.-> API
  SM -.-> ID
  AC -.spans/logs.-> OBS
  EC2 -.logs.-> OBS
```

## 2. Ingest pipeline (one source run)

```mermaid
flowchart TD
  A["Fetch source pages<br/>ODRS API / NDP"] --> B["Save raw pages<br/>S3 raw/&lt;source&gt;/&lt;run&gt;/"]
  B --> C["map<br/>hsds_transform mapper → HSDS 3.2.3 rows"]
  C --> D["normalize<br/>addresses, phones, location_type"]
  D --> E{"validate<br/>HSDS schema"}
  E -- invalid --> Q["quarantine<br/>metric services_quarantined"]
  E -- valid --> F["dedup<br/>derived UUIDv5 ids, match existing"]
  F --> G["commit<br/>merge stored + incoming rows"]
  G --> H[("S3 hsds/&lt;source&gt;/&lt;service_id&gt;.json")]
  G --> I[("DynamoDB re-hsds index")]
  G --> R[("DynamoDB re-runs: run stats")]
  G -->|"enqueue (skipped with no-verify)"| J[["SQS re-verify-jobs"]]
```

## 3. Verification / discovery (one job)

```mermaid
sequenceDiagram
  autonumber
  participant Q as SQS re-verify-jobs
  participant W as worker (EC2)
  participant C as Cognito
  participant R as AgentCore Runtime
  participant B as Bedrock Claude
  participant G as AgentCore Gateway + Cedar
  participant T as Targets (EC2 /tools, Lambda, web-search)
  participant D as DynamoDB re-findings

  Q->>W: job {service_id, run_id}
  alt discovery agent
    W->>C: client_credentials
    C-->>W: access token (JWT)
    W->>R: invoke ?qualifier=live (Bearer JWT)
  else verifier agent
    W->>R: invoke (SigV4)
  end
  loop tool calls (cap 25 verifier / 120 discovery)
    R->>B: converse (prompt + skill)
    B-->>R: tool_use
    R->>G: MCP tools/call (SigV4)
    G->>G: Cedar authorize (agent role → tool)
    G->>T: get_record / fetch_page / check_site / search_knowledge / send_message …
    T-->>R: result
  end
  R-->>W: JSON findings
  W->>D: record findings
  Note over R: spans → CloudWatch Transaction Search (GenAI Observability)
```

## 4. Who owns what

| Layer | Tool |
|---|---|
| KMS, S3, DynamoDB, SQS, IAM roles | Terraform (`infra/terraform/`) |
| Cognito, Secrets, Lambda, AgentCore (Memory, Gateway, Identity, Runtimes), Scheduler, EC2, Observability, alarms | `infra/deploy.sh` (AWS CLI) |
| Shared outputs between the two | `infra/.state.json` |
