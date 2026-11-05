"""Everything a request, worker or CLI command needs, built once per process."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property

from .config import Settings, get_settings
from .runs import RunsRepo
from .store import Stores, build_stores
from .store.hsds_repo import HsdsRepo
from .verify.findings import FindingsRepo
from .verify.memory import OrgMemory


@dataclass
class Context:
    settings: Settings
    stores: Stores
    agents: object | None = None  # app.verify.agent_client.AgentClient (lazy)

    @cached_property
    def repo(self) -> HsdsRepo:
        return HsdsRepo(self.stores)

    @cached_property
    def findings(self) -> FindingsRepo:
        return FindingsRepo(self.stores)

    @cached_property
    def runs(self) -> RunsRepo:
        return RunsRepo(self.stores, self.settings)

    @cached_property
    def memory(self) -> OrgMemory:
        return OrgMemory(self.stores, self.settings)

    @property
    def agent_client(self):
        if self.agents is None:
            from .verify.agent_client import build_agent_client
            self.agents = build_agent_client(self.settings)
        return self.agents


_CTX: Context | None = None


def get_context() -> Context:
    global _CTX
    if _CTX is None:
        settings = get_settings()
        _CTX = Context(settings, build_stores(settings))
    return _CTX


def set_context(ctx: Context | None) -> None:
    global _CTX
    _CTX = ctx
