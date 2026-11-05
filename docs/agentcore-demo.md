# AgentCore Verifier demo

## What we verified

- Locally, FastAPI imported two provided 211 NDP JSON samples and returned 36 HSDS services. Re-uploading Sample 2 skipped all 16 unchanged services. The total stayed at 36.
- On AWS in Oregon (`us-west-2`), FastAPI imported NDP Sample 1, processed 10 organizations, and inserted 20 HSDS services. We confirmed the original payload and a converted service document in S3 and retrieved Public School Education through the backend API.
- We directly invoked the demo Lambda and confirmed that it read the imported HSDS service from S3.

## Challenge evidence

### Ship It

The deployed Verifier returned **HTTP 200** from AgentCore Runtime using Amazon Nova Lite for the imported **Public School Education** service. The response reported a `verified` verdict. The demo Verifier only had the read-only record tool, so website claims and timestamps in the model output are unverified.

![CloudShell output showing HTTP 200 and the live Verifier response](agentcore-evidence/ship-it.png)

### Gateway Builder

The IAM-authenticated MCP Gateway had two ready Lambda targets and three registered tools. The `ndp-records` target exposed `get_record`. The `web-tools` target exposed `fetch_page` and `check_site`, configured to use a synthetic S3 page. The Verifier called `get_record` and returned the imported service.

![CloudShell listing the three registered MCP tools](agentcore-evidence/gateway-tools.png)

![AgentCore console showing both Gateway targets ready](agentcore-evidence/gateway-targets.png)

### Show Your Work

CloudWatch captured the `show-your-work` request in session `1534e4a6-948d-4248-9873-6b72143dd943`, trace `6aeba02870a02d560afb356954703ca5`. Within the `invoke_agent verifier-agent` span, `execute_tool get_record` reported `success` and returned **Public School Education**. The agent span took 71.84 seconds and the timeline showed repeated record lookups.

![CloudWatch showing the captured Verifier session](agentcore-evidence/cloudwatch-session.png)

![CloudWatch trace showing the Verifier invocation and span tree](agentcore-evidence/cloudwatch-trace.png)

Selected fields from the tool execution span and result:

```json
{
  "span": "execute_tool get_record",
  "gen_ai.tool.status": "success",
  "service_id": "1402495e-ea43-5af8-8d6a-c34d6dadb5a7",
  "returned_service_name": "Public School Education",
  "isError": false
}
```

## Reproduce the demo

1. Deploy the FastAPI backend and upload `backend/ingestion/tests/fixtures/national-data-platform-sample-1.json` to `POST /ingest/ndp/upload`. Pick a service ID from the response and verify it with `GET /services/{service_id}`.
2. Package `backend/lambdas/demo_record_reader/handler.py` as a Python Lambda with entrypoint `handler.handler` and set `RE_S3_BUCKET` to the deployment bucket. Grant the Lambda role `s3:GetObject` for `hsds/ndp/*` in that bucket and `kms:Decrypt` for the data key if it uses SSE-KMS.
3. Register the Lambda with an IAM-authenticated MCP Gateway. Define the tool as `get_record` with an object input containing a required string `service_id`. Grant the Gateway role permission to invoke this Lambda.
4. Build the ARM64 image from `backend/agents` and push it to the deployment account's ECR repository. Configure AgentCore Runtime with `AGENT_APP=verifier_app`, `BEDROCK_MODEL_ID=amazon.nova-lite-v1:0`, `WEBSITE_SOURCE=sample` and the deployment's `GATEWAY_URL`. Grant the Runtime role permission to pull the image, invoke the model and call the Gateway.
5. For the two website tools, package `backend/lambdas/web_tools/handler.py` and `backend/agents/tools/web_tools.py` together as a second Lambda. Set `WEBSITE_SOURCE=sample` and `RE_S3_BUCKET`, upload a synthetic page under `samples/websites/<domain>/index.json`, grant read access, and register `fetch_page` and `check_site` from `backend/gateway/targets/web-tools.json` on the same Gateway.
6. Enable CloudWatch Transaction Search, then invoke the live Runtime with a JSON payload containing `service_id`, `run_id` and `mode: "recheck"`. Capture the request and the returned verdict for the demo.

For this Nova Lite Verifier demo, the model sees the short tool name `get_record`. The adapter forwards it to the name reported by Gateway. This configuration runs without model streaming and exposes only the read-only record tool to the Verifier. Other agent configurations keep their existing tool selection.

The demo uses file uploads of the provided NDP JSON. Live NDP retrieval is unconfigured. The demo Lambda reads stored service documents. It does not fetch websites or persist findings.
