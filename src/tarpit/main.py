import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from tarpit.access_log import AccessLogWriter
from tarpit.abuse.log import AbuseLogWriter
from tarpit.abuse.middleware import register_abuse_middleware
from tarpit.abuse.state import AbuseStateStore
from tarpit.detection.log import DetectionLogWriter
from tarpit.detection.middleware import register_detection_middleware
from tarpit.detection.state import DetectionStateStore
from tarpit.markov import MarkovChain
from tarpit.routers import maze, root
from tarpit.settings import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()

    corpus_text = settings.corpus_path.read_text(encoding="utf-8")
    app.state.chain = MarkovChain.from_corpus_text(corpus_text)

    settings.log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = settings.log_path.open("a", encoding="utf-8")
    app.state.log_writer = AccessLogWriter(log_file, asyncio.Lock())

    app.state.detection_store = DetectionStateStore(
        max_entries=settings.detection_state_max_entries,
        window_seconds=settings.detection_rate_window_s,
        rate_budget=settings.detection_rate_budget,
    )

    settings.detection_log_path.parent.mkdir(parents=True, exist_ok=True)
    detection_file = settings.detection_log_path.open("a", encoding="utf-8")
    app.state.detection_log_writer = DetectionLogWriter(detection_file, asyncio.Lock())

    app.state.abuse_store = AbuseStateStore(
        per_ip_window_s=settings.rl_per_ip_window_s,
        per_ip_budget=settings.rl_per_ip_budget,
        per_ip_concurrency_max=settings.cc_per_ip_max,
        per_ip_state_max_entries=settings.rl_state_max_entries,
        global_window_s=settings.gl_window_s,
        global_rate_budget=settings.gl_rate_budget,
        global_inflight_max=settings.gl_inflight_max,
    )
    settings.abuse_log_path.parent.mkdir(parents=True, exist_ok=True)
    abuse_file = settings.abuse_log_path.open("a", encoding="utf-8")
    app.state.abuse_log_writer = AbuseLogWriter(abuse_file, asyncio.Lock())

    yield

    log_file.close()
    detection_file.close()
    abuse_file.close()


def create_app() -> FastAPI:
    app = FastAPI(lifespan=lifespan)

    app.include_router(root.router)
    app.include_router(maze.router)

    register_detection_middleware(app)   # registered first → runs second
    register_abuse_middleware(app)       # registered second → runs first

    @app.exception_handler(RequestValidationError)
    async def path_validation_to_404(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        for error in exc.errors():
            if error.get("loc") and "path" in error["loc"]:
                return JSONResponse(status_code=404, content={"detail": "Not Found"})
        return JSONResponse(status_code=422, content={"detail": exc.errors()})

    return app


app = create_app()


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "tarpit:app",
        host=settings.host,
        port=settings.port,
        reload=settings.reload,
        log_level=settings.log_level,
        workers=1,
    )
