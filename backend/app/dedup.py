"""Duplicate classification against the DynamoDB index.

Given the derived keys for an incoming record, decide how to treat it:

- EXACT_EXISTING     : same deterministic id already present -> update (upsert)
- IDENTICAL_PAYLOAD  : same content hash on a different-or-same id -> skip
- POSSIBLE_DUPLICATE : fuzzy match key collides with a different record -> flag
- NEW                : nothing matched -> insert
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol


class DupKind(str, Enum):
    NEW = "new"
    EXACT_EXISTING = "exact_existing"
    IDENTICAL_PAYLOAD = "identical_payload"
    POSSIBLE_DUPLICATE = "possible_duplicate"


@dataclass
class DupDecision:
    kind: DupKind
    existing_item: dict[str, Any] | None = None


class IndexLookup(Protocol):
    """The subset of Storage that classification needs (eases testing)."""

    def get_index_item(self, service_id: str) -> dict[str, Any] | None: ...
    def find_by_content_hash(self, content_hash: str) -> dict[str, Any] | None: ...
    def find_by_match_key(self, match_key: str) -> dict[str, Any] | None: ...


def classify(
    lookup: IndexLookup,
    *,
    service_id: str,
    content_hash: str,
    match_key: str | None,
) -> DupDecision:
    """Classify an incoming record against what is already stored."""
    existing = lookup.get_index_item(service_id)
    if existing is not None:
        if existing.get("content_hash") == content_hash:
            # Same record, unchanged content -> nothing to do.
            return DupDecision(DupKind.IDENTICAL_PAYLOAD, existing)
        # Same id, changed content -> update in place.
        return DupDecision(DupKind.EXACT_EXISTING, existing)

    # Different id but byte-for-byte same substantive content (e.g. re-sent
    # under a different native id). Treat as a skippable duplicate.
    same_payload = lookup.find_by_content_hash(content_hash)
    if same_payload is not None:
        return DupDecision(DupKind.IDENTICAL_PAYLOAD, same_payload)

    # Same real-world resource from (possibly) another source. Flag only.
    if match_key:
        match = lookup.find_by_match_key(match_key)
        if match is not None and match.get("service_id") != service_id:
            return DupDecision(DupKind.POSSIBLE_DUPLICATE, match)

    return DupDecision(DupKind.NEW)
