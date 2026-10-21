import asyncio
import io
import json
import re
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from tarpit.access_log import AccessLogWriter, AccessRecord
from tarpit.dependencies import get_chain, get_log_writer, get_settings_dep
from tarpit.markov import MarkovChain
from tarpit.routers.maze import router
from tarpit.settings import Settings

# Toy corpus for tests — no corpus.txt needed
TOY_CORPUS = (
    "The cat sat on the mat. The dog ran fast! "
    "A fox jumped high. The cat ran fast on the mat. "
    "Hello world here. We go again and again and more words."
    " Some more text here to make it longer for generation purposes."
    " Even more words added here to ensure we have enough content for generation."
) * 20


def make_settings_override(delay_min: float = 0.0, delay_max: float = 0.0) -> Settings:
    return Settings(
        delay_min_s=delay_min,
        delay_max_s=delay_max,
        corpus_path="src/tarpit/corpus.txt",
        log_path="logs/access.log",
    )


def make_app(
    chain: MarkovChain,
    log_writer: AccessLogWriter,
    delay_min: float = 0.0,
    delay_max: float = 0.0,
) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    settings = make_settings_override(delay_min, delay_max)
    app.dependency_overrides[get_chain] = lambda: chain
    app.dependency_overrides[get_log_writer] = lambda: log_writer
    app.dependency_overrides[get_settings_dep] = lambda: settings

    # Path-param pattern mismatch returns 422 in FastAPI; per design.md §2.1 it
    # should return 404 (no handler runs, no log line). This handler enforces that.
    @app.exception_handler(RequestValidationError)
    async def path_validation_to_404(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        for error in exc.errors():
            if error.get("loc") and "path" in error["loc"]:
                return JSONResponse(status_code=404, content={"detail": "Not Found"})
        return JSONResponse(status_code=422, content={"detail": exc.errors()})

    return app


def make_null_writer() -> AccessLogWriter:
    buf = io.StringIO()
    return AccessLogWriter(buf, asyncio.Lock())


@pytest.fixture
def chain() -> MarkovChain:
    return MarkovChain.from_corpus_text(TOY_CORPUS)


@pytest.fixture
def writer() -> AccessLogWriter:
    return make_null_writer()


def test_determinism(chain: MarkovChain) -> None:
    writer = make_null_writer()
    app = make_app(chain, writer)
    client = TestClient(app)

    r1 = client.get("/maze/a8f3")
    r2 = client.get("/maze/a8f3")

    assert r1.status_code == 200
    assert r1.content == r2.content


def test_link_count_in_range(chain: MarkovChain, writer: AccessLogWriter) -> None:
    app = make_app(chain, writer)
    client = TestClient(app)

    response = client.get("/maze/a8f3")
    assert response.status_code == 200

    links = re.findall(r'href="/maze/([^"]+)"', response.text)
    assert 5 <= len(links) <= 10


def test_link_targets_are_4_char_hex(chain: MarkovChain, writer: AccessLogWriter) -> None:
    app = make_app(chain, writer)
    client = TestClient(app)

    response = client.get("/maze/a8f3")
    links = re.findall(r'href="/maze/([^"]+)"', response.text)
    pattern = re.compile(r"^[0-9a-f]{4}$")
    for link in links:
        assert pattern.match(link), f"{link!r} is not a 4-char hex id"


def test_invalid_id_empty_returns_404(chain: MarkovChain, writer: AccessLogWriter) -> None:
    app = make_app(chain, writer)
    client = TestClient(app)
    response = client.get("/maze/")
    assert response.status_code == 404


def test_invalid_id_script_tag_returns_404(chain: MarkovChain, writer: AccessLogWriter) -> None:
    app = make_app(chain, writer)
    client = TestClient(app)
    response = client.get("/maze/%3Cscript%3E")
    assert response.status_code == 404


def test_invalid_id_too_long_returns_404(chain: MarkovChain, writer: AccessLogWriter) -> None:
    app = make_app(chain, writer)
    client = TestClient(app)
    bad_id = "a" * 65
    response = client.get(f"/maze/{bad_id}")
    assert response.status_code == 404


def test_invalid_id_no_log_line(chain: MarkovChain) -> None:
    mock_writer = MagicMock(spec=AccessLogWriter)
    mock_writer.append = AsyncMock()

    app = make_app(chain, mock_writer)
    client = TestClient(app)

    client.get("/maze/%3Cscript%3E")
    mock_writer.append.assert_not_called()


def test_delay(chain: MarkovChain, writer: AccessLogWriter) -> None:
    app = make_app(chain, writer, delay_min=0.1, delay_max=0.1)
    client = TestClient(app)

    start = time.monotonic()
    response = client.get("/maze/a8f3")
    elapsed = time.monotonic() - start

    assert response.status_code == 200
    assert elapsed >= 0.1


def test_log_field_schema(chain: MarkovChain, tmp_path) -> None:
    log_file = tmp_path / "access.log"
    with log_file.open("w") as f:
        lock = asyncio.Lock()
        real_writer = AccessLogWriter(f, lock)

        app = make_app(chain, real_writer)
        client = TestClient(app)
        client.get("/maze/a8f3")

    lines = log_file.read_text().strip().split("\n")
    assert len(lines) == 1

    record = json.loads(lines[0])
    assert list(record.keys()) == ["ts", "ip", "ua", "path", "delay_s"]
    assert record["path"] == "/maze/a8f3"


def test_log_path_no_query_string(chain: MarkovChain, tmp_path) -> None:
    log_file = tmp_path / "access.log"
    with log_file.open("w") as f:
        lock = asyncio.Lock()
        real_writer = AccessLogWriter(f, lock)

        app = make_app(chain, real_writer)
        client = TestClient(app)
        client.get("/maze/a8f3?foo=bar")

    lines = log_file.read_text().strip().split("\n")
    record = json.loads(lines[0])
    assert record["path"] == "/maze/a8f3"
    assert "?" not in record["path"]


def test_500_error_path_no_log(chain: MarkovChain) -> None:
    mock_writer = MagicMock(spec=AccessLogWriter)
    mock_writer.append = AsyncMock()

    broken_chain = MagicMock(spec=MarkovChain)
    broken_chain.generate = MagicMock(side_effect=RuntimeError("boom"))

    app = FastAPI()
    app.include_router(router)
    settings = make_settings_override()
    app.dependency_overrides[get_chain] = lambda: broken_chain
    app.dependency_overrides[get_log_writer] = lambda: mock_writer
    app.dependency_overrides[get_settings_dep] = lambda: settings

    client = TestClient(app)
    response = client.get("/maze/a8f3")

    assert response.status_code == 500
    assert "text/plain" in response.headers["content-type"]
    assert response.text == "internal error"
    mock_writer.append.assert_not_called()
