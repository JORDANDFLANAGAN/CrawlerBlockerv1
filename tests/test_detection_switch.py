"""Kill-switch tests: TARPIT_DETECTION_ENABLED=false behavior."""
import asyncio
import io
import json

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from tarpit.access_log import AccessLogWriter
from tarpit.dependencies import get_chain, get_log_writer, get_settings_dep
from tarpit.detection.log import DetectionLogWriter
from tarpit.detection.middleware import register_detection_middleware
from tarpit.detection.state import DetectionStateStore
from tarpit.markov import MarkovChain
from tarpit.routers import maze, root
from tarpit.settings import Settings

TOY_CORPUS = (
    "The cat sat on the mat. The dog ran fast! "
    "A fox jumped high. The cat ran fast on the mat. "
    "Hello world here. We go again and again and more words."
    " Some more text here to make it longer for generation purposes."
    " Even more words added here to ensure we have enough content for generation."
) * 20


def make_settings(detection_enabled: bool = True) -> Settings:
    return Settings(
        delay_min_s=0.0,
        delay_max_s=0.0,
        corpus_path="src/tarpit/corpus.txt",
        log_path="logs/access.log",
        detection_enabled=detection_enabled,
        detection_rate_budget=60,
        detection_rate_window_s=60.0,
        detection_state_max_entries=10_000,
    )


def make_app(
    chain: MarkovChain,
    access_writer: AccessLogWriter,
    detection_writer: DetectionLogWriter,
    store: DetectionStateStore,
    detection_enabled: bool = True,
) -> FastAPI:
    settings = make_settings(detection_enabled=detection_enabled)
    app = FastAPI()

    app.include_router(root.router)
    app.include_router(maze.router)

    register_detection_middleware(app)

    @app.exception_handler(RequestValidationError)
    async def path_validation_to_404(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        for error in exc.errors():
            if error.get("loc") and "path" in error["loc"]:
                return JSONResponse(status_code=404, content={"detail": "Not Found"})
        return JSONResponse(status_code=422, content={"detail": exc.errors()})

    app.state.chain = chain
    app.state.log_writer = access_writer
    app.state.detection_store = store
    app.state.detection_log_writer = detection_writer
    app.state.settings = settings

    app.dependency_overrides[get_chain] = lambda: chain
    app.dependency_overrides[get_log_writer] = lambda: access_writer
    app.dependency_overrides[get_settings_dep] = lambda: settings

    return app


def make_chain() -> MarkovChain:
    return MarkovChain.from_corpus_text(TOY_CORPUS)


def make_access_writer() -> tuple[AccessLogWriter, io.StringIO]:
    buf = io.StringIO()
    return AccessLogWriter(buf, asyncio.Lock()), buf


def make_detection_writer() -> tuple[DetectionLogWriter, io.StringIO]:
    buf = io.StringIO()
    return DetectionLogWriter(buf, asyncio.Lock()), buf


def make_store() -> DetectionStateStore:
    return DetectionStateStore(
        max_entries=10_000,
        window_seconds=60.0,
        rate_budget=60,
    )


# ——— Kill switch: GET / with missing UA ———

def test_kill_switch_disabled_missing_ua_gets_v1_content() -> None:
    """With detection disabled, GET / with missing UA returns v1 content
    (contains bait anchor). No detection log written. State not mutated.
    REQ-SWITCH-01, REQ-SWITCH-02."""
    chain = make_chain()
    access_writer, access_buf = make_access_writer()
    det_writer, det_buf = make_detection_writer()
    store = make_store()

    app = make_app(chain, access_writer, det_writer, store, detection_enabled=False)
    client = TestClient(app)  # no User-Agent

    response = client.get("/", headers={"User-Agent": ""})

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    # Should contain the v1 bait anchor
    assert 'href="/maze/root"' in response.text
    # v1 content marker
    assert "If you can read this" in response.text

    # No detection log line written
    assert det_buf.getvalue() == ""

    # State is untouched
    assert len(store) == 0


# ——— Kill switch: GET /maze/root runs normal maze handler ———

def test_kill_switch_disabled_maze_route_runs_normally() -> None:
    """With detection disabled, GET /maze/root runs the normal maze handler:
    access log is written, 2–5s delay applied (0s in tests)."""
    chain = make_chain()
    access_writer, access_buf = make_access_writer()
    det_writer, det_buf = make_detection_writer()
    store = make_store()

    app = make_app(chain, access_writer, det_writer, store, detection_enabled=False)
    client = TestClient(app, headers={"User-Agent": "Mozilla/5.0 Chrome/120.0"})

    response = client.get("/maze/root")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]

    # Access log written (normal maze handler ran)
    access_lines = [l for l in access_buf.getvalue().split("\n") if l.strip()]
    assert len(access_lines) == 1
    access_record = json.loads(access_lines[0])
    assert access_record["path"] == "/maze/root"

    # No detection log
    assert det_buf.getvalue() == ""

    # State untouched (detection disabled)
    assert len(store) == 0
