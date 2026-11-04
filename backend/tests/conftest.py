"""Shared fixtures. No AWS needed: STORE_BACKEND=memory fakes S3, DynamoDB and
SQS with the same keys, indexes and conditions (app/store/backend.py)."""

from __future__ import annotations

import copy
import os
import sys
from pathlib import Path

os.environ.update(STORE_BACKEND="memory", AUTH_MODE="dev", AGENT_MODE="off", TOOLS_API_KEY="test-tool-key",
                  AWS_DEFAULT_REGION="us-west-2", AWS_ACCESS_KEY_ID="testing", AWS_SECRET_ACCESS_KEY="testing")

BACKEND = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(BACKEND), str(BACKEND / "agents")]
FIXTURES = BACKEND / "ingestion" / "tests" / "fixtures"
NDP_1 = FIXTURES / "national-data-platform-sample-1.json"
NDP_2 = FIXTURES / "national-data-platform-sample-2.json"
ODRS = FIXTURES / "BayesExport_20250729.json"

import pytest  # noqa: E402

from app import context as context_mod  # noqa: E402
from app.config import Settings  # noqa: E402
from app.context import Context  # noqa: E402
from app.store import memory_stores  # noqa: E402

TOOL_HEADERS = {"x-api-key": "test-tool-key"}


class StubAgent:
    """Stands in for AgentCore: returns scripted verdicts and records calls."""
    mode = "stub"

    def __init__(self, verdicts: dict[str, str] | None = None, default: str = "verified"):
        self.verdicts = verdicts or {}
        self.default = default
        self.calls: list[dict] = []
        self.discover_summary: dict | None = None
        self.on_discover = None

    def verify(self, payload):
        self.calls.append(payload)
        v = self.verdicts.get(payload["service_id"], self.default)
        if v == "bad-output":
            raise ValueError("not json")
        return {"service_id": payload["service_id"], "verdict": v, "facts_checked": ["hours"],
                "facts_confirmed": ["hours"] if v == "verified" else [], "finding_ids": [], "flag_ids": [],
                "memory_note": "checked by stub", "model_id": "stub-model"}

    def discover(self, payload):
        self.calls.append(payload)
        if self.on_discover:
            self.on_discover(payload)
        return self.discover_summary or {"run_id": payload["run_id"], "queries": ["food pantry 94590"],
                                         "candidates_seen": 1, "matched_existing": 0, "created": [], "rejected": []}


@pytest.fixture
def settings(monkeypatch) -> Settings:
    return Settings()


def make_ctx(**overrides) -> Context:
    s = Settings()
    for k, v in overrides.items():
        object.__setattr__(s, k, v)
    return Context(s, memory_stores(), agents=StubAgent())


@pytest.fixture
def ctx() -> Context:
    c = make_ctx()
    context_mod.set_context(c)
    yield c
    context_mod.set_context(None)


@pytest.fixture
def client(ctx):
    from fastapi.testclient import TestClient

    from app.main import app
    return TestClient(app)


@pytest.fixture
def ndp_loaded(ctx):
    from app.adapters import ndp_sample
    from app.pipeline import run_ingest
    return run_ingest(ctx, ndp_sample([NDP_1]))


def service_named(ctx: Context, name: str) -> dict:
    for item in ctx.repo.index_items():
        if item.get("service_name") == name:
            return item
    raise AssertionError(f"no service named {name!r}")


def handoff_record(name="Second Harvest Food Bank", postal="94103", org_desc="A food bank.", complete=False) -> dict:
    record = {
        "id": "00000000-0000-0000-0000-000000000001",
        "name": "Food distribution",
        "status": "active",
        "organization": {"id": "00000000-0000-0000-0000-0000000000a1", "name": name, "description": org_desc},
        "service_at_locations": [{
            "id": "00000000-0000-0000-0000-0000000000b9",
            "location": {
                "id": "00000000-0000-0000-0000-0000000000b1", "location_type": "physical",
                "addresses": [{"id": "00000000-0000-0000-0000-0000000000c1", "address_1": "123 Main St",
                               "city": "San Francisco", "state_province": "CA", "postal_code": postal,
                               "country": "US", "address_type": "physical"}],
            },
        }],
    }
    if complete:
        record["eligibility_description"] = "Open to all residents."
        record["schedules"] = [{"id": "00000000-0000-0000-0000-0000000000d1", "freq": "WEEKLY", "byday": "MO",
                                "opens_at": "09:00", "closes_at": "17:00"}]
        record["languages"] = [{"id": "00000000-0000-0000-0000-0000000000e1", "name": "English", "code": "en"}]
        record["service_at_locations"][0]["location"]["accessibility"] = [
            {"id": "00000000-0000-0000-0000-0000000000f1", "description": "Wheelchair accessible"}]
        record["phones"] = [{"id": "00000000-0000-0000-0000-0000000000a9", "number": "510-613-0330"}]
    return copy.deepcopy(record)
