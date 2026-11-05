"""Shared archive-before-parse pipeline and source-native handoff contract."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Protocol

log = logging.getLogger(__name__)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class RawPayload:
    body: bytes
    retrieved_at: str
    content_type: str = "application/json"


class RawStore(Protocol):
    def put(self, payload: RawPayload, source_name: str, run_id: str, page: int) -> str: ...


class LocalRawStore:
    """Development archive; not a resource database."""
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def put(self, payload: RawPayload, source_name: str, run_id: str, page: int) -> str:
        path = self.root / archive_key(source_name, run_id, page)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as output:
            output.write(payload.body)
        path.with_suffix(".metadata.json").write_text(json.dumps({
            "source_name": source_name, "retrieved_at": payload.retrieved_at,
            "content_type": payload.content_type,
            "sha256": hashlib.sha256(payload.body).hexdigest(),
        }))
        return path.as_uri()


def archive_key(source_name: str, run_id: str, page: int) -> str:
    # Source names are encoded as one path segment, never interpreted as paths.
    from urllib.parse import quote
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", source_name):
        raise ValueError("Invalid stable source name")
    return f"{quote(source_name, safe='')}/{run_id}/{page:06d}.json"


class FileSource:
    def __init__(self, paths: Iterable[str | Path], record_field: str = "data"):
        if record_field not in {"data", "opportunities"}:
            raise ValueError("Unsupported record field")
        self.record_field = record_field
        self.paths = [Path(p) for p in paths]

    def fetch(self) -> Iterable[RawPayload]:
        for path in self.paths:
            yield RawPayload(path.read_bytes(), utc_now())


@dataclass(frozen=True)
class RecordEnvelope:
    source_name: str
    original_record_id: str
    logical_record_key: str
    ingested_at: str
    raw_storage_reference: str
    record_pointer: str
    source_updated_at: str | None
    payload: dict
    contract_version: str = "1"


@dataclass
class Summary:
    run_id: str
    source_name: str
    started_at: str
    finished_at: str | None = None
    status: str = "running"
    payloads_archived: int = 0
    records_received: int = 0
    records_handed_off: int = 0
    duplicates_skipped: int = 0
    record_errors: int = 0
    errors: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def parse_page(body: bytes, record_field: str = "data") -> tuple[dict, list]:
    document = json.loads(body)
    if not isinstance(document, dict) or not isinstance(document.get(record_field), list):
        raise ValueError("Expected source envelope with configured record array")
    return document, document[record_field]


def ingest(source, raw_store: RawStore, handoff: Callable[[RecordEnvelope], None],
           source_name: str = "ndp") -> Summary:
    """A returning handoff acknowledges receipt, not HSDS validation or DB storage.

    Handoff must be idempotent by logical_record_key across runs. A failed or
    incomplete run is replayable. No deletion or incremental watermark is inferred.
    """
    if not source_name.strip():
        raise ValueError("source_name is required")
    summary = Summary(str(uuid.uuid4()), source_name, utc_now())
    seen: dict[str, str] = {}
    try:
        for page_number, raw in enumerate(source.fetch()):
            ref = raw_store.put(raw, source_name, summary.run_id, page_number)
            summary.payloads_archived += 1
            record_field = getattr(source, "record_field", "data")
            _, records = parse_page(raw.body, record_field)
            summary.records_received += len(records)
            for index, record in enumerate(records):
                pointer = f"/{record_field}/{index}"
                stage = "extraction"
                reason = "invalid_record_type"
                native_id = None
                try:
                    if not isinstance(record, dict):
                        raise ValueError("Organization must be an object")
                    reason = "missing_or_invalid_native_id"
                    native_id = record.get("id")
                    if isinstance(native_id, bool) or not isinstance(native_id, (str, int)) or not str(native_id).strip():
                        raise ValueError("Organization requires a non-empty native id")
                    native_id = str(native_id)
                    key = json.dumps([source_name, native_id], separators=(",", ":"))
                    digest = hashlib.sha256(json.dumps(record, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                    if key in seen:
                        if seen[key] != digest:
                            reason = "conflicting_duplicate"
                            raise ValueError("Conflicting payloads for the same logical record in one run")
                        summary.duplicates_skipped += 1
                        continue
                    meta = record.get("meta")
                    envelope = RecordEnvelope(source_name, native_id, key, raw.retrieved_at,
                                              ref, pointer, record.get("updated_at") if record_field == "opportunities" else (meta.get("lastUpdated") if isinstance(meta, dict) else None),
                                              record)
                    stage = "handoff"
                    reason = "handoff_failed"
                    handoff(envelope)
                    seen[key] = digest
                    summary.records_handed_off += 1
                except Exception:
                    summary.record_errors += 1
                    # Never log payloads, native IDs, URLs, credentials, or arbitrary exception messages.
                    summary.errors.append({"stage": stage, "code": reason,
                                           "raw_storage_reference": ref, "record_pointer": pointer})
                    log.warning("record_failed stage=%s page=%s index=%s", stage, page_number, index)
        summary.status = "completed_with_errors" if summary.record_errors else "completed"
    except Exception as exc:
        summary.status = "incomplete"
        summary.errors.append({"stage": "acquisition_archive_or_parse", "code": type(exc).__name__})
        log.error("ingestion_incomplete error_type=%s", type(exc).__name__)
    summary.finished_at = utc_now()
    return summary
