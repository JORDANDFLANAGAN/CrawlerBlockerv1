"""End-to-end tests for abuse protection: boots real app via create_app().

Verifies REQ-SCOPE-01/02/03, REQ-GL-04, REQ-RESP-01/02/03.
Uses temporary log paths via settings override — never writes to real logs/.
"""
import json
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tarpit.main import create_app
from tarpit.settings import get_settings


def _make_env_overrides(tmp_path, *, abuse_enabled: bool = True, per_ip_budget: int = 3) -> dict:
    return {
        "TARPIT_DELAY_MIN_S": "0",
        "TARPIT_DELAY_MAX_S": "0",
        "TARPIT_LOG_PATH": str(tmp_path / "access.log"),
        "TARPIT_DETECTION_LOG_PATH": str(tmp_path / "detection.log"),
        "TARPIT_ABUSE_LOG_PATH": str(tmp_path / "abuse.log"),
        "TARPIT_CORPUS_PATH": str(Path("src/tarpit/corpus.txt").resolve()),
        "TARPIT_ABUSE_PROTECTION_ENABLED": "true" if abuse_enabled else "false",
        "TARPIT_RL_PER_IP_BUDGET": str(per_ip_budget),
        "TARPIT_RL_PER_IP_WINDOW_S": "60.0",
        "TARPIT_RL_STATE_MAX_ENTRIES": "1000",
        "TARPIT_CC_PER_IP_MAX": "5",
        "TARPIT_GL_INFLIGHT_MAX": "50",
        "TARPIT_GL_RATE_BUDGET": "100",
        "TARPIT_GL_WINDOW_S": "60.0",
        "TARPIT_DETECTION_ENABLED": "true",
    }


def apply_env(overrides: dict) -> dict:
    original = {}
    for k, v in overrides.items():
        original[k] = os.environ.get(k)
        os.environ[k] = v
    get_settings.cache_clear()
    return original


def restore_env(original: dict) -> None:
    for k, v in original.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    get_settings.cache_clear()


def test_baseline_200_under_limits(tmp_path) -> None:
    """First request from an IP under all limits gets 200."""
    overrides = _make_env_overrides(tmp_path)
    original = apply_env(overrides)
    try:
        app = create_app()
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/")
            assert response.status_code == 200
    finally:
        restore_env(original)


def test_sustained_requests_eventually_429_on_root(tmp_path) -> None:
    """Sustained requests from one IP eventually produce 429 on /."""
    overrides = _make_env_overrides(tmp_path, per_ip_budget=3)
    original = apply_env(overrides)
    try:
        app = create_app()
        with TestClient(app, raise_server_exceptions=False) as client:
            responses = [client.get("/") for _ in range(10)]
        status_codes = [r.status_code for r in responses]
        assert 429 in status_codes, f"Expected 429 in {status_codes}"
    finally:
        restore_env(original)


def test_sustained_requests_eventually_429_on_maze(tmp_path) -> None:
    """Sustained requests from one IP eventually produce 429 on /maze/{id}."""
    overrides = _make_env_overrides(tmp_path, per_ip_budget=3)
    original = apply_env(overrides)
    try:
        app = create_app()
        with TestClient(app, raise_server_exceptions=False) as client:
            responses = [client.get("/maze/abc") for _ in range(10)]
        status_codes = [r.status_code for r in responses]
        assert 429 in status_codes, f"Expected 429 in {status_codes}"
    finally:
        restore_env(original)


def test_robots_txt_never_429(tmp_path) -> None:
    """/robots.txt is never 429'd even when all caps are tripped."""
    overrides = _make_env_overrides(tmp_path, per_ip_budget=3)
    original = apply_env(overrides)
    try:
        app = create_app()
        with TestClient(app, raise_server_exceptions=False) as client:
            # Trip the rate cap
            for _ in range(10):
                client.get("/")
            response = client.get("/robots.txt")
        assert response.status_code == 200
    finally:
        restore_env(original)


def test_429_has_empty_body(tmp_path) -> None:
    """A 429 has empty body, text/plain content-type, Content-Length: 0, no Retry-After."""
    overrides = _make_env_overrides(tmp_path, per_ip_budget=3)
    original = apply_env(overrides)
    try:
        app = create_app()
        with TestClient(app, raise_server_exceptions=False) as client:
            found_429 = False
            for _ in range(10):
                response = client.get("/")
                if response.status_code == 429:
                    found_429 = True
                    assert response.content == b""
                    assert "text/plain" in response.headers.get("content-type", "")
                    assert response.headers.get("content-length") == "0"
                    assert "retry-after" not in {k.lower() for k in response.headers}
                    break
        assert found_429, "Did not get a 429 in 10 requests"
    finally:
        restore_env(original)


def test_429_appends_line_to_abuse_log(tmp_path) -> None:
    """A 429 appends a line to abuse.log with correct keys and a valid reason."""
    abuse_log = tmp_path / "abuse.log"
    overrides = _make_env_overrides(tmp_path, per_ip_budget=3)
    original = apply_env(overrides)
    try:
        app = create_app()
        with TestClient(app, raise_server_exceptions=False) as client:
            for _ in range(10):
                client.get("/")
                time.sleep(0.01)
    finally:
        restore_env(original)

    assert abuse_log.exists(), "abuse.log should have been created"
    lines = [l for l in abuse_log.read_text().splitlines() if l.strip()]
    assert len(lines) >= 1, "Expected at least one line in abuse.log"

    record = json.loads(lines[-1])
    assert set(record.keys()) == {"ts", "ip", "ua", "path", "reason"}
    assert record["reason"] in {"per_ip_rate", "per_ip_concurrency", "global_inflight", "global_rate"}
