"""Storage primitives: key/value tables (DynamoDB) and blobs (S3).

Every table is described once by a `TableSpec`. The same spec drives
- `DynamoTable` (real DynamoDB via boto3),
- `MemoryTable` (in-process fake for tests / `STORE_BACKEND=memory`),
- the `infra/dynamodb/*.json` create-table files (`python -m app.cli infra-specs`).

Domain code (repos, commit, crawler, ...) only uses the small `Table` / `Blobs`
surface below, so it runs identically against AWS and in memory.
"""

from __future__ import annotations

import copy
import json
import threading
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterable, Iterator, Protocol

from boto3.dynamodb.conditions import Attr, Key


class ConditionFailed(Exception):
    """A conditional write lost (item exists / version mismatch)."""


@dataclass(frozen=True)
class Index:
    name: str
    hash_key: str
    range_key: str | None = None


@dataclass(frozen=True)
class TableSpec:
    logical: str  # hsds | index | findings | tasks | runs | keys
    hash_key: str
    range_key: str | None = None
    indexes: tuple[Index, ...] = field(default_factory=tuple)

    def key_of(self, item: dict) -> tuple:
        return (item[self.hash_key], item.get(self.range_key) if self.range_key else None)

    def create_table_json(self, table_name: str, kms_key: str = "") -> dict:
        attrs = {self.hash_key, *( [self.range_key] if self.range_key else [])}
        for ix in self.indexes:
            attrs.add(ix.hash_key)
            if ix.range_key:
                attrs.add(ix.range_key)
        key_schema = [{"AttributeName": self.hash_key, "KeyType": "HASH"}]
        if self.range_key:
            key_schema.append({"AttributeName": self.range_key, "KeyType": "RANGE"})
        spec: dict[str, Any] = {
            "TableName": table_name,
            "BillingMode": "PAY_PER_REQUEST",
            "AttributeDefinitions": [{"AttributeName": a, "AttributeType": "S"} for a in sorted(attrs)],
            "KeySchema": key_schema,
            "SSESpecification": {"Enabled": True, **({"SSEType": "KMS", "KMSMasterKeyId": kms_key} if kms_key else {})},
        }
        if self.indexes:
            spec["GlobalSecondaryIndexes"] = [
                {
                    "IndexName": ix.name,
                    "KeySchema": [{"AttributeName": ix.hash_key, "KeyType": "HASH"}]
                    + ([{"AttributeName": ix.range_key, "KeyType": "RANGE"}] if ix.range_key else []),
                    "Projection": {"ProjectionType": "ALL"},
                }
                for ix in self.indexes
            ]
        return spec


# --- the tables (RESOURCE-ENGINE-DESIGN.md §4) ------------------------------
SPECS: dict[str, TableSpec] = {
    # Single-table HSDS store: one item per HSDS object (+ metadata rows).
    "hsds": TableSpec("hsds", "PK", "SK", (
        Index("org-index", "org_pk", "entity_sk"),   # GSI1: whole organization graph
        Index("zip-index", "zip_pk", "PK"),          # GSI2: locations/addresses in a ZIP
        Index("type-index", "entity_type", "PK"),    # list all objects of a type (API)
    )),
    # One item per service (handoff index, extended §4.4).
    "index": TableSpec("index", "service_id", None, (
        Index("content-hash-index", "hash_pk"),
        Index("match-key-index", "match_pk"),
        Index("status-index", "status_pk", "last_seen_at"),
    )),
    "findings": TableSpec("findings", "PK", "SK", (
        Index("category-status-index", "cat_status", "checked_at"),
        Index("finding-index", "finding_id"),
    )),
    "tasks": TableSpec("tasks", "PK", None, (
        Index("zip-index", "zip_status", "sort"),
        Index("assignee-index", "assignee", "sort"),
    )),
    "runs": TableSpec("runs", "PK", None, (Index("kind-index", "kind", "started_at"),)),
    "keys": TableSpec("keys", "PK"),
}


# --- (de)serialisation for DynamoDB ------------------------------------------
def to_ddb(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: to_ddb(v) for k, v in value.items() if v is not None and v != "" and v != [] and v != {}}
    if isinstance(value, (list, tuple)):
        return [to_ddb(v) for v in value]
    return value


def from_ddb(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: from_ddb(v) for k, v in value.items()}
    if isinstance(value, (list, set)):
        return [from_ddb(v) for v in value]
    return value


class Table(Protocol):
    spec: TableSpec

    def put(self, item: dict, *, if_not_exists: bool = False, expected_version: int | None = None) -> None: ...
    def get(self, hash_value: str, range_value: str | None = None) -> dict | None: ...
    def delete(self, hash_value: str, range_value: str | None = None) -> None: ...
    def query(self, hash_value: str, *, index: str | None = None, prefix: str | None = None,
              limit: int | None = None, descending: bool = False) -> list[dict]: ...
    def scan(self) -> Iterator[dict]: ...
    def iter_query(self, hash_value: str, *, index: str, descending: bool = False,
                   start: dict | None = None) -> Iterator[dict]: ...
    def put_many(self, items: Iterable[dict]) -> None: ...


class MemoryTable:
    """Dict-backed table that honours the same keys, GSIs and conditions."""

    def __init__(self, spec: TableSpec):
        self.spec = spec
        self._items: dict[tuple, dict] = {}
        self._lock = threading.RLock()

    def _clean(self, item: dict) -> dict:
        return from_ddb(to_ddb(copy.deepcopy(item)))

    def put(self, item, *, if_not_exists=False, expected_version=None):
        with self._lock:
            key = self.spec.key_of(item)
            cur = self._items.get(key)
            if if_not_exists and cur is not None:
                raise ConditionFailed(str(key))
            if expected_version is not None and (cur or {}).get("version", 0) != expected_version:
                raise ConditionFailed(f"version mismatch for {key}")
            self._items[key] = self._clean(item)

    def get(self, hash_value, range_value=None):
        with self._lock:
            item = self._items.get((hash_value, range_value))
            return copy.deepcopy(item) if item else None

    def delete(self, hash_value, range_value=None):
        with self._lock:
            self._items.pop((hash_value, range_value), None)

    def query(self, hash_value, *, index=None, prefix=None, limit=None, descending=False):
        if index:
            ix = next(i for i in self.spec.indexes if i.name == index)
            hk, rk = ix.hash_key, ix.range_key
        else:
            hk, rk = self.spec.hash_key, self.spec.range_key
        with self._lock:
            rows = [copy.deepcopy(i) for i in self._items.values() if i.get(hk) == hash_value
                    and (not index or not rk or rk in i)]
        if prefix is not None and rk:
            rows = [r for r in rows if str(r.get(rk, "")).startswith(prefix)]
        if rk:
            rows.sort(key=lambda r: str(r.get(rk, "")), reverse=descending)
        return rows[:limit] if limit else rows

    def iter_query(self, hash_value, *, index, descending=False, start=None):
        """Lazy index query that resumes after `start` (an item's keys), like DynamoDB's ExclusiveStartKey."""
        ix = next(i for i in self.spec.indexes if i.name == index)
        order = lambda r: (str(r.get(ix.range_key, "")), str(r.get(self.spec.hash_key, "")))  # noqa: E731
        rows = sorted(self.query(hash_value, index=index), key=order, reverse=descending)
        if start:
            s = order(start)
            rows = [r for r in rows if (order(r) < s if descending else order(r) > s)]
        yield from rows

    def scan(self):
        with self._lock:
            items = [copy.deepcopy(i) for i in self._items.values()]
        yield from items

    def put_many(self, items):
        for item in items:
            self.put(item)


class DynamoTable:
    def __init__(self, spec: TableSpec, name: str, ddb_resource: Any):
        self.spec = spec
        self.name = name
        self._t = ddb_resource.Table(name)

    def _key(self, h, r=None):
        key = {self.spec.hash_key: h}
        if self.spec.range_key:
            key[self.spec.range_key] = r
        return key

    def put(self, item, *, if_not_exists=False, expected_version=None):
        kwargs: dict[str, Any] = {"Item": to_ddb(item)}
        if if_not_exists:
            kwargs["ConditionExpression"] = Attr(self.spec.hash_key).not_exists()
        elif expected_version is not None:
            cond = Attr("version").eq(expected_version)
            if expected_version == 0:
                cond = cond | Attr("version").not_exists()
            kwargs["ConditionExpression"] = cond
        try:
            self._t.put_item(**kwargs)
        except self._t.meta.client.exceptions.ConditionalCheckFailedException as exc:
            raise ConditionFailed(str(exc)) from None

    def get(self, hash_value, range_value=None):
        item = self._t.get_item(Key=self._key(hash_value, range_value)).get("Item")
        return from_ddb(item) if item else None

    def delete(self, hash_value, range_value=None):
        self._t.delete_item(Key=self._key(hash_value, range_value))

    def query(self, hash_value, *, index=None, prefix=None, limit=None, descending=False):
        if index:
            ix = next(i for i in self.spec.indexes if i.name == index)
            hk, rk = ix.hash_key, ix.range_key
        else:
            hk, rk = self.spec.hash_key, self.spec.range_key
        cond = Key(hk).eq(hash_value)
        if prefix is not None and rk:
            cond = cond & Key(rk).begins_with(prefix)
        kwargs: dict[str, Any] = {"KeyConditionExpression": cond, "ScanIndexForward": not descending}
        if index:
            kwargs["IndexName"] = index
        out: list[dict] = []
        while True:
            resp = self._t.query(**kwargs)
            out.extend(from_ddb(i) for i in resp.get("Items", []))
            if limit and len(out) >= limit:
                return out[:limit]
            if "LastEvaluatedKey" not in resp:
                return out
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    def iter_query(self, hash_value, *, index, descending=False, start=None):
        """Lazy index query (one page at a time) that resumes after `start` (an item's keys)."""
        ix = next(i for i in self.spec.indexes if i.name == index)
        kwargs: dict[str, Any] = {"IndexName": index, "KeyConditionExpression": Key(ix.hash_key).eq(hash_value),
                                  "ScanIndexForward": not descending}
        if start:
            keys = {ix.hash_key, ix.range_key, self.spec.hash_key, self.spec.range_key} - {None}
            kwargs["ExclusiveStartKey"] = {k: start[k] for k in keys}
        while True:
            resp = self._t.query(**kwargs)
            for item in resp.get("Items", []):
                yield from_ddb(item)
            if "LastEvaluatedKey" not in resp:
                return
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    def scan(self):
        kwargs: dict[str, Any] = {}
        while True:
            resp = self._t.scan(**kwargs)
            for item in resp.get("Items", []):
                yield from_ddb(item)
            if "LastEvaluatedKey" not in resp:
                return
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    def put_many(self, items):
        with self._t.batch_writer() as bw:
            for item in items:
                bw.put_item(Item=to_ddb(item))


# --- blobs -------------------------------------------------------------------
class Blobs(Protocol):
    bucket: str

    def put_json(self, key: str, value: Any) -> str | None: ...
    def put_bytes(self, key: str, body: bytes, content_type: str = "application/octet-stream") -> str | None: ...
    def get_json(self, key: str) -> Any | None: ...
    def get_bytes(self, key: str) -> bytes | None: ...
    def list(self, prefix: str) -> list[str]: ...
    def presign(self, key: str, seconds: int = 900) -> str: ...


class MemoryBlobs:
    def __init__(self, bucket: str = "memory"):
        self.bucket = bucket
        self.objects: dict[str, bytes] = {}
        self.versions: dict[str, list[bytes]] = {}

    def put_bytes(self, key, body, content_type="application/octet-stream"):
        self.objects[key] = body
        self.versions.setdefault(key, []).append(body)
        return str(len(self.versions[key]))

    def put_json(self, key, value):
        return self.put_bytes(key, json.dumps(value, default=str).encode(), "application/json")

    def get_bytes(self, key):
        return self.objects.get(key)

    def get_json(self, key):
        body = self.objects.get(key)
        return json.loads(body) if body is not None else None

    def list(self, prefix):
        return sorted(k for k in self.objects if k.startswith(prefix))

    def presign(self, key, seconds=900):
        return f"memory://{self.bucket}/{key}"


class S3Blobs:
    def __init__(self, bucket: str, s3_client: Any, kms_key_id: str = ""):
        self.bucket = bucket
        self._s3 = s3_client
        self._sse = ({"ServerSideEncryption": "aws:kms", "SSEKMSKeyId": kms_key_id}
                     if kms_key_id else {"ServerSideEncryption": "AES256"})

    def put_bytes(self, key, body, content_type="application/octet-stream"):
        resp = self._s3.put_object(Bucket=self.bucket, Key=key, Body=body, ContentType=content_type, **self._sse)
        return resp.get("VersionId")

    def put_json(self, key, value):
        return self.put_bytes(key, json.dumps(value, separators=(",", ":"), default=str).encode(), "application/json")

    def get_bytes(self, key):
        try:
            resp = self._s3.get_object(Bucket=self.bucket, Key=key)
        except self._s3.exceptions.NoSuchKey:
            return None
        with resp["Body"] as body:
            return body.read()

    def get_json(self, key):
        body = self.get_bytes(key)
        return json.loads(body) if body is not None else None

    def list(self, prefix):
        keys: list[str] = []
        for page in self._s3.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix):
            keys.extend(o["Key"] for o in page.get("Contents", []))
        return keys

    def presign(self, key, seconds=900):
        return self._s3.generate_presigned_url("get_object", Params={"Bucket": self.bucket, "Key": key},
                                               ExpiresIn=seconds)
