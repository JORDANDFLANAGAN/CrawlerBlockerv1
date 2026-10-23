"""Tests for the abuse-protection middleware.

All tests build a minimal FastAPI app with just the abuse middleware
and a few dummy routes. No create_app() — isolation is intentional.
(Middleware ordering is tested in test_abuse_middleware_ordering.py, T8.)
"""
import asyncio
import io
import time
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tarpit.abuse.log import AbuseLogWriter
from tarpit.abuse.middleware import register_abuse_middleware
from tarpit.abuse.reasons import (
    REASON_GLOBAL_INFLIGHT,
    REASON_GLOBAL_RATE,
    REASON_PER_IP_CONCURRENCY,
    REASON_PER_IP_RATE,
)
from tarpit.abuse.state import AbuseStateStore
from tarpit.detection.state import DetectionStateStore
from tarpit.settings import Settings


def make_settings(**overrides) -> Settings:
    defaults = dict(
        abuse_protection_enabled=True,
        detection_enabled=True,
        rl_per_ip_budget=10,
        rl_per_ip_window_s=60.0,
        rl_state_max_entries=1000,
        cc_per_ip_max=5,
        gl_inflight_max=50,
        gl_rate_budget=100,
        gl_window_s=60.0,
    )
    defaults.update(overrides)
    return Settings(**defaults)


def make_store(settings: Settings) -> AbuseStateStore:
    return AbuseStateStore(
        per_ip_window_s=settings.rl_per_ip_window_s,
        per_ip_budget=settings.rl_per_ip_budget,
        per_ip_concurrency_max=settings.cc_per_ip_max,
        per_ip_state_max_entries=settings.rl_state_max_entries,
        global_window_s=settings.gl_window_s,
        global_rate_budget=settings.gl_rate_budget,
        global_inflight_max=settings.gl_inflight_max,
    )


def make_app(settings: Settings | None = None) -> tuple[FastAPI, io.StringIO, AbuseStateStore, DetectionStateStore]:
    if settings is None:
        settings = make_settings()
    buf = io.StringIO()
    store = make_store(settings)
    detection_store = DetectionStateStore(max_entries=1000, window_seconds=60.0, rate_budget=60)
    log_writer = AbuseLogWriter(buf, asyncio.Lock())

    app = FastAPI()

    @app.get("/")
    async def root():
        return {"ok": True}

    @app.get("/maze/{maze_id}")
    async def maze(maze_id: str):
        return {"id": maze_id}

    @app.get("/robots.txt")
    async def robots():
        return "User-agent: *\nDisallow: /maze/"

    @app.get("/other")
    async def other():
        return {"other": True}

    register_abuse_middleware(app)

    app.state.settings = settings
    app.state.abuse_store = store
    app.state.abuse_log_writer = log_writer
    app.state.detection_store = detection_store

    return app, buf, store, detection_store


# --- Rate limiting ---

def test_per_ip_rate_429() -> None:
    """Exceed per-IP rate budget → 429."""
    settings = make_settings(rl_per_ip_budget=3)
    app, buf, store, _ = make_app(settings)
    client = TestClient(app, raise_server_exceptions=False)

    # Fill the bucket
    now = time.monotonic()
    for _ in range(4):
        store.record_request("testclient", now)
    now += 0.001

    # Manually exceed budget then make request
    response = client.get("/")
    assert response.status_code == 429


def test_per_ip_rate_429_response_shape() -> None:
    """429 has empty body, correct Content-Type, Content-Length: 0."""
    settings = make_settings(rl_per_ip_budget=1)
    app, buf, store, _ = make_app(settings)
    client = TestClient(app, raise_server_exceptions=False)

    # Exceed budget
    now = time.monotonic()
    store.record_request("testclient", now)
    store.record_request("testclient", now + 0.001)

    response = client.get("/")
    assert response.status_code == 429
    assert response.content == b""
    assert "text/plain" in response.headers.get("content-type", "")
    assert response.headers.get("content-length") == "0"
    assert "retry-after" not in response.headers


def test_per_ip_rate_429_writes_log_line() -> None:
    """A 429 writes exactly one abuse log line with the correct reason."""
    settings = make_settings(rl_per_ip_budget=1)
    app, buf, store, _ = make_app(settings)
    client = TestClient(app, raise_server_exceptions=False)

    now = time.monotonic()
    store.record_request("testclient", now)
    store.record_request("testclient", now + 0.001)

    client.get("/")

    import json
    lines = [l for l in buf.getvalue().splitlines() if l.strip()]
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["reason"] == REASON_PER_IP_RATE
    assert parsed["path"] == "/"


def test_global_rate_429() -> None:
    """Exceed global rate budget → 429."""
    settings = make_settings(gl_rate_budget=2)
    app, buf, store, _ = make_app(settings)
    client = TestClient(app, raise_server_exceptions=False)

    now = time.monotonic()
    for i in range(3):
        store.record_request(f"ip{i}", now + i * 0.001)

    response = client.get("/")
    assert response.status_code == 429
    import json
    lines = [l for l in buf.getvalue().splitlines() if l.strip()]
    assert len(lines) == 1
    assert json.loads(lines[0])["reason"] == REASON_GLOBAL_RATE


def test_per_ip_concurrency_429() -> None:
    """Exceed per-IP concurrency → 429."""
    settings = make_settings(cc_per_ip_max=1)
    app, buf, store, _ = make_app(settings)
    client = TestClient(app, raise_server_exceptions=False)

    # Simulate an in-flight slot already held
    store.record_request("testclient", time.monotonic())
    result = store.try_acquire_slot("testclient")
    assert result is None  # slot acquired

    response = client.get("/maze/abc")
    assert response.status_code == 429
    import json
    lines = [l for l in buf.getvalue().splitlines() if l.strip()]
    assert len(lines) == 1
    assert json.loads(lines[0])["reason"] == REASON_PER_IP_CONCURRENCY


def test_global_inflight_429() -> None:
    """Exceed global in-flight → 429."""
    settings = make_settings(cc_per_ip_max=100, gl_inflight_max=1)
    app, buf, store, _ = make_app(settings)
    client = TestClient(app, raise_server_exceptions=False)

    # Saturate global with a different IP
    store.record_request("other", time.monotonic())
    store.try_acquire_slot("other")

    response = client.get("/maze/abc")
    assert response.status_code == 429
    import json
    lines = [l for l in buf.getvalue().splitlines() if l.strip()]
    assert len(lines) == 1
    assert json.loads(lines[0])["reason"] == REASON_GLOBAL_INFLIGHT


# --- Scope / pass-through ---

def test_robots_txt_always_allowed() -> None:
    """/robots.txt passes through even when all caps are tripped."""
    settings = make_settings(rl_per_ip_budget=0, gl_rate_budget=0, gl_inflight_max=0)
    app, buf, store, _ = make_app(settings)
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/robots.txt")
    assert response.status_code == 200


def test_out_of_scope_path_allowed() -> None:
    """Paths outside / and /maze/* are not limited."""
    settings = make_settings(rl_per_ip_budget=0, gl_rate_budget=0, gl_inflight_max=0)
    app, buf, store, _ = make_app(settings)
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/other")
    assert response.status_code == 200


def test_kill_switch_bypasses_everything() -> None:
    """When abuse_protection_enabled=False, no 429 even with all caps exceeded."""
    settings = make_settings(
        abuse_protection_enabled=False,
        rl_per_ip_budget=0,
        gl_rate_budget=0,
        gl_inflight_max=0,
    )
    app, buf, store, _ = make_app(settings)
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/")
    assert response.status_code == 200
    assert buf.getvalue() == ""


# --- Detection signal ---

def test_429_calls_flag_for_detection() -> None:
    """A 429 calls flag_for_detection so subsequent requests trip prior_maze_visit."""
    settings = make_settings(rl_per_ip_budget=1, detection_enabled=True)
    app, buf, store, detection_store = make_app(settings)
    client = TestClient(app, raise_server_exceptions=False)

    now = time.monotonic()
    store.record_request("testclient", now)
    store.record_request("testclient", now + 0.001)

    response = client.get("/")
    assert response.status_code == 429
    # IP should now be flagged in detection store
    assert detection_store.has_prior_maze_visit("testclient") is True


def test_kill_switched_detection_suppresses_signal() -> None:
    """When detection_enabled=False, 429 does not signal detection store."""
    settings = make_settings(rl_per_ip_budget=1, detection_enabled=False)
    app, buf, store, detection_store = make_app(settings)
    client = TestClient(app, raise_server_exceptions=False)

    now = time.monotonic()
    store.record_request("testclient", now)
    store.record_request("testclient", now + 0.001)

    response = client.get("/")
    assert response.status_code == 429
    assert detection_store.has_prior_maze_visit("testclient") is False
