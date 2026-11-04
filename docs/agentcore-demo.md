# AgentCore Verifier demo

## What we verified

- Locally, FastAPI imported two provided 211 NDP JSON samples and returned 36 HSDS services. Re-uploading Sample 2 skipped all 16 unchanged services. The total stayed at 36.
- On AWS in Oregon (`us-west-2`), FastAPI imported NDP Sample 1, processed 10 organizations, and inserted 20 HSDS services. We confirmed the original payload and a converted service document in S3 and retrieved Public School Education through the backend API.
- We directly invoked the demo Lambda and confirmed that it read the imported HSDS service from S3.
- For **Ship It**, the live AgentCore Runtime returned HTTP 200 and a verifier verdict for the imported service using Amazon Nova Lite. The response reported a discrepancy. The returned website status, timestamps and finding IDs were not independently verified or confirmed as stored.

## Reproduce the demo

1. Deploy the FastAPI backend and upload `backend/ingestion/tests/fixtures/national-data-platform-sample-1.json` to `POST /ingest/ndp/upload`. Pick a service ID from the response and verify it with `GET /services/{service_id}`.
2. Package `backend/lambdas/demo_record_reader/handler.py` as a Python Lambda with entrypoint `handler.handler` and set `RE_S3_BUCKET` to the deployment bucket. Grant the Lambda role `s3:GetObject` for `hsds/ndp/*` in that bucket and `kms:Decrypt` for the data key if it uses SSE-KMS.
3. Register the Lambda with an IAM-authenticated MCP Gateway. Define the tool as `get_record` with an object input containing a required string `service_id`. Grant the Gateway role permission to invoke this Lambda.
4. Build the ARM64 image from `backend/agents` and push it to the deployment account's ECR repository. Configure AgentCore Runtime with `AGENT_APP=verifier_app`, `BEDROCK_MODEL_ID=amazon.nova-lite-v1:0`, `WEBSITE_SOURCE=sample` and the deployment's `GATEWAY_URL`. Grant the Runtime role permission to pull the image, invoke the model and call the Gateway.
5. Invoke the live Runtime with a JSON payload containing `service_id`, `run_id` and `mode: "recheck"`. Capture the request and the returned verdict for the demo.

For this Nova Lite Verifier demo, the model sees the short tool name `get_record`. The adapter forwards it to the name reported by Gateway. This configuration runs without model streaming and exposes only the read-only record tool to the Verifier. Other agent configurations keep their existing tool selection.

The demo uses file uploads of the provided NDP JSON. Live NDP retrieval is unconfigured. The demo Lambda reads stored service documents. It does not fetch websites or persist findings. Gateway Builder still requires a verified list of three or more tools and an observed agent tool call. Show Your Work still requires a CloudWatch trace containing that call.
