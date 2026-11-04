"""FastAPI app: one server for the HSDS API, internal API, agent tools and ops.

    uvicorn app.main:app                       # API only
    RUN_WORKERS=true uvicorn app.main:app      # API + verify/jobs worker thread (single-box dev)

In docker compose the worker runs as its own service: `python -m app.cli worker`.
"""

from __future__ import annotations

import logging
import os
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .context import get_context
from .routers import hsds_api, internal, ops, tools, work

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    stop = threading.Event()
    if os.getenv("RUN_WORKERS", "false").lower() == "true":
        from .verify.worker import run_forever
        threading.Thread(target=run_forever, args=(get_context(), stop), daemon=True, name="worker").start()
    yield
    stop.set()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Resource Engine",
        description="HSDS 3.2.3 resource store with AI verification (Bedrock AgentCore), "
                    "specialist task queues by ZIP and agent discovery of new facilities.",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.include_router(ops.router)
    app.include_router(hsds_api.router)
    app.include_router(internal.router)
    app.include_router(work.router)
    app.include_router(tools.router)
    return app


app = create_app()
