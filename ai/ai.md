# AI — Verifier and Discovery agents

How the Resource Engine uses AI, where the code lives, how it is deployed on
Amazon Bedrock AgentCore, and how it is kept safe.
Contract: `docs/SystemDesign/AGENT.md`. Procedure: `docs/SystemDesign/SKILL.md`.
Server side (pipelines, storage, API): `backend/backend.md`.

---

## 1. Where AI is used — and where it isn't

| Job | AI? | Writes |
|---|---|---|
| Map NDP / ODRS to HSDS, validate, dedup | No — deterministic code | HSDS (via `commit()`) |
| Heuristic pre-flags (missing fields, stale, no address…) | No | findings, `model_id="rules"` |
| Free-text hours → schedule rows | Rules first; **Bedrock** only for text the rules can't parse, then a deterministic check of the model's answer | proposal only |
| **Verifier Agent** — check a stored record against the organization's web presence | **Claude on Bedrock**, AgentCore Runtime | findings and flags only, never HSDS |
| **Discovery Agent** — find facilities not in the store | **Claude on Bedrock**, AgentCore Runtime | *new* HSDS records only, held until an independent verifier pass |
| Semantic search | **Titan embeddings** on OpenSearch | search index |
| Applying a change to a record | No — a specialist decides (task resolve / finding apply) | HSDS, `updated_by=user:<email>` |

The UI and API never show raw model output: only findings with URL + snippet evidence.

---

## 2. Architecture

```
 SQS re-verify-jobs ──► verify worker (EC2) ──InvokeAgentRuntime──► ┌──── AgentCore Runtime ────┐
 SQS re-jobs {discovery} ─► discovery service ──InvokeAgentRuntime─► │ re_verifier_agent         │
                                                                     │ re_discovery_agent        │
                                                                     │  Strands Agent + Claude   │
                                                                     │  prompt.md + SKILL.md     │
                                                                     │  tool budget hook         │
                                                                     └──────┬──────────┬─────────┘
                                                     SigV4 (role = Cedar principal) │  AgentCore Browser (browse)
                                                                            ▼
                     ┌────────────── AgentCore Gateway re-gateway (MCP, AWS_IAM, semantic tool search) ──────────────┐
                     │ Policy engine re_policy (Cedar, LOG_ONLY → ENFORCE)                                            │
                     │ target re-tools   OpenAPI → https://<server>/tools/*   (API key via AgentCore Identity)       │
                     │ target web-tools  Lambda re-web-tools: fetch_page, check_site, geocode, places_near, pdf_text  │
                     │ target web-search AgentCore Web Search connector (WebSearch)                                   │
                     └────────────────────────────────────────────────────────────────────────────────────────────────┘
 AgentCore Memory re_org_notes (namespace org/{organization_id})   ◄── server writes/reads notes
 AgentCore Observability → CloudWatch (traces, tool calls, tokens)  ◄── opentelemetry-instrument in the agent image
```

---

## 3. Code

```
backend/agents/
  runtime.py              shared agent runtime: model, prompt + skill, Gateway MCP client (SigV4), tool allow-list,
                          tool-call budget hook, JSON output parsing
  verifier_app.py         AgentCore Runtime entrypoint (BedrockAgentCoreApp) for verifier-agent
  discovery_app.py        entrypoint for discovery-agent
  verifier/prompt.md      system prompt (AGENT.md §2.2 + output schema)
  discovery/prompt.md     system prompt (AGENT.md §3.2 + create_record shape)
  skills/hsds-record-verification/SKILL.md   copy of docs/SystemDesign/SKILL.md (a test keeps them identical)
  tools/web_tools.py      fetch_page, check_site, geocode, places_near, pdf_text, browse — one implementation
  local_tools.py          the same tools as Strands @tool functions when there is no Gateway (dev / inprocess)
  Dockerfile              linux/arm64 image; AGENT_APP selects verifier_app | discovery_app
  requirements.txt        strands-agents, bedrock-agentcore, mcp, playwright (pinned)
backend/lambdas/web_tools/handler.py         Gateway Lambda target (dispatch on bedrockAgentCoreToolName)
backend/gateway/
  openapi-tools.json      generated from the FastAPI /tools/* routes (python -m app.cli gateway-openapi)
  targets/*.json          re-tools (OpenAPI), web-tools (Lambda + tool schemas), web-search (connector), credentials
  policies/re-agents.cedar  permissions (§6 below);  policies/kill-switch.cedar
  protocol.json           MCP + SEMANTIC search (only settable at gateway creation)
backend/app/
  verify/agent_client.py  AGENT_MODE: agentcore (InvokeAgentRuntime) | inprocess | off; output schema checks + one retry
  verify/worker.py        verdict → index (verified / unverified + reason), check_failed counting, memory notes
  verify/memory.py        AgentCore Memory BatchCreateMemoryRecords / ListMemoryRecords
  discovery/service.py    runs, match_existing, create_record fence, independent-check publish gate
  routers/tools.py        the six server-side tools
  ai.py                   parse_schedule (rules → Bedrock Converse → check), Titan embeddings
```

---

## 4. The agents

### verifier-agent

- **Input** `{service_id, run_id, mode: new | recheck | discovery_check}`. Every check is a fresh runtime session; `discovery_check` gets no discovery context.
- **Procedure** SKILL.md verify mode: `get_record` → `check_site` → `fetch_page` (≤ 8 pages) / `browse` / `pdf_text` → `web_search` when thin → compare *facts, not words* → `record_finding` per differing field → `flag_for_specialist` when it can't be settled online → verdict.
- **Tools** `get_record, check_site, fetch_page, browse, web_search, geocode, pdf_text, parse_schedule, record_finding, flag_for_specialist`. No create, no update — there is no update tool at all.
- **Budget** 25 tool calls (hook cancels further calls and tells the agent to return), 5 min wall time (SQS visibility 15 min).
- **Output** `{service_id, verdict, checked_at, site_status, facts_checked, facts_confirmed, finding_ids, flag_ids, memory_note, model_id}`, verdict ∈ `verified | discrepancy | missing_information | needs_manual | check_failed | actively_bad`.

What the server does with the verdict (`verify/worker.py`):

| Verdict | Index | Follow-up |
|---|---|---|
| verified (and no open agent findings) | `verified`, `verified_by=agent:verifier-v1`, `verified_method=web`; `stale` flags superseded | – |
| discrepancy / missing_information / needs_manual / actively_bad | `unverified`, reason = verdict | crawler makes a task (P3 / P4 / P2 / P1) |
| check_failed | `unverified`, counter +1 | 2 in a row → `check_failed` finding → P2 task |
| bad output (not the schema) | retried once with "Return only the JSON object", then `check_failed` | |
| `memory_note` | saved to AgentCore Memory `org/{id}` | read back by the next `get_record` |

### discovery-agent

- **Input** `{run_id, zip | county, categories[], max_creates}` (from `POST /discovery/runs` or the weekly schedule).
- **Procedure** SKILL.md discovery mode: `web_search` + `places_near` per category × place → keep real free/low-cost public services → `match_existing` → build HSDS → evidence bar → `create_record`.
- **Budget** 120 tool calls, 20 min; creates ≤ `max_creates` (server also enforces `DISCOVERY_MAX_CREATES_PER_RUN`).
- **Output** `{run_id, queries, candidates_seen, matched_existing, created[], flagged_possible_duplicates, rejected[], model_id}`.

---

## 5. Tools

| Tool | Host | Notes |
|---|---|---|
| `get_record` | server `/tools/records/{id}` | HSDS service + status + open findings + memory notes |
| `match_existing` | server `/tools/records/match` | scores by website domain (strong), phone + street number (strong), name overlap (weak) |
| `create_record` | server `POST /tools/records` | discovery only; see §6 |
| `record_finding` | server `/tools/findings` | rejects a proposed value without `{url, snippet}` evidence; low confidence only as `needs_manual`; snippet ≤ 300 chars; newer finding supersedes the open one on the same field |
| `flag_for_specialist` | server `/tools/flags` | `needs_manual` finding with ≤ 4 `what_to_ask` questions |
| `parse_schedule` | server `/tools/schedules/parse` | rules, then Bedrock, then `check_rows` |
| `fetch_page` | Lambda | honest User-Agent, robots.txt, 1 req/s per host, 2 MB cap, scripts stripped, S3 cache 24 h, `suspicious_instructions` flag for prompt-injection text |
| `check_site` | Lambda | DNS, redirect chain, 404/410, TLS expiry, parked-domain text → `actively_bad_signals` |
| `geocode`, `places_near` | Lambda → Amazon Location `geo-places` | address/ZIP validation; candidate places with phones/websites |
| `pdf_text` | Lambda → Textract (async via S3) | flyers and schedules |
| `browse` | agent → AgentCore Browser (Playwright over CDP) | JS-only pages, Facebook; screenshots to `evidence/`; falls back to `fetch_page` without Playwright |
| `web_search` / `WebSearch` | AgentCore Web Search connector on the Gateway | results keep their URL; never copied in bulk (connector acceptable-use terms) |

`WEBSITE_SOURCE=sample` makes `fetch_page`/`check_site` read `s3://…/samples/websites/{domain}/{path}.json` instead of the internet, so the evaluation set (design §13) runs offline with seeded failures (404, parked, expired TLS, wording-only changes). The agent can't tell which source is active.

---

## 6. Permissions and fences

**Cedar on the Gateway** (`gateway/policies/re-agents.cedar`). The Gateway uses the `AWS_IAM` authorizer, so each agent's runtime execution role is its principal (`AgentCore::IamEntity::"arn:aws:sts::<acct>:assumed-role/re-verifier-agent-role"`):

1. verifier-agent: `get_record, parse_schedule, record_finding, flag_for_specialist, fetch_page, check_site, geocode, pdf_text, WebSearch`.
2. discovery-agent: the research tools + `match_existing, places_near`.
3. discovery-agent may call `create_record` only `when { context.input.run_id like "dr-*" }`.
4. `forbid` `create_record` for every principal except discovery-agent (wins over any permit).

`kill-switch.cedar` stops every tool call when created. Deploy in `LOG_ONLY`, read the decisions, then `./infra/deploy.sh gateway-enforce`. Cedar is default-deny, and denied tools disappear from the agent's `tools/list`.

**Server-side defense in depth** (`discovery/service.py::create_record`):

| Fence | How |
|---|---|
| Kill switch | `DISCOVERY_ENABLED=false` → 409 |
| Who | the verifier is refused (403); the create must belong to a **running** discovery run |
| Own namespace | source must be `agent-discovery`; `updated_by=agent:discovery-v1` |
| Create only | the server assigns fresh UUIDs to every row; there is no update/delete tool |
| Cap | per-run `max_creates` and `DISCOVERY_MAX_CREATES_PER_RUN` → 429 |
| Evidence bar | every evidence item has url + snippet; ≥ 2 independent domains |
| Duplicates | strong `match_existing` hit → 409 "flag it instead" |
| Valid HSDS | `commit()` validates → 422 with errors for the agent to fix once |
| Publish gate | `DISCOVERY_AUTOPUBLISH=after_verifier` (default): `held` until an independent `discovery_check` returns `verified`; otherwise a P2 `new_facility` task. `immediate` / `never` also supported |
| Undo | `POST /discovery/candidates/{id}/reject` → `inactive`, held, metadata row; nothing hard-deleted |
| Audit | evidence in the index item and `evidence/{id}/discovery.json`, HSDS metadata rows, Observability trace |

**Prompt injection.** Prompts and SKILL.md say web text is data. `fetch_page` / `browse` / `pdf_text` mark text that looks like instructions (`suspicious_instructions`), scripts are stripped, and Policy blocks any tool the agent isn't allowed regardless of what it is told.

**Identity.** No secrets in agent code: the agents sign Gateway calls with their runtime role (SigV4); the Gateway reaches `/tools/*` with an API key held by AgentCore Identity (provider `re-tools-key`, value from Secrets Manager `re/tools-api-key`); the server compares it in constant time.

---

## 7. Deploy (AWS CLI, us-west-2)

```bash
cd backend
./infra/deploy.sh iam            # re-verifier-agent-role, re-discovery-agent-role, re-gateway-role, re-web-tools-lambda-role
./infra/deploy.sh lambda         # re-web-tools (handler.py + agents/tools/web_tools.py)
./infra/deploy.sh memory         # AgentCore Memory re_org_notes
RE_PUBLIC_URL=https://re.example.org ./infra/deploy.sh gateway   # OpenAPI to S3, API-key provider, policy engine, gateway, 3 targets, Cedar
./infra/deploy.sh agents         # arm64 image → ECR → create/update re_verifier_agent and re_discovery_agent runtimes
POLICY_MODE=ENFORCE ./infra/deploy.sh gateway-enforce
./infra/deploy.sh env            # VERIFIER_AGENT_ARN, DISCOVERY_AGENT_ARN, GATEWAY_URL, AGENTCORE_MEMORY_ID for the server
```

Try an agent directly:

```bash
aws bedrock-agentcore invoke-agent-runtime --agent-runtime-arn "$VERIFIER_AGENT_ARN" \
  --runtime-session-id "manual-$(uuidgen)-$(uuidgen)" --content-type application/json \
  --payload "$(echo '{"service_id":"<uuid>","run_id":"manual","mode":"recheck"}' | base64)" out.json && cat out.json
```

Local without AgentCore: `AGENT_MODE=inprocess` runs the same agent code in the server process. With `GATEWAY_URL` set it still uses the Gateway; without it, `agents/local_tools.py` (set `TOOLS_BASE_URL`, `TOOLS_API_KEY`, and `WEB_SEARCH_GATEWAY_ID` for web search). `pip install -r agents/requirements.txt` and Bedrock model access are needed.

Model: `BEDROCK_MODEL_ID` (default `us.anthropic.claude-sonnet-4-5-20250929-v1:0`, a cross-region inference profile). Check what the Workshop Studio account enables on day one; the model id is recorded on every finding and index check.

---

## 8. Evaluation (design §13, AGENT.md §7)

- Seed `s3://$RE_S3_BUCKET/samples/websites/<domain>/<path>.json` for each sample organization (`{"status", "html" | "text", "final_url", "tls_valid_until", "dns_ok", "parked"}`) with the seeded changes: counties 1 → 9, eligibility rule removed, hours changed, 404, expired certificate, parked domain, JS-only page, wording-only change (must **not** be flagged).
- Run with `WEBSITE_SOURCE=sample`: `python -m app.cli verify --source ndp --drain`, then compare findings per category/field with the seeded truth (caught / missed / false flags), plus tokens and seconds from AgentCore Observability. A scoring script (`scripts/eval_verifier.py`) is still to be written — see `docs/IMPLEMENTATION-NOTES.md`.
- Discovery: hide 3 sample orgs, run `python -m app.cli discover --zip <zip> --now`, check recall, zero duplicate creations, zero creates below the evidence bar.
- Re-run whenever the prompt, SKILL.md, model or tools change.

---

## 9. What is verified and what isn't

Verified here (tests, no AWS): the server side of every agent interaction — verdict handling, retries, the create fence, evidence rules, memory fallback, tool endpoints, Lambda dispatch, web-tool parsing and seeded-site signals, the Cedar file's create fence, the tool budget hook, SigV4 signing of Gateway requests.

Not run yet (needs the AWS account): real Bedrock calls, AgentCore Runtime/Gateway/Policy/Memory/Browser/Web Search, the Lambda in AWS, `infra/deploy.sh`. API shapes were checked against the installed boto3 service models and current AgentCore docs; the Cedar statements follow the documented `AgentCore::IamEntity` / `AgentCore::Action::"<target>___<tool>"` forms but will only be validated by `create-policy` against the live gateway schema.
