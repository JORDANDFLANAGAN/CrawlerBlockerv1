"""Tests for detection middleware — one test per design §4 flow."""
import asyncio
import io
import json
import re
import time

import pytest
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from tarpit.access_log import AccessLogWriter
from tarpit.dependencies import get_chain, get_log_writer, get_settings_dep
from tarpit.detection.log import DetectionLogWriter
from tarpit.detection.middleware import BAIT_ID, register_detection_middleware
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

    # Register detection middleware AFTER routers
    register_detection_middleware(app)

    # Path-param pattern mismatch → 404
    @app.exception_handler(RequestValidationError)
    async def path_validation_to_404(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        for error in exc.errors():
            if error.get("loc") and "path" in error["loc"]:
                return JSONResponse(status_code=404, content={"detail": "Not Found"})
        return JSONResponse(status_code=422, content={"detail": exc.errors()})

    # Inject singletons via app.state (middleware reads these directly)
    app.state.chain = chain
    app.state.log_writer = access_writer
    app.state.detection_store = store
    app.state.detection_log_writer = detection_writer
    # Store settings on app.state so middleware can use test settings
    # without touching the module-level lru_cache
    app.state.settings = settings

    # Override DI so route handlers also get the injected singletons
    app.dependency_overrides[get_chain] = lambda: chain
    app.dependency_overrides[get_log_writer] = lambda: access_writer
    app.dependency_overrides[get_settings_dep] = lambda: settings

    return app


def make_access_writer() -> tuple[AccessLogWriter, io.StringIO]:
    buf = io.StringIO()
    return AccessLogWriter(buf, asyncio.Lock()), buf


def make_detection_writer() -> tuple[DetectionLogWriter, io.StringIO]:
    buf = io.StringIO()
    return DetectionLogWriter(buf, asyncio.Lock()), buf


def make_store(budget: int = 60) -> DetectionStateStore:
    return DetectionStateStore(
        max_entries=10_000,
        window_seconds=60.0,
        rate_budget=budget,
    )


@pytest.fixture
def chain() -> MarkovChain:
    return MarkovChain.from_corpus_text(TOY_CORPUS)


# ——— Test: non-flagged GET / ———

def test_non_flagged_root_passes_through(chain: MarkovChain) -> None:
    """Normal browser GET / — passes through, no detection log, state records request."""
    access_writer, access_buf = make_access_writer()
    det_writer, det_buf = make_detection_writer()
    store = make_store()
    app = make_app(chain, access_writer, det_writer, store)
    client = TestClient(app, headers={"User-Agent": "Mozilla/5.0 Chrome/120.0"})

    response = client.get("/")

    assert response.status_code == 200
    assert "If you can read this" in response.text
    assert 'href="/maze/root"' in response.text

    # No detection log line
    assert det_buf.getvalue() == ""

    # State recorded the request
    assert len(store) == 1


# ——— Test: flagged GET / (missing UA) ———

def test_flagged_root_missing_ua(chain: MarkovChain) -> None:
    """Missing UA on GET / → 200 maze HTML, detection log written, access log written."""
    access_writer, access_buf = make_access_writer()
    det_writer, det_buf = make_detection_writer()
    store = make_store()
    app = make_app(chain, access_writer, det_writer, store)
    client = TestClient(app)  # no User-Agent header

    response = client.get("/", headers={"User-Agent": ""})

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    # Response should be maze HTML (contains hidden links)
    assert 'style="display:none"' in response.text or 'href="/maze/' in response.text

    # Detection log written with ua_missing
    det_lines = [l for l in det_buf.getvalue().split("\n") if l.strip()]
    assert len(det_lines) == 1
    det_record = json.loads(det_lines[0])
    assert det_record["triggers"] == ["ua_missing"]
    assert det_record["path"] == "/"
    assert det_record["ua"] == ""

    # Access log written with path "/"
    access_lines = [l for l in access_buf.getvalue().split("\n") if l.strip()]
    assert len(access_lines) == 1
    access_record = json.loads(access_lines[0])
    assert access_record["path"] == "/"


# ——— Test: flagged GET /maze/a8f3 (rate exceeded) ———

def test_flagged_maze_rate_exceeded(chain: MarkovChain) -> None:
    """Rate-exceeded GET /maze/a8f3 → 200 maze HTML, detection log, access log."""
    access_writer, access_buf = make_access_writer()
    det_writer, det_buf = make_detection_writer()
    store = make_store(budget=60)

    # Seed state with 61 prior requests from same IP to exceed budget.
    # Use now-relative timestamps so they fall within the 60s sliding window.
    base = time.monotonic()
    for i in range(61):
        store.record_request("testclient", now=base + i * 0.1)

    app = make_app(chain, access_writer, det_writer, store)
    client = TestClient(app, headers={"User-Agent": "Mozilla/5.0 Chrome/120.0"})

    response = client.get("/maze/a8f3")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]

    # Detection log written with rate_exceeded
    det_lines = [l for l in det_buf.getvalue().split("\n") if l.strip()]
    assert len(det_lines) == 1
    det_record = json.loads(det_lines[0])
    assert "rate_exceeded" in det_record["triggers"]
    assert det_record["path"] == "/maze/a8f3"

    # Access log written with path "/maze/a8f3"
    access_lines = [l for l in access_buf.getvalue().split("\n") if l.strip()]
    assert len(access_lines) == 1
    access_record = json.loads(access_lines[0])
    assert access_record["path"] == "/maze/a8f3"


# ——— Test: GET /robots.txt with missing UA ———

def test_robots_txt_always_passthrough(chain: MarkovChain) -> None:
    """robots.txt always passes through: no detection log, no state mutation."""
    access_writer, access_buf = make_access_writer()
    det_writer, det_buf = make_detection_writer()
    store = make_store()
    app = make_app(chain, access_writer, det_writer, store)
    client = TestClient(app)

    response = client.get("/robots.txt")  # no User-Agent

    assert response.status_code == 200
    assert response.text == "User-agent: *\nDisallow: /maze/\n"

    # No detection log
    assert det_buf.getvalue() == ""

    # No state mutation — store is empty
    assert len(store) == 0


# ——— Test: flagged GET /maze/<invalid-id> ———

def test_flagged_invalid_maze_id_detection_log_no_access_log(chain: MarkovChain) -> None:
    """Flagged request to /maze/<invalid> (rate exceeded):
    detection log IS written, access log is NOT, response is 404.
    The test uses two sequential requests from the same IP to verify
    mark_maze_visit was called on the invalid path."""
    access_writer, access_buf = make_access_writer()
    det_writer, det_buf = make_detection_writer()
    store = make_store(budget=60)

    # Pre-seed 61 requests to trip rate_exceeded with current-relative timestamps
    base = time.monotonic()
    for i in range(61):
        store.record_request("testclient", now=base + i * 0.1)

    app = make_app(chain, access_writer, det_writer, store)
    client = TestClient(app, headers={"User-Agent": "Mozilla/5.0 Chrome/120.0"})

    # First request: flagged /maze/has%20space (invalid — contains space)
    r1 = client.get("/maze/has%20space")
    assert r1.status_code == 404

    # Detection log written for invalid maze path
    # Note: request.url.path returns the decoded path (/maze/has space,
    # not /maze/has%20space — starlette decodes %20 to space in .path)
    det_lines = [l for l in det_buf.getvalue().split("\n") if l.strip()]
    assert len(det_lines) == 1
    det_record = json.loads(det_lines[0])
    assert "maze" in det_record["path"]
    assert "rate_exceeded" in det_record["triggers"]

    # Access log NOT written (no 200 response)
    assert access_buf.getvalue() == ""

    # Second request: verify mark_maze_visit was called (state mutation happened)
    # This second request from the same IP should now have prior_maze_visit
    # (rate is no longer exceeded since we didn't add more timestamps)
    # So we just confirm the store has the maze visit flag
    assert store.has_prior_maze_visit("testclient") is True


# ——— Test: multi-signal trip ———

def test_multi_signal_trigger_order(chain: MarkovChain) -> None:
    """Missing UA + rate exceeded + prior maze visit → triggers in exact order."""
    access_writer, access_buf = make_access_writer()
    det_writer, det_buf = make_detection_writer()
    store = make_store(budget=60)

    # Seed rate exceeded with current-relative timestamps
    base = time.monotonic()
    for i in range(61):
        store.record_request("testclient", now=base + i * 0.1)
    # Seed prior maze visit
    store.mark_maze_visit("testclient")

    app = make_app(chain, access_writer, det_writer, store)
    client = TestClient(app)  # no User-Agent → ua_missing

    response = client.get("/", headers={"User-Agent": ""})

    assert response.status_code == 200

    det_lines = [l for l in det_buf.getvalue().split("\n") if l.strip()]
    assert len(det_lines) == 1
    det_record = json.loads(det_lines[0])
    assert det_record["triggers"] == ["ua_missing", "rate_exceeded", "prior_maze_visit"]


# ——— Test: flagged seed selection ———

def test_flagged_root_uses_bait_id_seed(chain: MarkovChain) -> None:
    """When path is / and client is flagged, generate_maze_response is called
    with seed=BAIT_ID ('root'). Verified by comparing body to direct call
    with the same chain instance."""
    from tarpit.routers.maze import generate_maze_response
    import asyncio

    access_writer1, access_buf1 = make_access_writer()
    det_writer1, det_buf1 = make_detection_writer()
    store = make_store()
    app = make_app(chain, access_writer1, det_writer1, store)

    client = TestClient(app, headers={"User-Agent": ""})

    # Make flagged request to /
    response = client.get("/", headers={"User-Agent": ""})
    assert response.status_code == 200
    middleware_body = response.content

    # Directly generate the expected body using the same chain and seed=BAIT_ID
    access_writer2, _ = make_access_writer()
    settings = make_settings()

    # We need a fake request for the direct call
    from starlette.types import Scope
    from fastapi import Request
    scope: Scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "app": app,
    }
    fake_request = Request(scope)

    direct_response = asyncio.run(
        generate_maze_response(
            seed=BAIT_ID,
            chain=chain,
            log_writer=access_writer2,
            settings=settings,
            request=fake_request,
        )
    )
    direct_body = direct_response.body

    assert middleware_body == direct_body, (
        "Middleware body should equal direct generate_maze_response(seed=BAIT_ID) body"
    )


# ——— Test: 2–5s delay applies (uses 0s in tests for speed) ———

def test_flagged_non_maze_path_delay_zero(chain: MarkovChain) -> None:
    """Flagged non-maze path responds with 200 (delay=0 in test settings)."""
    access_writer, access_buf = make_access_writer()
    det_writer, det_buf = make_detection_writer()
    store = make_store()
    app = make_app(chain, access_writer, det_writer, store)
    client = TestClient(app, headers={"User-Agent": ""})

    start = time.monotonic()
    response = client.get("/", headers={"User-Agent": ""})
    elapsed = time.monotonic() - start

    assert response.status_code == 200
    assert elapsed < 1.0  # delay_min_s=delay_max_s=0 in test settings


# ——— Test: middleware does not import from tarpit.dependencies ———

def test_middleware_does_not_import_dependencies() -> None:
    import inspect
    import tarpit.detection.middleware as mw_module

    source = inspect.getsource(mw_module)
    assert "from tarpit.dependencies" not in source
    assert "import tarpit.dependencies" not in source
