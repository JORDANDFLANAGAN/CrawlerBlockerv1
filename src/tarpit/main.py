import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from tarpit.access_log import AccessLogWriter
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

    yield

    log_file.close()


def create_app() -> FastAPI:
    app = FastAPI(lifespan=lifespan)

    app.include_router(root.router)
    app.include_router(maze.router)

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
