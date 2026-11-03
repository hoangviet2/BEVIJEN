"""S3 and DynamoDB storage for HSDS records.

- S3 holds the full HSDS JSON document (canonical copy).
- DynamoDB holds a lightweight index item used for dedup and status queries.

DynamoDB table (see infra/dynamodb-table.json):
  partition key : service_id (S)
  GSI content-hash-index  : hash_pk = content_hash
  GSI match-key-index     : match_pk = fuzzy_match_key
  GSI status-index        : status_pk = verification_status, sk = last_seen_at
"""

from __future__ import annotations

import json
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.config import Config

from .config import Settings

_CLIENT_CONFIG = Config(retries={"total_max_attempts": 3, "mode": "adaptive"})


class Storage:
    """Thin wrapper over S3 + DynamoDB. One instance per process; thread safe."""

    def __init__(self, settings: Settings):
        self._settings = settings
        kwargs: dict[str, Any] = {
            "region_name": settings.aws_region,
            "config": _CLIENT_CONFIG,
        }
        if settings.aws_endpoint_url:
            kwargs["endpoint_url"] = settings.aws_endpoint_url

        self._s3 = boto3.client("s3", **kwargs)
        self._table = boto3.resource("dynamodb", **kwargs).Table(settings.ddb_table)

    # --- S3 ---------------------------------------------------------------

    def s3_key_for(self, source: str, service_id: str) -> str:
        return f"{self._settings.s3_prefix}/{source}/{service_id}.json"

    def put_document(self, key: str, record: dict[str, Any]) -> None:
        self._s3.put_object(
            Bucket=self._settings.s3_bucket,
            Key=key,
            Body=json.dumps(record, separators=(",", ":")).encode("utf-8"),
            ContentType="application/json",
            ServerSideEncryption="AES256",
        )

    def get_document(self, key: str) -> dict[str, Any] | None:
        try:
            response = self._s3.get_object(Bucket=self._settings.s3_bucket, Key=key)
        except self._s3.exceptions.NoSuchKey:
            return None
        with response["Body"] as body:
            return json.loads(body.read())

    # --- DynamoDB ---------------------------------------------------------

    def get_index_item(self, service_id: str) -> dict[str, Any] | None:
        return self._table.get_item(Key={"service_id": service_id}).get("Item")

    def find_by_content_hash(self, content_hash: str) -> dict[str, Any] | None:
        response = self._table.query(
            IndexName="content-hash-index",
            KeyConditionExpression=Key("hash_pk").eq(content_hash),
            Limit=1,
        )
        items = response.get("Items") or []
        return items[0] if items else None

    def find_by_match_key(self, match_key: str) -> dict[str, Any] | None:
        response = self._table.query(
            IndexName="match-key-index",
            KeyConditionExpression=Key("match_pk").eq(match_key),
            Limit=1,
        )
        items = response.get("Items") or []
        return items[0] if items else None

    def put_index_item(self, item: dict[str, Any]) -> None:
        self._table.put_item(Item=item)
