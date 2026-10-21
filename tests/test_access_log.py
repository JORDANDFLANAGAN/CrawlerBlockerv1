import asyncio
import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from tarpit.access_log import AccessRecord, AccessLogWriter

SAMPLE_RECORD = AccessRecord(
    ts="2026-10-21T10:40:12Z",
    ip="203.0.113.7",
    ua="SomeBot/1.0",
    path="/maze/a8f3",
    delay_s=3.4,
)


def test_access_record_is_frozen() -> None:
    with pytest.raises(FrozenInstanceError):
        SAMPLE_RECORD.ts = "changed"  # type: ignore[misc]


def test_written_json_key_order(tmp_path: Path) -> None:
    log_file = tmp_path / "access.log"
    with log_file.open("w") as f:
        writer = AccessLogWriter(f, asyncio.Lock())
        asyncio.run(writer.append(SAMPLE_RECORD))

    content = log_file.read_text()
    line = content.strip()
    # Verify it's valid JSON
    parsed = json.loads(line)
    # Verify key order by checking raw string positions
    keys = list(parsed.keys())
    assert keys == ["ts", "ip", "ua", "path", "delay_s"]


def test_written_json_newline_terminated(tmp_path: Path) -> None:
    log_file = tmp_path / "access.log"
    with log_file.open("w") as f:
        writer = AccessLogWriter(f, asyncio.Lock())
        asyncio.run(writer.append(SAMPLE_RECORD))

    content = log_file.read_text()
    assert content.endswith("\n")
    assert content.count("\n") == 1


def test_ua_none_serialises_as_null(tmp_path: Path) -> None:
    record = AccessRecord(
        ts="2026-10-21T10:40:12Z",
        ip="127.0.0.1",
        ua=None,
        path="/maze/test",
        delay_s=1.0,
    )
    log_file = tmp_path / "access.log"
    with log_file.open("w") as f:
        writer = AccessLogWriter(f, asyncio.Lock())
        asyncio.run(writer.append(record))

    content = log_file.read_text()
    parsed = json.loads(content.strip())
    assert parsed["ua"] is None


def test_delay_s_rounded_to_2_decimal_places(tmp_path: Path) -> None:
    record = AccessRecord(
        ts="2026-10-21T10:40:12Z",
        ip="127.0.0.1",
        ua=None,
        path="/maze/test",
        delay_s=3.4000000000001,
    )
    log_file = tmp_path / "access.log"
    with log_file.open("w") as f:
        writer = AccessLogWriter(f, asyncio.Lock())
        asyncio.run(writer.append(record))

    content = log_file.read_text()
    parsed = json.loads(content.strip())
    assert parsed["delay_s"] == 3.4
    assert str(parsed["delay_s"]) == "3.4"


def test_concurrent_append_20_lines(tmp_path: Path) -> None:
    log_file = tmp_path / "access.log"

    async def run() -> None:
        lock = asyncio.Lock()
        with log_file.open("w") as f:
            writer = AccessLogWriter(f, lock)
            records = [
                AccessRecord(
                    ts=f"2026-10-21T10:40:{i:02d}Z",
                    ip="127.0.0.1",
                    ua=f"bot/{i}",
                    path=f"/maze/{i:04x}",
                    delay_s=float(i) / 10,
                )
                for i in range(20)
            ]
            await asyncio.gather(*[writer.append(r) for r in records])

    asyncio.run(run())

    lines = log_file.read_text().strip().split("\n")
    assert len(lines) == 20
    for line in lines:
        parsed = json.loads(line)
        assert set(parsed.keys()) == {"ts", "ip", "ua", "path", "delay_s"}


def test_single_record_schema(tmp_path: Path) -> None:
    log_file = tmp_path / "access.log"
    with log_file.open("w") as f:
        writer = AccessLogWriter(f, asyncio.Lock())
        asyncio.run(writer.append(SAMPLE_RECORD))

    content = log_file.read_text()
    parsed = json.loads(content.strip())
    assert parsed["ts"] == "2026-10-21T10:40:12Z"
    assert parsed["ip"] == "203.0.113.7"
    assert parsed["ua"] == "SomeBot/1.0"
    assert parsed["path"] == "/maze/a8f3"
    assert parsed["delay_s"] == pytest.approx(3.4, abs=0.001)
