# Resource Engine — backend

One FastAPI server that ingests community-resource data (NDP, ODRS, already-HSDS records),
stores it as **HSDS 3.2.3** in DynamoDB + S3, has AI agents on **Amazon Bedrock AgentCore**
verify records and discover new facilities, and turns everything that needs a person into
specialist tasks grouped by ZIP code.

- Full backend documentation: [`backend.md`](backend.md)
- AI agents, tools, Gateway, Cedar policies: [`../ai/ai.md`](../ai/ai.md)
- Design: [`../docs/SystemDesign/`](../docs/SystemDesign/) · Work log and open items: [`../docs/IMPLEMENTATION-NOTES.md`](../docs/IMPLEMENTATION-NOTES.md)

## Quick start (no AWS)

```bash
cd backend
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest

STORE_BACKEND=memory AUTH_MODE=dev AGENT_MODE=off RUN_WORKERS=true uvicorn app.main:app --reload
curl -F file=@ingestion/tests/fixtures/national-data-platform-sample-1.json http://127.0.0.1:8000/ingest/ndp/upload
open http://127.0.0.1:8000/docs
```

## Deploy (AWS CLI, us-west-2)

```bash
export AWS_REGION=us-west-2 RE_PUBLIC_URL=https://<host> SG_ID=sg-… SUBNET_ID=subnet-…
./infra/deploy.sh all
```

See `backend.md` §8 for each step.

## Layout

```
app/        server (pipelines, storage, API)       agents/    verifier + discovery agents
gateway/    AgentCore Gateway targets + Cedar      lambdas/   Gateway Lambda target (web tools)
infra/      AWS CLI deploy, IAM, table specs       tests/     pytest suite
ingestion/  raw NDP/ODRS acquisition package       transform/ HSDS mapping package (+ pinned schema)
```
