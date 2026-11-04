"""Request and response models for the intake API."""

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class IntakeRequest(BaseModel):
    """One HSDS service record plus the source it came from."""

    source: str = Field(..., description="Stable source identifier, e.g. 'odrs' or 'ndp'.")
    native_id: str = Field(
        ...,
        description="The source's own id for this record, used for deterministic ids.",
    )
    record: dict[str, Any] = Field(
        ...,
        description="The HSDS service record (already converted to HSDS).",
    )


class BatchIntakeRequest(BaseModel):
    """A batch of records to intake in one call."""

    source: str
    records: list[dict[str, Any]] = Field(
        ...,
        description="HSDS service records. Each must carry its own 'id'.",
    )


class Outcome(str, Enum):
    INSERTED = "inserted"
    UPDATED = "updated"
    SKIPPED_DUPLICATE = "skipped_duplicate"
    POSSIBLE_DUPLICATE = "possible_duplicate"
    QUARANTINED = "quarantined"


class VerificationStatus(str, Enum):
    VERIFIED = "verified"
    UNVERIFIED = "unverified"


class UnverifiedReason(str, Enum):
    POSSIBLE_DUPLICATE = "possible_duplicate"
    MISSING_INFORMATION = "missing_information"


class IntakeResult(BaseModel):
    """What happened to a single record."""

    outcome: Outcome
    service_id: Optional[str] = None
    verification_status: Optional[VerificationStatus] = None
    unverified_reason: Optional[UnverifiedReason] = Field(
        default=None,
        description="Why the record is unverified. Null when verified.",
    )
    missing_fields: list[str] = Field(
        default_factory=list,
        description="Important optional fields missing (detail for missing_information).",
    )
    errors: list[str] = Field(default_factory=list)
    duplicate_of: Optional[str] = Field(
        default=None,
        description="For possible_duplicate, the service_id of the existing match.",
    )
    s3_key: Optional[str] = None


class BatchIntakeResult(BaseModel):
    """Per-record results plus R1.7 counts (received = stored + quarantined)."""

    received: int
    stored: int
    quarantined: int
    results: list[IntakeResult]
