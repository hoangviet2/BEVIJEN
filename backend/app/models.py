"""Request / response models for the HTTP API."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


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
    """Why a record is unverified (handoff values + design §4.4 extensions)."""
    POSSIBLE_DUPLICATE = "possible_duplicate"
    MISSING_INFORMATION = "missing_information"
    DISCREPANCY = "discrepancy"
    ACTIVELY_BAD = "actively_bad"
    CHECK_FAILED = "check_failed"
    NEEDS_MANUAL = "needs_manual"
    AGENT_DISCOVERED = "agent_discovered"
    STALE = "stale"
    CLASH = "clash"
    NEW_FACILITY = "new_facility"


# --- handoff intake ------------------------------------------------------------
class IntakeRequest(BaseModel):
    source: str = Field(..., description="Stable source identifier, e.g. 'odrs' or 'ndp'.")
    native_id: str = Field(..., description="The source's own id; drives the deterministic service id.")
    record: dict[str, Any] = Field(..., description="One HSDS 3.2.3 service with nested organization.")


class BatchIntakeRequest(BaseModel):
    source: str
    records: list[dict[str, Any]] = Field(..., description="HSDS services; each carries its own 'id'.")


class IntakeResult(BaseModel):
    outcome: Outcome
    service_id: Optional[str] = None
    verification_status: Optional[VerificationStatus] = None
    unverified_reason: Optional[UnverifiedReason] = None
    missing_fields: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    duplicate_of: Optional[str] = None
    s3_key: Optional[str] = None


class BatchIntakeResult(BaseModel):
    """R1.7: received = stored + quarantined."""
    received: int
    stored: int
    quarantined: int
    results: list[IntakeResult]


# --- ingest / verify / discovery ---------------------------------------------------
class IngestNdpRequest(BaseModel):
    mode: str = Field("sample", pattern="^(sample|mock)$")
    s3_key: Optional[str] = Field(None, description="sample mode: object key of the NDP sample in RE_S3_BUCKET")
    url: Optional[str] = Field(None, description="mock mode: NDP mock API URL (default NDP_MOCK_URL)")
    since: Optional[str] = None


class IngestOdrsRequest(BaseModel):
    since: Optional[str] = None
    max_pages: int = Field(10000, ge=1)


class VerifyBatchRequest(BaseModel):
    source: Optional[str] = None
    zip: Optional[str] = None
    older_than_days: Optional[int] = Field(None, ge=0)


class FindingDismiss(BaseModel):
    reason: str = "dismissed"


class FindingEdit(BaseModel):
    value: Any


class ResolveFinding(BaseModel):
    finding_id: str
    outcome: str = Field(..., pattern="^(confirmed|apply|edit|dismiss|closed|unreachable)$")
    value: Any = None
    reason: Optional[str] = None


class ResolveTask(BaseModel):
    method: str = Field(..., pattern="^(phone|visit|email|web)$")
    notes: str = ""
    findings: list[ResolveFinding]


class SnoozeTask(BaseModel):
    days: int = Field(3, ge=1, le=90)
    reason: str = ""


class AssignTask(BaseModel):
    assignee: str


class DiscoveryRunRequest(BaseModel):
    zip: Optional[str] = None
    county: Optional[str] = None
    categories: list[str] = Field(default_factory=list)


# --- agent tools ----------------------------------------------------------------------
class Evidence(BaseModel):
    url: str
    snippet: str = Field(..., max_length=2000)
    fetched_at: Optional[str] = None
    s3_key: Optional[str] = None


class ToolFinding(BaseModel):
    service_id: str
    category: str
    field: str = ""
    current_value: Any = None
    proposed_value: Any = None
    evidence: list[Evidence] = Field(default_factory=list)
    confidence: str = Field("medium", pattern="^(high|medium|low)$")
    model_id: str = ""
    agent_session_id: str = ""
    what_to_ask: list[str] = Field(default_factory=list)
    ai_generated: bool = False


class ToolFlag(BaseModel):
    service_id: str
    reason: str
    what_to_ask: list[str] = Field(default_factory=list, max_length=4)
    model_id: str = ""
    agent_session_id: str = ""
    evidence: list[Evidence] = Field(default_factory=list)


class ToolMatch(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    domain: Optional[str] = None
    zip: Optional[str] = None


class ToolCreate(BaseModel):
    run_id: str
    hsds: dict[str, Any]
    evidence: list[Evidence]


class ToolSchedule(BaseModel):
    text: str = Field(..., max_length=4000)


# --- accounts -------------------------------------------------------------------------
Role = Literal["specialist", "lead", "admin"]


class SignupRequest(BaseModel):
    email: str = Field(..., max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    name: str = Field(..., min_length=1, max_length=100)
    organization: str = Field("", max_length=200)
    reason: str = Field("", max_length=1000, description="Why they need access; shown to the approving admin.")


class ApproveSignup(BaseModel):
    role: Role = "specialist"


class RejectSignup(BaseModel):
    reason: str = Field("", max_length=500)


class SetRole(BaseModel):
    role: Optional[Role] = Field(None, description="null removes every group (revokes access)")
