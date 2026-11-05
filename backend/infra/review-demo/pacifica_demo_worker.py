"""Consume the isolated Pacifica workshop queue using the deployed demo runtime."""

import json
import os
import uuid

import boto3

from app.context import get_context
from app.verify.worker import apply_verdict

SERVICE_ID = "1402495e-ea43-5af8-8d6a-c34d6dadb5a7"
EVIDENCE_URL = "https://bevijen-demo.example/review-language"
DEFAULT_RUNTIME = (
    "arn:aws:bedrock-agentcore:us-west-2:232549714284:"
    "runtime/re_verifier_agent-ykjdWF8cMN"
)


def classify_and_save(ctx, client, runtime_arn, job):
    response = client.invoke_agent_runtime(
        agentRuntimeArn=runtime_arn,
        qualifier="DEFAULT",
        runtimeSessionId=str(uuid.uuid4()),
        contentType="application/json",
        accept="application/json",
        payload=json.dumps({
            "service_id": SERVICE_ID,
            "run_id": job.get("run_id", "demo-queue"),
            "mode": "demo_language_review",
        }).encode(),
    )
    with response["response"] as stream:
        body = json.load(stream)
    finding = body.get("finding")
    if not isinstance(finding, dict):
        raise ValueError("Verifier did not return a finding")
    evidence = finding.get("evidence")
    if (
        response.get("statusCode") != 200
        or body.get("synthetic_demo") is not True
        or finding.get("service_id") != SERVICE_ID
        or finding.get("category") != "discrepancy"
        or finding.get("field") != "languages"
        or finding.get("current_value") != ["English"]
        or finding.get("proposed_value") != ["English", "Spanish"]
        or not isinstance(evidence, list)
        or not any(
            isinstance(item, dict)
            and item.get("url") == EVIDENCE_URL
            and isinstance(item.get("snippet"), str)
            and item["snippet"].strip()
            for item in evidence
        )
        or not finding.get("model_id")
    ):
        raise ValueError("Verifier output does not match the Pacifica demo fixture")

    record = ctx.repo.render_service(SERVICE_ID)
    current = [row["name"] for row in (record or {}).get("languages", [])]
    if current != finding["current_value"]:
        raise ValueError("Pacifica languages changed; review the fixture before retrying")

    existing = [
        row for row in ctx.findings.for_service(SERVICE_ID, status="open")
        if row.get("field") in ("languages", "languages[].name")
    ]
    if existing:
        saved = next(
            (row for row in existing if row.get("category") == finding["category"]
             and row.get("current_value") == finding["current_value"]
             and row.get("proposed_value") == finding["proposed_value"]),
            None,
        )
        if saved is None:
            raise ValueError("A different language finding requires review")
    else:
        saved = ctx.findings.create(
            service_id=SERVICE_ID,
            category=finding["category"],
            field=finding["field"],
            current_value=finding["current_value"],
            proposed_value=finding["proposed_value"],
            evidence=evidence,
            confidence=finding.get("confidence", "medium"),
            model_id=finding["model_id"],
            agent_session_id=finding.get("agent_session_id", ""),
            note="Synthetic demo evidence",
            source="demo",
        )
    verdict = apply_verdict(
        ctx, SERVICE_ID,
        {"verdict": finding["category"], "model_id": finding["model_id"],
         "agent_session_id": finding.get("agent_session_id", "")},
        run_id=job.get("run_id", "demo-queue"),
        mode="recheck",
    )
    if verdict.get("verdict") != finding["category"]:
        raise ValueError("Verification index was not updated")
    return {
        "outcome": "already_open" if existing else "created",
        "finding_id": saved["finding_id"],
        "status": saved["status"],
        "category": saved["category"],
        "field": saved["field"],
        "service_id": SERVICE_ID,
    }


def process_message(ctx, client, runtime_arn, message):
    try:
        job = message.body
        if job.get("service_id") != SERVICE_ID or job.get("mode") != "recheck":
            raise ValueError("Only Pacifica recheck jobs are supported by this demo")
        result = classify_and_save(ctx, client, runtime_arn, job)
        ctx.stores.verify_q.ack(message)
    except Exception:
        ctx.stores.verify_q.nack(message)
        raise
    return {**result, "sqs_acknowledged": True}


def main():
    ctx = get_context()
    if (
        type(ctx.stores.verify_q).__name__ != "SqsQueue"
        or not ctx.settings.sqs_verify_url.endswith("/bevijen-pacifica-demo-verify")
    ):
        raise SystemExit("Expected the isolated Pacifica demo SQS queue")
    runtime_arn = os.getenv("DEMO_VERIFIER_RUNTIME_ARN", DEFAULT_RUNTIME)
    client = boto3.client("bedrock-agentcore", region_name="us-west-2")
    print("Pacifica demo worker ready", flush=True)
    while True:
        for message in ctx.stores.verify_q.receive(1, 10):
            result = process_message(ctx, client, runtime_arn, message)
            print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
