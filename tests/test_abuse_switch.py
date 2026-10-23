"""Kill-switch tests for abuse-protection and detection independence.

Verifies REQ-SWITCH-01/02, REQ-SIG-04.
Mirrors tests/test_detection_switch.py for style and layout.
"""
import asyncio
import io
import json
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tarpit.abuse.log import AbuseLogWriter
from tarpit.abuse.state import AbuseStateStore
from tarpit.detection.log import DetectionLogWriter
from tarpit.detection.state import DetectionStateStore
from tarpit.main import create_app
from tarpit.settings import Settings, get_settings


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


def base_overrides(tmp_path, *, abuse_enabled: bool = True, detection_enabled: bool = True) -> dict:
    return {
        "TARPIT_DELAY_MIN_S": "0",
        "TARPIT_DELAY_MAX_S": "0",
        "TARPIT_LOG_PATH": str(tmp_path / "access.log"),
        "TARPIT_DETECTION_LOG_PATH": str(tmp_path / "detection.log"),
        "TARPIT_ABUSE_LOG_PATH": str(tmp_path / "abuse.log"),
        "TARPIT_CORPUS_PATH": str(Path("src/tarpit/corpus.txt").resolve()),
        "TARPIT_ABUSE_PROTECTION_ENABLED": "true" if abuse_enabled else "false",
        "TARPIT_DETECTION_ENABLED": "true" if detection_enabled else "false",
        # Low thresholds so limits would trip if abuse is active
        "TARPIT_RL_PER_IP_BUDGET": "2",
        "TARPIT_RL_PER_IP_WINDOW_S": "60.0",
        "TARPIT_CC_PER_IP_MAX": "5",
        "TARPIT_GL_INFLIGHT_MAX": "50",
        "TARPIT_GL_RATE_BUDGET": "100",
        "TARPIT_GL_WINDOW_S": "60.0",
        # Detection rate budget high so detection doesn't 429 (it doesn't, but keep it clear)
        "TARPIT_DETECTION_RATE_BUDGET": "10000",
        "TARPIT_DETECTION_RATE_WINDOW_S": "60.0",
    }


# --- REQ-SWITCH-01: abuse disabled → no 429, no abuse log writes ---

def test_abuse_disabled_no_429_under_exceeded_thresholds(tmp_path) -> None:
    """With TARPIT_ABUSE_PROTECTION_ENABLED=false, requests exceeding every
    documented threshold still receive 200. REQ-SWITCH-01."""
    abuse_log = tmp_path / "abuse.log"
    overrides = base_overrides(tmp_path, abuse_enabled=False)
    original = apply_env(overrides)
    try:
        app = create_app()
        with TestClient(app, raise_server_exceptions=False) as client:
            responses = [client.get("/") for _ in range(10)]
        status_codes = [r.status_code for r in responses]
        assert all(code == 200 for code in status_codes), (
            f"Expected all 200 but got {status_codes}"
        )
    finally:
        restore_env(original)


def test_abuse_disabled_abuse_log_remains_empty(tmp_path) -> None:
    """With abuse disabled, abuse.log is never written. REQ-SWITCH-01."""
    abuse_log = tmp_path / "abuse.log"
    overrides = base_overrides(tmp_path, abuse_enabled=False)
    original = apply_env(overrides)
    try:
        app = create_app()
        with TestClient(app, raise_server_exceptions=False) as client:
            for _ in range(10):
                client.get("/")
    finally:
        restore_env(original)

    if abuse_log.exists():
        assert abuse_log.read_text().strip() == "", "abuse.log should be empty when abuse is disabled"


# --- REQ-SWITCH-02: abuse disabled → detection operates normally ---

def test_abuse_disabled_detection_still_fires(tmp_path) -> None:
    """With abuse protection disabled, detection fires normally on a
    rate-exceeded request. REQ-SWITCH-02."""
    detection_log = tmp_path / "detection.log"
    overrides = base_overrides(tmp_path, abuse_enabled=False, detection_enabled=True)
    # Lower detection rate budget so it trips
    overrides["TARPIT_DETECTION_RATE_BUDGET"] = "3"
    overrides["TARPIT_DETECTION_RATE_WINDOW_S"] = "60.0"
    original = apply_env(overrides)
    try:
        app = create_app()
        det_before = detection_log.read_text() if detection_log.exists() else ""
        with TestClient(app, raise_server_exceptions=False) as client:
            for _ in range(6):
                client.get("/")
                time.sleep(0.01)
    finally:
        restore_env(original)

    det_after = detection_log.read_text() if detection_log.exists() else ""
    new_lines = [l for l in det_after.replace(det_before, "", 1).splitlines() if l.strip()]
    assert len(new_lines) >= 1, "Detection should have fired when abuse is disabled"
    # At least one line has rate_exceeded trigger
    rate_triggers = [
        l for l in new_lines
        if "rate_exceeded" in json.loads(l).get("triggers", [])
    ]
    assert len(rate_triggers) >= 1, f"Expected rate_exceeded trigger, got: {new_lines}"


# --- REQ-SIG-04: detection disabled → flag_for_detection is no-op ---

def test_detection_disabled_abuse_still_429s(tmp_path) -> None:
    """With TARPIT_DETECTION_ENABLED=false but abuse enabled, 429s still happen.
    REQ-SIG-04."""
    overrides = base_overrides(tmp_path, abuse_enabled=True, detection_enabled=False)
    original = apply_env(overrides)
    try:
        app = create_app()
        with TestClient(app, raise_server_exceptions=False) as client:
            responses = [client.get("/") for _ in range(10)]
        status_codes = [r.status_code for r in responses]
        assert 429 in status_codes, f"Expected 429 but got {status_codes}"
    finally:
        restore_env(original)


def test_detection_disabled_abuse_log_written(tmp_path) -> None:
    """With detection disabled but abuse enabled, abuse.log still receives lines.
    REQ-SIG-04."""
    abuse_log = tmp_path / "abuse.log"
    overrides = base_overrides(tmp_path, abuse_enabled=True, detection_enabled=False)
    original = apply_env(overrides)
    try:
        app = create_app()
        with TestClient(app, raise_server_exceptions=False) as client:
            for _ in range(10):
                client.get("/")
                time.sleep(0.01)
    finally:
        restore_env(original)

    assert abuse_log.exists(), "abuse.log should exist"
    lines = [l for l in abuse_log.read_text().splitlines() if l.strip()]
    assert len(lines) >= 1, "abuse.log should have at least one line when detection is disabled"


def test_detection_disabled_no_prior_maze_visit_leak(tmp_path) -> None:
    """With detection disabled, flag_for_detection is suppressed.
    A slow follow-up request from the same IP is NOT flagged prior_maze_visit.
    REQ-SIG-04."""
    overrides = base_overrides(tmp_path, abuse_enabled=True, detection_enabled=False)
    original = apply_env(overrides)
    try:
        app = create_app()
        with TestClient(app, raise_server_exceptions=False) as client:
            # Trip abuse (flag_for_detection would be called but detection is off)
            for _ in range(5):
                client.get("/")

            detection_store = app.state.detection_store
            # flag_for_detection was suppressed (detection disabled), so no record exists
            assert detection_store.has_prior_maze_visit("testclient") is False
    finally:
        restore_env(original)
