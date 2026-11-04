"""Deterministic identity and duplicate-detection keys for HSDS records.

Three derived values drive the dedup logic:

- deterministic_id: a UUIDv5 from (source name + native id). Re-ingesting the
  same source record always yields the same id, so it upserts instead of
  duplicating (HSDS R1.6).
- content_hash: a SHA-256 over the normalized record content. Identical
  payloads produce the same hash, so a repeat of an unchanged record can be
  skipped.
- fuzzy_match_key: a normalized (org name + location/phone) key used only to
  *flag* a possible cross-source duplicate of the same real-world resource.
  It never triggers an automatic merge.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any


def deterministic_id(source: str, native_id: str, namespace: str) -> str:
    """Return a stable UUIDv5 derived from the source and its native record id."""
    ns = uuid.UUID(namespace)
    name = f"{source.strip().lower()}:{str(native_id).strip()}"
    return str(uuid.uuid5(ns, name))


def content_hash(record: dict[str, Any]) -> str:
    """Return a SHA-256 over the record, excluding volatile/operational fields.

    Operational extensions (x_resource_engine) and timestamps are excluded so
    that re-sending the same substantive record produces the same hash.
    """
    stripped = _strip_volatile(record)
    canonical = json.dumps(stripped, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


_VOLATILE_KEYS = {
    "x_resource_engine",
    "last_modified",
    "assured_date",
    "metadata",
}


def _strip_volatile(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: _strip_volatile(v)
            for k, v in value.items()
            if k not in _VOLATILE_KEYS
        }
    if isinstance(value, list):
        return [_strip_volatile(v) for v in value]
    return value


def _normalize_text(text: str) -> str:
    text = text.lower().strip()
    # Drop common org suffixes/punctuation so "Food Bank, Inc." == "food bank".
    text = re.sub(r"[.,/#!$%^&*;:{}=\-_`~()]", " ", text)
    text = re.sub(r"\b(inc|llc|corp|co|the|a|of|and)\b", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def fuzzy_match_key(record: dict[str, Any]) -> str | None:
    """Return a normalized key for possible cross-source duplicate detection.

    Built from the organization name plus the strongest available location
    signal (postal code, else a phone number). Returns None when there is not
    enough signal to responsibly claim a possible duplicate.
    """
    org = record.get("organization") or {}
    org_name = org.get("name") or record.get("name")
    if not org_name:
        return None

    norm_name = _normalize_text(str(org_name))
    if not norm_name:
        return None

    signal = _location_signal(record) or _phone_signal(record)
    if not signal:
        return None

    raw = f"{norm_name}|{signal}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _location_signal(record: dict[str, Any]) -> str | None:
    for location in record.get("locations") or []:
        if not isinstance(location, dict):
            continue
        addresses = location.get("addresses") or (
            [location["address"]] if isinstance(location.get("address"), dict) else []
        )
        for addr in addresses:
            postal = (addr or {}).get("postal_code")
            if postal:
                return re.sub(r"\s+", "", str(postal).lower())[:5]
    return None


def _phone_signal(record: dict[str, Any]) -> str | None:
    for phone in record.get("phones") or []:
        if isinstance(phone, dict) and phone.get("number"):
            digits = re.sub(r"\D", "", str(phone["number"]))
            if len(digits) >= 10:
                return digits[-10:]
    return None
