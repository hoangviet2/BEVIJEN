"""Read one imported NDP HSDS record for the AgentCore demo."""

import json
import os
from uuid import UUID

import boto3

s3 = boto3.client("s3")


def handler(event, context):
    try:
        service_id = str(UUID(event["service_id"]))
    except (KeyError, TypeError, ValueError, AttributeError):
        return {"error": "A valid service_id is required"}

    response = s3.get_object(
        Bucket=os.environ["RE_S3_BUCKET"],
        Key=f"hsds/ndp/{service_id}.json",
    )
    with response["Body"] as body:
        return {"service": json.load(body), "source": "ndp"}
