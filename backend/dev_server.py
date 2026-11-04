"""Local dev server with in-memory storage (no AWS required).

Run:  cd backend && .venv/bin/python dev_server.py
Then open http://127.0.0.1:8000/docs for the interactive Swagger UI.

This swaps the real S3 + DynamoDB storage for an in-memory fake, so you can
exercise the full HTTP pipeline (validate -> dedup -> store -> fetch) without
any AWS setup or credentials. Do not use this in production.
"""

from __future__ import annotations

from typing import Any

import uvicorn

from app import main
from app.config import get_settings
from app.intake import IntakeService


class InMemoryStorage:
    """Stand-in for S3 + DynamoDB that keeps everything in process memory."""

    def __init__(self) -> None:
        self.docs: dict[str, dict] = {}
        self.items: dict[str, dict] = {}

    def s3_key_for(self, source: str, service_id: str) -> str:
        return f"hsds/{source}/{service_id}.json"

    def put_document(self, key: str, record: dict[str, Any]) -> None:
        self.docs[key] = record

    def get_document(self, key: str):
        return self.docs.get(key)

    def get_index_item(self, service_id: str):
        return self.items.get(service_id)

    def find_by_content_hash(self, content_hash: str):
        return next(
            (i for i in self.items.values() if i.get("content_hash") == content_hash),
            None,
        )

    def find_by_match_key(self, match_key: str):
        if not match_key:
            return None
        return next(
            (i for i in self.items.values() if i.get("fuzzy_match_key") == match_key),
            None,
        )

    def put_index_item(self, item: dict[str, Any]) -> None:
        self.items[item["service_id"]] = item


def main_run() -> None:
    service = IntakeService(get_settings(), InMemoryStorage())
    main.app.dependency_overrides[main.get_intake_service] = lambda: service
    uvicorn.run(main.app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main_run()
