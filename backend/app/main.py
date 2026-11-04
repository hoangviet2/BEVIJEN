"""FastAPI app exposing the HSDS intake pipeline."""

from __future__ import annotations

from functools import lru_cache

from fastapi import Depends, FastAPI, HTTPException

from .config import Settings, get_settings
from .intake import IntakeService
from .models import (
    BatchIntakeRequest,
    BatchIntakeResult,
    IntakeRequest,
    IntakeResult,
    Outcome,
)
from .storage import Storage

app = FastAPI(
    title="Resource Engine - HSDS Intake",
    description=(
        "Receives already-converted HSDS records, checks for duplicates, stores "
        "the full document in S3 and an index item in DynamoDB, and marks each "
        "record unverified."
    ),
    version="0.1.0",
)


@lru_cache(maxsize=1)
def _service() -> IntakeService:
    settings = get_settings()
    return IntakeService(settings, Storage(settings))


def get_intake_service() -> IntakeService:
    return _service()


@app.get("/")
def root(settings: Settings = Depends(get_settings)) -> dict:
    return {
        "service": "resource-engine-hsds-intake",
        "hsds_version": settings.hsds_version,
        "storage": {"s3_bucket": settings.s3_bucket, "dynamodb_table": settings.ddb_table},
    }


@app.post("/records", response_model=IntakeResult)
def post_record(
    body: IntakeRequest,
    service: IntakeService = Depends(get_intake_service),
) -> IntakeResult:
    return service.intake(body.source, body.native_id, body.record)


@app.post("/records/batch", response_model=BatchIntakeResult)
def post_batch(
    body: BatchIntakeRequest,
    service: IntakeService = Depends(get_intake_service),
) -> BatchIntakeResult:
    results: list[IntakeResult] = []
    for record in body.records:
        native_id = record.get("id") or record.get("native_id")
        if not native_id:
            results.append(
                IntakeResult(
                    outcome=Outcome.QUARANTINED,
                    errors=["record has no 'id' to derive a native id from"],
                )
            )
            continue
        results.append(service.intake(body.source, str(native_id), record))

    quarantined = sum(1 for r in results if r.outcome is Outcome.QUARANTINED)
    stored = len(results) - quarantined
    return BatchIntakeResult(
        received=len(results),
        stored=stored,
        quarantined=quarantined,
        results=results,
    )


@app.get("/records/{service_id}")
def get_record(
    service_id: str,
    service: IntakeService = Depends(get_intake_service),
) -> dict:
    storage = service._storage  # read-through to the stored document
    item = storage.get_index_item(service_id)
    if item is None:
        raise HTTPException(status_code=404, detail="record not found")
    document = storage.get_document(item["s3_key"])
    if document is None:
        raise HTTPException(status_code=404, detail="document missing from S3")
    return document
