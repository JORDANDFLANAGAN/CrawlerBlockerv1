"""End-to-end tests for v2.1 detection: boots real uvicorn, mirrors test_e2e_smoke.py style."""
import json
import os
import threading
import time
from pathlib import Path

import httpx2 as httpx
import pytest
import uvicorn

from tarpit.detection.log import DetectionLogWriter
from tarpit.detection.state import DetectionStateStore


def find_free_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def detection_server(tmp_path_factory):
    """Start the real tarpit app with detection enabled on an ephemeral port.
    Uses tmp_path for both log files to avoid polluting real logs."""
    tmp = tmp_path_factory.mktemp("e2e_detection")
    access_log_path = tmp / "logs" / "access.log"
    detection_log_path = tmp / "logs" / "detection.log"

    port = find_free_port()

    env_overrides = {
        "TARPIT_PORT": str(port),
        "TARPIT_DELAY_MIN_S": "0",
        "TARPIT_DELAY_MAX_S": "0",
        "TARPIT_LOG_PATH": str(access_log_path),
        "TARPIT_DETECTION_LOG_PATH": str(detection_log_path),
        "TARPIT_CORPUS_PATH": str(Path("src/tarpit/corpus.txt").resolve()),
        "TARPIT_DETECTION_ENABLED": "true",
    }
    original_env = {}
    for k, v in env_overrides.items():
        original_env[k] = os.environ.get(k)
        os.environ[k] = v

    from tarpit.settings import get_settings
    get_settings.cache_clear()

    from tarpit.main import create_app
    real_app = create_app()

    config = uvicorn.Config(
        real_app,
        host="127.0.0.1",
        port=port,
        log_level="error",
        lifespan="on",
    )
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        try:
            httpx.get(f"{base_url}/robots.txt", timeout=1.0)
            break
        except Exception:
            time.sleep(0.05)
    else:
        pytest.fail("Detection server did not start within 10 seconds")

    yield base_url, real_app, access_log_path, detection_log_path

    server.should_exit = True
    thread.join(timeout=5)

    for k, v in original_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    get_settings.cache_clear()


def test_lifespan_builds_detection_store_and_writer(detection_server) -> None:
    """Lifespan creates detection_store (DetectionStateStore) and
    detection_log_writer (DetectionLogWriter) on app.state."""
    _, app, _, _ = detection_server

    assert hasattr(app.state, "detection_store")
    assert isinstance(app.state.detection_store, DetectionStateStore)

    assert hasattr(app.state, "detection_log_writer")
    assert isinstance(app.state.detection_log_writer, DetectionLogWriter)


def test_flagged_request_writes_detection_log(detection_server) -> None:
    """GET / with no User-Agent header: 200 maze HTML + detection log line
    with triggers=["ua_missing"] + access log line with path="/"."""
    base_url, _, access_log_path, detection_log_path = detection_server

    # Snapshot before
    det_before = detection_log_path.read_text() if detection_log_path.exists() else ""
    acc_before = access_log_path.read_text() if access_log_path.exists() else ""

    # Send request with empty User-Agent to trigger ua_missing signal.
    # httpx2 sets a default UA; override it explicitly.
    response = httpx.get(
        f"{base_url}/",
        headers={"User-Agent": ""},
        timeout=5.0,
    )
    time.sleep(0.3)  # Allow async writes to flush

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]

    # Detection log gained exactly one line
    det_after = detection_log_path.read_text()
    new_det_lines = [
        l for l in det_after.replace(det_before, "", 1).split("\n") if l.strip()
    ]
    assert len(new_det_lines) == 1
    det_record = json.loads(new_det_lines[0])
    assert det_record["triggers"] == ["ua_missing"]
    assert det_record["path"] == "/"

    # Access log gained exactly one line
    acc_after = access_log_path.read_text()
    new_acc_lines = [
        l for l in acc_after.replace(acc_before, "", 1).split("\n") if l.strip()
    ]
    assert len(new_acc_lines) == 1
    acc_record = json.loads(new_acc_lines[0])
    assert acc_record["path"] == "/"


def test_robots_txt_no_detection_log(detection_server) -> None:
    """GET /robots.txt with no User-Agent: normal robots.txt response,
    detection log unchanged, access log unchanged."""
    base_url, _, access_log_path, detection_log_path = detection_server

    det_before = detection_log_path.read_text() if detection_log_path.exists() else ""
    acc_before = access_log_path.read_text() if access_log_path.exists() else ""

    response = httpx.get(f"{base_url}/robots.txt", timeout=5.0)
    time.sleep(0.2)

    assert response.status_code == 200
    assert response.text == "User-agent: *\nDisallow: /maze/\n"

    # Both logs unchanged
    det_after = detection_log_path.read_text() if detection_log_path.exists() else ""
    acc_after = access_log_path.read_text() if access_log_path.exists() else ""
    assert det_after == det_before
    assert acc_after == acc_before


@pytest.fixture(scope="module")
def detection_disabled_server(tmp_path_factory):
    """Start the real tarpit app with TARPIT_DETECTION_ENABLED=false."""
    tmp = tmp_path_factory.mktemp("e2e_detection_off")
    access_log_path = tmp / "logs" / "access.log"
    detection_log_path = tmp / "logs" / "detection.log"

    port = find_free_port()

    env_overrides = {
        "TARPIT_PORT": str(port),
        "TARPIT_DELAY_MIN_S": "0",
        "TARPIT_DELAY_MAX_S": "0",
        "TARPIT_LOG_PATH": str(access_log_path),
        "TARPIT_DETECTION_LOG_PATH": str(detection_log_path),
        "TARPIT_CORPUS_PATH": str(Path("src/tarpit/corpus.txt").resolve()),
        "TARPIT_DETECTION_ENABLED": "false",
    }
    original_env = {}
    for k, v in env_overrides.items():
        original_env[k] = os.environ.get(k)
        os.environ[k] = v

    from tarpit.settings import get_settings
    get_settings.cache_clear()

    from tarpit.main import create_app
    real_app = create_app()

    config = uvicorn.Config(
        real_app,
        host="127.0.0.1",
        port=port,
        log_level="error",
        lifespan="on",
    )
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        try:
            httpx.get(f"{base_url}/robots.txt", timeout=1.0)
            break
        except Exception:
            time.sleep(0.05)
    else:
        pytest.fail("Detection-disabled server did not start within 10 seconds")

    yield base_url, real_app, access_log_path, detection_log_path

    server.should_exit = True
    thread.join(timeout=5)

    for k, v in original_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    get_settings.cache_clear()


def test_kill_switch_disabled_returns_v1_content(detection_disabled_server) -> None:
    """With TARPIT_DETECTION_ENABLED=false, GET / with no User-Agent returns
    v1 content (bait anchor present), both logs unchanged."""
    base_url, _, access_log_path, detection_log_path = detection_disabled_server

    det_before = detection_log_path.read_text() if detection_log_path.exists() else ""
    acc_before = access_log_path.read_text() if access_log_path.exists() else ""

    # Request with empty User-Agent (would normally trip ua_missing if detection were on)
    response = httpx.get(f"{base_url}/", headers={"User-Agent": ""}, timeout=5.0)
    time.sleep(0.2)

    assert response.status_code == 200
    # v1 content has the bait anchor
    assert 'href="/maze/root"' in response.text
    assert "If you can read this" in response.text

    # Both logs unchanged (detection disabled)
    det_after = detection_log_path.read_text() if detection_log_path.exists() else ""
    acc_after = access_log_path.read_text() if access_log_path.exists() else ""
    assert det_after == det_before
    assert acc_after == acc_before
