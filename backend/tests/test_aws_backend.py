"""The real AWS storage classes (DynamoTable, S3Blobs, SqsQueue) against moto,
using the same table specs that infra/dynamodb/*.json are generated from."""

import json

import boto3
import pytest
from moto import mock_aws

from app.adapters import ndp_sample
from app.config import Settings
from app.context import Context
from app.pipeline import run_ingest
from app.store import Stores, table_names
from app.store.backend import SPECS, ConditionFailed, DynamoTable, S3Blobs
from app.store.queue import SqsQueue
from app.tasks.crawler import crawl
from app.verify.worker import drain_verify

from conftest import BACKEND, NDP_1, StubAgent


@pytest.fixture
def aws_ctx():
    with mock_aws():
        s = Settings()
        object.__setattr__(s, "store_backend", "aws")
        ddb = boto3.resource("dynamodb", region_name="us-west-2")
        names = table_names(s)
        for logical, spec in SPECS.items():
            ddb.create_table(**spec.create_table_json(names[logical]))
        s3 = boto3.client("s3", region_name="us-west-2")
        s3.create_bucket(Bucket=s.s3_bucket, CreateBucketConfiguration={"LocationConstraint": "us-west-2"})
        sqs = boto3.client("sqs", region_name="us-west-2")
        vq = sqs.create_queue(QueueName="re-verify-jobs")["QueueUrl"]
        jq = sqs.create_queue(QueueName="re-jobs")["QueueUrl"]
        stores = Stores(**{k: DynamoTable(SPECS[k], names[k], ddb) for k in SPECS},
                        blobs=S3Blobs(s.s3_bucket, s3), verify_q=SqsQueue(vq, sqs), jobs_q=SqsQueue(jq, sqs))
        object.__setattr__(s, "store_backend", "memory")  # skip CloudWatch metrics in tests
        yield Context(s, stores, agents=StubAgent())


def test_full_pipeline_on_dynamodb_s3_sqs(aws_ctx):
    ctx = aws_ctx
    run = run_ingest(ctx, ndp_sample([NDP_1]))
    assert run["counts"]["services_stored"] == 20
    assert ctx.runs.get(run["run_id"])["status"] == "completed"
    items = list(ctx.repo.index_items())
    assert len(items) == 20
    sid = items[0]["service_id"]
    assert ctx.repo.render_service(sid) == ctx.stores.blobs.get_json(items[0]["s3_key"])  # GSI1 rebuild == S3
    assert ctx.repo.at_zip("94060")  # GSI2
    again = run_ingest(ctx, ndp_sample([NDP_1]))
    assert again["counts"]["services_skipped_unchanged"] == 20
    assert len(drain_verify(ctx, wait_seconds=0)) == 20
    assert crawl(ctx)["created"] > 0


def test_conditional_writes(aws_ctx):
    t = aws_ctx.stores.tasks
    t.put({"PK": "DEDUPE#LOC#x", "task_id": "1"}, if_not_exists=True)
    with pytest.raises(ConditionFailed):
        t.put({"PK": "DEDUPE#LOC#x", "task_id": "2"}, if_not_exists=True)
    h = aws_ctx.stores.hsds
    h.put({"PK": "SERVICE#a", "SK": "CURRENT", "version": 1, "entity_type": "service"}, expected_version=0)
    with pytest.raises(ConditionFailed):
        h.put({"PK": "SERVICE#a", "SK": "CURRENT", "version": 2, "entity_type": "service"}, expected_version=0)


def test_committed_table_specs_match_code():
    s = Settings()
    names = table_names(s)
    for logical, spec in SPECS.items():
        on_disk = json.loads((BACKEND / "infra" / "dynamodb" / f"{logical}.json").read_text())
        assert on_disk == spec.create_table_json(names[logical]), f"regenerate: python -m app.cli infra-specs ({logical})"


def test_committed_gateway_openapi_is_current():
    from app.cli import _to_openapi30
    from app.main import app
    on_disk = json.loads((BACKEND / "gateway" / "openapi-tools.json").read_text())
    assert set(on_disk["paths"]) == {p for p in app.openapi()["paths"] if p.startswith("/tools/")}
    ops = {op["operationId"] for p in on_disk["paths"].values() for op in p.values()}
    assert ops == {"get_record", "match_existing", "create_record", "record_finding", "flag_for_specialist",
                   "parse_schedule"}
    assert "nullable" in json.dumps(on_disk) and '"type": "null"' not in json.dumps(_to_openapi30(on_disk))


def test_agent_runtime_client_waits_for_the_whole_run_and_never_retries():
    # a 60 s timeout + botocore retries started up to 4 paid agent runs per SQS delivery
    from app import aws
    cfg = aws._client("bedrock-agentcore", "us-west-2", "").meta.config
    assert cfg.read_timeout >= 1200 and cfg.retries["total_max_attempts"] == 1


def test_records_paging_on_dynamodb(aws_ctx):
    """GET /records pages the status-index with an ExclusiveStartKey built from the last item."""
    from fastapi.testclient import TestClient

    from app import context as context_mod
    from app.main import create_app
    run_ingest(aws_ctx, ndp_sample([NDP_1]))
    context_mod.set_context(aws_ctx)
    try:
        c = TestClient(create_app())
        want = {i["service_id"] for i in aws_ctx.repo.index_items() if i["verification_status"] == "unverified"}
        seen, cursor = [], None
        while True:
            r = c.get("/records", params={"status": "unverified", "limit": 7, **({"cursor": cursor} if cursor else {})})
            assert r.status_code == 200, r.text
            seen += [i["service_id"] for i in r.json()]
            if not (cursor := r.headers.get("x-next-cursor")):
                break
        assert len(seen) == len(set(seen)) and set(seen) == want
    finally:
        context_mod.set_context(None)
