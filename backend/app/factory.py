from __future__ import annotations

from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import router
from .config import Settings, settings as default_settings
from .db import Database
from .models import utcnow
from .pose import PoseExtractor, RTMPoseExtractor
from .worker import AnalysisWorker


def create_app(
    settings: Settings | None = None,
    extractor_factory: Callable[[], PoseExtractor] | None = None,
    clock: Callable[[], datetime] = utcnow,
    start_worker: bool | None = None,
) -> FastAPI:
    settings = settings or default_settings
    settings.ensure_dirs()
    database = Database(settings.database_url)
    database.create_all()
    worker = AnalysisWorker(database, settings, extractor_factory or RTMPoseExtractor)
    run_worker = settings.start_worker if start_worker is None else start_worker

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if run_worker:
            worker.start()
        yield
        worker.stop()

    app = FastAPI(title="Dance Duel API", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.database = database
    app.state.worker = worker
    app.state.clock = clock
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)

    @app.get("/api/health")
    def health():
        return {"ok": True}

    return app
