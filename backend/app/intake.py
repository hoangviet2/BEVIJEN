"""Intake orchestration: validate -> dedup -> store.

This is the core use case. It is deliberately independent of FastAPI so it can
be called from a scheduler, a batch job, or tests without a web server.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any

from .completeness import missing_important_fields
from .config import Settings
from .dedup import DupKind, classify
from .hsds_schema import validate_service_record
from .identity import content_hash, deterministic_id, fuzzy_match_key
from .models import IntakeResult, Outcome, UnverifiedReason, VerificationStatus
from .storage import Storage


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# Map legacy/combined stored status values onto the current
# (verification_status, unverified_reason) scheme, so items written under an
# older schema are read back safely.
_LEGACY_STATUS_MAP = {
    "verified": (VerificationStatus.VERIFIED, None),
    "unverified": (VerificationStatus.UNVERIFIED, None),
    "possible_duplicate": (
        VerificationStatus.UNVERIFIED,
        UnverifiedReason.POSSIBLE_DUPLICATE,
    ),
    "missing_information": (
        VerificationStatus.UNVERIFIED,
        UnverifiedReason.MISSING_INFORMATION,
    ),
}


def _coerce_status(status, reason):
    """Return (VerificationStatus, UnverifiedReason|None) for a stored item."""
    mapped = _LEGACY_STATUS_MAP.get(status)
    if mapped is not None:
        coerced_status, legacy_reason = mapped
        if reason and legacy_reason is None:
            try:
                legacy_reason = UnverifiedReason(reason)
            except ValueError:
                legacy_reason = None
        return coerced_status, legacy_reason
    # Unknown value -> treat as unverified with no specific reason.
    return VerificationStatus.UNVERIFIED, None


class IntakeService:
    """Receives HSDS records, dedups them, and stores them unverified."""

    def __init__(self, settings: Settings, storage: Storage):
        self._settings = settings
        self._storage = storage

    def intake(self, source: str, native_id: str, record: dict[str, Any]) -> IntakeResult:
        """Process a single already-converted HSDS record."""
        # 1. Validate against the pinned HSDS schema. Invalid -> quarantine.
        validation = validate_service_record(record)
        if not validation.valid:
            return IntakeResult(
                outcome=Outcome.QUARANTINED,
                errors=validation.errors,
            )

        # 2. Derive identity + dedup keys.
        service_id = deterministic_id(source, native_id, self._settings.id_namespace)
        c_hash = content_hash(record)
        match_key = fuzzy_match_key(record)

        # 3. Classify against what is already stored.
        decision = classify(
            self._storage,
            service_id=service_id,
            content_hash=c_hash,
            match_key=match_key,
        )

        if decision.kind is DupKind.IDENTICAL_PAYLOAD:
            existing = decision.existing_item or {}
            stored_status, stored_reason = _coerce_status(
                existing.get("verification_status"), existing.get("unverified_reason")
            )
            return IntakeResult(
                outcome=Outcome.SKIPPED_DUPLICATE,
                service_id=existing.get("service_id", service_id),
                verification_status=stored_status,
                unverified_reason=stored_reason,
                s3_key=existing.get("s3_key"),
            )

        now = _now_iso()
        is_update = decision.kind is DupKind.EXACT_EXISTING
        is_possible_dup = decision.kind is DupKind.POSSIBLE_DUPLICATE

        # Determine verification status and (if unverified) the reason.
        # Rule: a record is auto-verified only when it is not a (possible)
        # duplicate AND it has all the important optional fields. Otherwise it
        # stays unverified, and the reason is recorded in a separate field:
        #   - possible_duplicate  : matches an existing record, needs a human
        #   - missing_information : lacks important fields (eligibility, hours,
        #                           languages, accessibility, or contact)
        # On update we preserve whatever status/reason the record already had,
        # since a human/AI may have moved it past the automatic rule.
        missing = missing_important_fields(record)
        if is_update and decision.existing_item:
            status = decision.existing_item.get(
                "verification_status", VerificationStatus.UNVERIFIED.value
            )
            reason = decision.existing_item.get("unverified_reason")
        elif is_possible_dup:
            status = VerificationStatus.UNVERIFIED.value
            reason = UnverifiedReason.POSSIBLE_DUPLICATE.value
        elif missing:
            status = VerificationStatus.UNVERIFIED.value
            reason = UnverifiedReason.MISSING_INFORMATION.value
        else:
            status = VerificationStatus.VERIFIED.value
            reason = None

        # 4. Enrich the record with the Resource Engine extension, then store
        #    the full document in S3 with the service id applied.
        stored_record = self._with_extension(
            record,
            service_id=service_id,
            source=source,
            native_id=native_id,
            status=status,
            reason=reason,
            now=now,
            missing_fields=missing,
            duplicate_of=(
                decision.existing_item.get("service_id")
                if is_possible_dup and decision.existing_item
                else None
            ),
        )
        s3_key = self._storage.s3_key_for(source, service_id)
        self._storage.put_document(s3_key, stored_record)

        # 5. Write the lightweight index item to DynamoDB.
        self._storage.put_index_item(
            {
                "service_id": service_id,
                "source": source,
                "native_id": str(native_id),
                "content_hash": c_hash,
                "hash_pk": c_hash,
                "match_pk": match_key,
                "fuzzy_match_key": match_key,
                "verification_status": status,
                "status_pk": status,
                "unverified_reason": reason,
                "missing_fields": missing,
                "s3_key": s3_key,
                "org_name": (record.get("organization") or {}).get("name"),
                "service_name": record.get("name"),
                "first_seen_at": (
                    decision.existing_item.get("first_seen_at", now)
                    if is_update and decision.existing_item
                    else now
                ),
                "last_seen_at": now,
            }
        )

        if is_update:
            outcome = Outcome.UPDATED
        elif is_possible_dup:
            outcome = Outcome.POSSIBLE_DUPLICATE
        else:
            outcome = Outcome.INSERTED

        return IntakeResult(
            outcome=outcome,
            service_id=service_id,
            verification_status=VerificationStatus(status),
            unverified_reason=UnverifiedReason(reason) if reason else None,
            missing_fields=missing,
            s3_key=s3_key,
            duplicate_of=(
                decision.existing_item.get("service_id")
                if is_possible_dup and decision.existing_item
                else None
            ),
        )

    @staticmethod
    def _with_extension(
        record: dict[str, Any],
        *,
        service_id: str,
        source: str,
        native_id: str,
        status: str,
        reason: str | None,
        now: str,
        missing_fields: list[str],
        duplicate_of: str | None,
    ) -> dict[str, Any]:
        enriched = copy.deepcopy(record)
        enriched["id"] = service_id
        ext = enriched.setdefault("x_resource_engine", {})
        ext["source"] = {
            "system": source,
            "record_id": str(native_id),
            "fetched_at": now,
        }
        ext["verification"] = {"status": status, "marked_at": now}
        if reason:
            ext["verification"]["unverified_reason"] = reason
        if missing_fields:
            ext["verification"]["missing_fields"] = missing_fields
        if duplicate_of:
            ext["possible_duplicate_of"] = duplicate_of
        return enriched
