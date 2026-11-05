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
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .context import get_context
from .routers import accounts, hsds_api, internal, ops, tools, work

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
    origins = [o.strip() for o in get_settings().cors_origins.split(",") if o.strip()]
    if origins:  # a browser UI on another origin (e.g. a local dev server); same-origin /ui needs none
        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=False,
                           allow_methods=["*"], allow_headers=["Authorization", "Content-Type", "If-Match"],
                           expose_headers=["ETag", "X-Next-Cursor"])
    app.include_router(ops.router)
    app.include_router(hsds_api.router)
    app.include_router(internal.router)
    app.include_router(work.router)
    app.include_router(tools.router)
    app.include_router(accounts.router)
    ui_dir = Path(get_settings().ui_dir)
    if ui_dir.is_dir():  # absent in tests and before `npm run build`
        app.mount("/ui", StaticFiles(directory=ui_dir, html=True), name="ui")
    return app
   

app = create_app()
