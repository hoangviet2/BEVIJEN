# Implementation notes — Resource Engine backend + AI

2026-11-04 · Implements `docs/SystemDesign/RESOURCE-ENGINE-DESIGN.md`, `AGENT.md`, `SKILL.md` on top of the
`feature/hsds` handoff code, the `ingestion/` package and the `transform/` package.

Read next: `backend/backend.md` (server, data, API, AWS, run/deploy) and `ai/ai.md` (agents, tools, Gateway, Policy).

---

## 1. What was done

| Area | Work |
|---|---|
| Refactor | Backend reorganised along design §17: `adapters/`, `hsds/`, `store/`, `verify/`, `tasks/`, `discovery/`, `routers/`, `cli.py`. One write path (`store/commit.py`). The handoff intake now runs on the shared pipeline. Removed `storage.py`, `hsds_schema.py`, `dev_server.py`, `scripts/demo_ingest.py`. |
| HSDS | Official 3.2.3 JSON Schema validation (shared pinned copy in `transform/hsds_transform/schema/`); single-table DynamoDB storage with S3 document that must equal the DynamoDB rebuild; HSDS `metadata` change history; schedule offsets per response date (DST). |
| Pipeline A | NDP sample / S3 / mock API and ODRS API / file adapters reusing `ndp_ingestion`; raw archive; mapping via `hsds_transform`; documented quirk fixes; quarantine; dedup; reconciliation (`clash`); pre-flags; verify jobs; run counts + CloudWatch metrics. |
| Pipeline B | Flag crawler with conditional dedupe, ZIP / county / virtual buckets (Amazon Location reverse geocode), leases, resolve outcomes, escalation, memory notes. |
| Pipeline C | Discovery runs, `match_existing`, fenced `create_record`, independent-check publish gate, publish/reject. |
| AI | Verifier and Discovery agents (Strands + Claude on Bedrock, AgentCore Runtime), shared runtime with tool budget and SigV4 Gateway client, web tools (Lambda + local), AgentCore Browser, Web Search connector, Memory, Cedar policies, Identity API-key provider, Observability instrumentation. |
| API | Every endpoint in design §9 (HSDS API, ingest, records, verify, findings, tasks, discovery, tools, ops), Cognito JWT, consumer API keys (hashed + pepper), tool key. |
| AWS | `infra/deploy.sh` (AWS CLI) for KMS, S3, DynamoDB, SQS, Secrets Manager, IAM, Cognito, Lambda, AgentCore Memory/Gateway/Policy/Runtime, EventBridge Scheduler, OpenSearch, ECR, EC2 + SSM, CloudWatch alarms. Least-privilege IAM documents in `infra/iam/`. |
| Tests | 86 pytest tests, no AWS needed (memory fakes + moto). `ingestion/` and `transform/` suites unchanged and passing. |
| Docs | `backend/backend.md`, `ai/ai.md`, this file, `backend/README.md`; `.gitignore` updated. |

---

## 2. Decisions and deviations from the design

1. **Repository layout.** `agents/`, `gateway/`, `lambdas/`, `infra/` live under `backend/` (design §17 shows them at the root) so the backend is one deployable unit and the in-process agent mode can import them.
2. **Gateway inbound auth is `AWS_IAM`, not `CUSTOM_JWT`.** Each agent signs with its AgentCore Runtime execution role, which becomes the Cedar principal (`AgentCore::IamEntity`). This needs no Cognito M2M client and gives per-agent principals for free. The design's Cedar sketch used `AgentCore::Agent::…`; the documented entity types are `AgentCore::IamEntity` / `AgentCore::OAuthUser`.
3. **`create_record` caller check.** The Gateway can't forward which agent called an OpenAPI target, so the server can't see the agent name. Cedar (only discovery-agent's role, only `run_id like "dr-*"`) is the primary fence; the server refuses the verifier when identified and requires a *running* discovery run, plus all other fences.
4. **Transactions.** `TransactWriteItems` is capped at 100 items and organization graphs are larger, so `commit()` uses batch writes and writes the index item last; commits are idempotent and safe to retry. Single-item edits use an optimistic version condition.
5. **No auto-verify.** The handoff marked complete, non-duplicate records `verified` at intake. Now everything stays `unverified` until the Verifier Agent or a person confirms it (design §5). With `AGENT_MODE=off` records stay unverified and `last_check_category=skipped`.
6. **Values derived during normalisation** (all reported as fixes): taxonomy / taxonomy-term descriptions from their names (HSDS requires them; sources don't have them); ODRS `country=US` from a US state code and `address_type=physical` (ODRS is a US directory of physical sites); `location_type` from whether the location has an address / map point.
7. **ZIP buckets for "Various Locations".** Design §6.1 says postal ZIP before county, but §11 expects these rows in `COUNTY#…`; their only address is the org's PO box. Locations named "Various Locations" skip the mailing ZIP and go to the county bucket.
8. **PATCH /records** uses a JSON-Patch-lite on flat HSDS objects (`/<table>/<id>/<field>`) rather than JSON Patch on the nested document, so every edit maps to exactly one stored item.
9. **Memory fallback.** Without `AGENTCORE_MEMORY_ID`, organization notes are kept in `re-findings` (`PK=ORG#…`).
10. **Web Search region.** The installed `bedrock-agentcore` SDK lists only us-east-1 / eu-west-1 / ap-northeast-1, but the current AgentCore docs list us-west-2; the design's region is kept. Confirm in the account (design open question 4).
11. **Web Search terms.** The connector's acceptable-use terms require keeping citations and forbid bulk-populating a database from search results. Discovery keeps every evidence URL and creates at most `max_creates` records per run, but One Degree should confirm discovery fits those terms.
12. **Generated files tracked.** `infra/dynamodb/*.json` and `gateway/openapi-tools.json` are generated (`python -m app.cli infra-specs | gateway-openapi`) and committed; tests fail if they drift. `backend/transform/work/` (generated sample conversions) is no longer tracked; the files stay on disk.

---

## 3. Status of verification

| Verified | How |
|---|---|
| Pipeline on both NDP samples and the ODRS export | `tests/test_pipeline.py` (counts, validity, quirks, idempotency) |
| DynamoDB/S3/SQS code paths | `tests/test_aws_backend.py` on moto, same table specs as infra |
| API, auth, roles, tasks, discovery fences, verdict handling | `tests/test_api.py`, `test_tasks.py`, `test_discovery.py`, `test_verify.py` |
| Agent wiring that runs without Bedrock | `tests/test_agents.py` (tools, Lambda dispatch, prompts, Cedar fence, budget hook) |
| AWS API parameter shapes | checked against boto3 1.43 service models and current AgentCore docs |

| **Not yet run** | Needs |
|---|---|
| `infra/deploy.sh` end-to-end | AWS account (AWS CLI is not installed on the dev machine used here) |
| Real agent runs (Bedrock, Runtime, Gateway, Policy validation, Browser, Web Search, Memory) | deployed stack + model access |
| Live ODRS staging / NDP mock pulls | ODRS key in Secrets Manager; NDP mock URL |
| Cognito login flow | user pool + HTTPS host |

---

## 4. Open items

1. `scripts/eval_verifier.py` and the seeded `samples/websites/` set (design §13) — the tool side (`WEBSITE_SOURCE=sample`) is ready; the seed content and scoring script are not written.
2. Specialist UI (`/ui`, design §6.2) — the API is complete; the React app belongs to the frontend.
3. Confirm with Steffi / Craig: agent-created records published after an independent verifier pass (default) is acceptable (design open question 1).
4. NDP mock contract (URL, auth, `skip`/`take`) — `ndp_ingestion`'s `sample-offset` profile is still provisional.
5. Rotate the ODRS staging key that was shared in chat (design §12.2).
6. NDP sample 1: the design expected "Zzz_ Holiday Programs" and a null-id service-at-location to be quarantined (20 received → 19 stored). The transform package attaches that service to its organization and drops the null-id link, so all 20 are stored (the "Zzz_" one as `inactive`). Decide whether "Zzz_"-prefixed (hidden) 211 services should be quarantined.
7. Search index updates are a full rebuild (`python -m app.cli search-rebuild`); incremental indexing on commit is not wired.
8. ECS Fargate move (design §14.10): the same image runs as `api` and `worker`; task definitions are not written.
