"""Tests for DetectionLogWriter and DetectionRecord."""
import asyncio
import io
import json

import pytest

from tarpit.detection.log import DetectionLogWriter, DetectionRecord


def make_writer() -> tuple[DetectionLogWriter, io.StringIO]:
    buf = io.StringIO()
    writer = DetectionLogWriter(buf, asyncio.Lock())
    return writer, buf


def test_detection_record_is_frozen() -> None:
    record = DetectionRecord(
        ts="2026-10-21T12:00:00Z",
        ip="1.2.3.4",
        ua="Mozilla/5.0",
        path="/",
        triggers=["ua_missing"],
    )
    from dataclasses import FrozenInstanceError
    with pytest.raises(FrozenInstanceError):
        record.ip = "5.6.7.8"  # type: ignore[misc]


def test_append_writes_one_json_line() -> None:
    writer, buf = make_writer()
    record = DetectionRecord(
        ts="2026-10-21T12:00:00Z",
        ip="1.2.3.4",
        ua="Mozilla/5.0",
        path="/",
        triggers=["ua_missing"],
    )
    asyncio.run(writer.append(record))

    lines = buf.getvalue().strip().split("\n")
    assert len(lines) == 1


def test_append_output_is_valid_json() -> None:
    writer, buf = make_writer()
    record = DetectionRecord(
        ts="2026-10-21T12:00:00Z",
        ip="1.2.3.4",
        ua="Mozilla/5.0",
        path="/",
        triggers=["ua_missing"],
    )
    asyncio.run(writer.append(record))
    parsed = json.loads(buf.getvalue().strip())
    assert parsed["ts"] == "2026-10-21T12:00:00Z"
    assert parsed["ip"] == "1.2.3.4"
    assert parsed["ua"] == "Mozilla/5.0"
    assert parsed["path"] == "/"
    assert parsed["triggers"] == ["ua_missing"]


def test_append_key_order() -> None:
    """JSON line must have exactly ts, ip, ua, path, triggers in that order."""
    writer, buf = make_writer()
    record = DetectionRecord(
        ts="2026-10-21T12:00:00Z",
        ip="1.2.3.4",
        ua="bot",
        path="/maze/a8f3",
        triggers=["ua_known_bot_pattern"],
    )
    asyncio.run(writer.append(record))
    line = buf.getvalue().strip()
    parsed = json.loads(line)
    assert list(parsed.keys()) == ["ts", "ip", "ua", "path", "triggers"]


def test_append_ua_none_serialises_as_null() -> None:
    writer, buf = make_writer()
    record = DetectionRecord(
        ts="2026-10-21T12:00:00Z",
        ip="1.2.3.4",
        ua=None,
        path="/",
        triggers=["ua_missing"],
    )
    asyncio.run(writer.append(record))
    parsed = json.loads(buf.getvalue().strip())
    assert parsed["ua"] is None


def test_append_triggers_list_order_preserved() -> None:
    writer, buf = make_writer()
    record = DetectionRecord(
        ts="2026-10-21T12:00:00Z",
        ip="1.2.3.4",
        ua=None,
        path="/",
        triggers=["ua_missing", "rate_exceeded"],
    )
    asyncio.run(writer.append(record))
    parsed = json.loads(buf.getvalue().strip())
    assert parsed["triggers"] == ["ua_missing", "rate_exceeded"]


def test_append_empty_triggers_allowed() -> None:
    """API allows empty triggers list; caller discipline ensures it won't happen in practice."""
    writer, buf = make_writer()
    record = DetectionRecord(
        ts="2026-10-21T12:00:00Z",
        ip="1.2.3.4",
        ua="Mozilla/5.0",
        path="/",
        triggers=[],
    )
    asyncio.run(writer.append(record))
    parsed = json.loads(buf.getvalue().strip())
    assert parsed["triggers"] == []


def test_append_line_is_newline_terminated() -> None:
    writer, buf = make_writer()
    record = DetectionRecord(
        ts="2026-10-21T12:00:00Z",
        ip="1.2.3.4",
        ua=None,
        path="/",
        triggers=["ua_missing"],
    )
    asyncio.run(writer.append(record))
    assert buf.getvalue().endswith("\n")


def test_concurrent_append_20_calls() -> None:
    """20 concurrent appends: exactly 20 lines, each valid JSON, no interleaving."""
    buf = io.StringIO()
    writer = DetectionLogWriter(buf, asyncio.Lock())

    records = [
        DetectionRecord(
            ts=f"2026-10-21T12:00:{i:02d}Z",
            ip=f"1.2.3.{i}",
            ua=None,
            path=f"/path/{i}",
            triggers=["ua_missing"],
        )
        for i in range(20)
    ]

    async def run_concurrent() -> None:
        await asyncio.gather(*[writer.append(r) for r in records])

    asyncio.run(run_concurrent())

    lines = [l for l in buf.getvalue().split("\n") if l.strip()]
    assert len(lines) == 20

    # Every line is valid JSON
    for line in lines:
        parsed = json.loads(line)
        assert list(parsed.keys()) == ["ts", "ip", "ua", "path", "triggers"]


def test_multiple_sequential_appends() -> None:
    writer, buf = make_writer()
    for i in range(5):
        record = DetectionRecord(
            ts=f"2026-10-21T12:00:0{i}Z",
            ip="1.2.3.4",
            ua=None,
            path="/",
            triggers=["ua_missing"],
        )
        asyncio.run(writer.append(record))

    lines = [l for l in buf.getvalue().split("\n") if l.strip()]
    assert len(lines) == 5
