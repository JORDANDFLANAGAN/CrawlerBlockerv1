"""End-to-end smoke test: boots the real app in-process via uvicorn.Server."""
import asyncio
import os
import threading
import time
from pathlib import Path

import httpx2 as httpx
import pytest
import uvicorn


def find_free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def running_server(tmp_path_factory):
    """Start the real tarpit app on an ephemeral port; yield the base URL; teardown."""
    tmp = tmp_path_factory.mktemp("e2e")
    log_path = tmp / "logs" / "access.log"

    port = find_free_port()

    env_overrides = {
        "TARPIT_PORT": str(port),
        "TARPIT_DELAY_MIN_S": "0",
        "TARPIT_DELAY_MAX_S": "0",
        "TARPIT_LOG_PATH": str(log_path),
        "TARPIT_CORPUS_PATH": str(Path("src/tarpit/corpus.txt").resolve()),
    }
    original_env = {}
    for k, v in env_overrides.items():
        original_env[k] = os.environ.get(k)
        os.environ[k] = v

    # Must invalidate the lru_cache before creating the app so it picks up new env
    from tarpit.settings import get_settings

    get_settings.cache_clear()

    # Import the factory fresh with the patched env
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

    # Wait for the server to be ready
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        try:
            httpx.get(f"{base_url}/robots.txt", timeout=1.0)
            break
        except Exception:
            time.sleep(0.05)
    else:
        pytest.fail("Server did not start within 10 seconds")

    yield base_url, log_path

    server.should_exit = True
    thread.join(timeout=5)

    # Restore env
    for k, v in original_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    get_settings.cache_clear()


def test_robots_txt(running_server):
    base_url, _ = running_server
    response = httpx.get(f"{base_url}/robots.txt")
    assert response.status_code == 200
    assert response.text == "User-agent: *\nDisallow: /maze/\n"


def test_root_page(running_server):
    base_url, _ = running_server
    response = httpx.get(f"{base_url}/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "If you can read this" in response.text
    assert 'href="/maze/root"' in response.text


def test_maze_page_status_and_shape(running_server):
    base_url, _ = running_server
    response = httpx.get(f"{base_url}/maze/a8f3")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    # Should contain hidden links
    assert 'style="display:none"' in response.text


def test_maze_page_access_log_written(running_server):
    base_url, log_path = running_server
    # Make a request and confirm a log line was written
    initial_lines = []
    if log_path.exists():
        initial_lines = log_path.read_text().strip().split("\n")
        initial_lines = [l for l in initial_lines if l]

    httpx.get(f"{base_url}/maze/logtest")
    time.sleep(0.2)  # Give async write time to flush

    import json
    lines = log_path.read_text().strip().split("\n")
    lines = [l for l in lines if l]
    assert len(lines) > len(initial_lines)
    # Last line should be the one we just triggered
    last = json.loads(lines[-1])
    assert last["path"] == "/maze/logtest"
    assert set(last.keys()) == {"ts", "ip", "ua", "path", "delay_s"}


def test_corpus_missing_raises_at_startup():
    """Starting the lifespan with a missing corpus_path raises at startup."""
    import os
    from tarpit.settings import get_settings, Settings

    get_settings.cache_clear()
    os.environ["TARPIT_CORPUS_PATH"] = "/nonexistent/path/corpus.txt"
    os.environ["TARPIT_DELAY_MIN_S"] = "0"
    os.environ["TARPIT_DELAY_MAX_S"] = "0"
    get_settings.cache_clear()

    from tarpit.main import create_app

    test_app = create_app()

    port = find_free_port()
    config = uvicorn.Config(
        test_app,
        host="127.0.0.1",
        port=port,
        log_level="error",
        lifespan="on",
    )
    server = uvicorn.Server(config)

    # Running the server should fail because the corpus is missing.
    # Uvicorn catches the FileNotFoundError and calls sys.exit(3), so we
    # expect either FileNotFoundError or SystemExit with a non-zero code.
    with pytest.raises((FileNotFoundError, SystemExit)) as exc_info:
        asyncio.run(server.serve())

    if isinstance(exc_info.value, SystemExit):
        assert exc_info.value.code != 0

    # Restore
    del os.environ["TARPIT_CORPUS_PATH"]
    get_settings.cache_clear()
