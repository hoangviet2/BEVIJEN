"""Storage container: every table, the S3 bucket and both queues in one object."""

from __future__ import annotations

from dataclasses import dataclass

from ..config import Settings
from .backend import SPECS, Blobs, DynamoTable, MemoryBlobs, MemoryTable, S3Blobs, Table
from .queue import MemoryQueue, Queue, SqsQueue


@dataclass
class Stores:
    hsds: Table
    index: Table
    findings: Table
    tasks: Table
    runs: Table
    keys: Table
    blobs: Blobs
    verify_q: Queue
    jobs_q: Queue


def table_names(settings: Settings) -> dict[str, str]:
    return {
        "hsds": settings.ddb_hsds_table,
        "index": settings.ddb_index_table,
        "findings": settings.ddb_findings_table,
        "tasks": settings.ddb_tasks_table,
        "runs": settings.ddb_runs_table,
        "keys": settings.ddb_keys_table,
    }


def memory_stores() -> Stores:
    tables = {name: MemoryTable(spec) for name, spec in SPECS.items()}
    return Stores(**tables, blobs=MemoryBlobs(), verify_q=MemoryQueue(), jobs_q=MemoryQueue())


def build_stores(settings: Settings) -> Stores:
    if settings.is_memory:
        return memory_stores()
    from .. import aws

    ddb = aws.resource("dynamodb", settings)
    names = table_names(settings)
    tables = {k: DynamoTable(SPECS[k], names[k], ddb) for k in SPECS}
    sqs = aws.client("sqs", settings)
    return Stores(
        **tables,
        blobs=S3Blobs(settings.s3_bucket, aws.client("s3", settings), settings.kms_key_id),
        verify_q=SqsQueue(settings.sqs_verify_url, sqs) if settings.sqs_verify_url else MemoryQueue(),
        jobs_q=SqsQueue(settings.sqs_jobs_url, sqs) if settings.sqs_jobs_url else MemoryQueue(),
    )
