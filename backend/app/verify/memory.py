"""Per-organization long-term notes (AgentCore Memory, namespace org/{id}).

"confirmed by phone 2026-10-12", "site blocks bots" — written when a specialist
resolves a task or the verifier returns a memory_note, read by `get_record` so
the next agent run sees them (design §6.3, R2.4).

With AGENTCORE_MEMORY_ID set, notes go to AgentCore Memory
(BatchCreateMemoryRecords / ListMemoryRecords). Without it (local dev, tests)
they are kept in the findings table under PK=ORG#<id>, SK=NOTE#<ts>.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from ..config import Settings
from ..store import Stores
from ..store.commit import now_iso

log = logging.getLogger(__name__)


class OrgMemory:
    def __init__(self, stores: Stores, settings: Settings):
        self.stores = stores
        self.settings = settings

    def _client(self):
        from .. import aws
        return aws.client("bedrock-agentcore", self.settings)

    def add(self, org_id: str, text: str, *, author: str) -> None:
        text = text.strip()[:2000]
        if not text:
            return
        if self.settings.memory_id:
            try:
                self._client().batch_create_memory_records(
                    memoryId=self.settings.memory_id,
                    records=[{
                        "requestIdentifier": uuid.uuid4().hex,
                        "namespaces": [f"org/{org_id}"],
                        "content": {"text": text},
                        "timestamp": datetime.now(timezone.utc),
                        "metadata": {"author": {"stringValue": author}},
                    }],
                )
                return
            except Exception as exc:
                log.warning("agentcore_memory_write_failed %s; using table fallback", type(exc).__name__)
        ts = now_iso()
        self.stores.findings.put({"PK": f"ORG#{org_id}", "SK": f"NOTE#{ts}#{uuid.uuid4().hex[:6]}",
                                  "text": text, "author": author, "created_at": ts})

    def notes(self, org_id: str, limit: int = 20) -> list[dict]:
        if self.settings.memory_id:
            try:
                resp = self._client().list_memory_records(memoryId=self.settings.memory_id,
                                                          namespace=f"org/{org_id}", maxResults=limit)
                return [{"text": r["content"]["text"],
                         "created_at": str(r.get("timestamp") or r.get("createdAt") or ""),
                         "author": ((r.get("metadata") or {}).get("author") or {}).get("stringValue", "")}
                        for r in resp.get("memoryRecordSummaries", [])]
            except Exception as exc:
                log.warning("agentcore_memory_read_failed %s; using table fallback", type(exc).__name__)
        rows = self.stores.findings.query(f"ORG#{org_id}", prefix="NOTE#", descending=True, limit=limit)
        return [{"text": r["text"], "author": r.get("author", ""), "created_at": r.get("created_at", "")} for r in rows]
