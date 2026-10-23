"""Tests for AbuseRecord and AbuseLogWriter."""
import asyncio
import io
import json

import pytest

from tarpit.abuse.log import AbuseRecord, AbuseLogWriter


def make_record(
    ts: str = "2026-10-23T12:00:00Z",
    ip: str = "1.2.3.4",
    ua: str | None = "TestBot/1.0",
    path: str = "/maze/abc",
    reason: str = "per_ip_rate",
) -> AbuseRecord:
    return AbuseRecord(ts=ts, ip=ip, ua=ua, path=path, reason=reason)


def test_abuse_record_is_frozen() -> None:
    record = make_record()
    with pytest.raises(Exception):
        record.ip = "5.6.7.8"  # type: ignore[misc]


def test_single_line_json_output() -> None:
    buf = io.StringIO()
    writer = AbuseLogWriter(buf, asyncio.Lock())
    record = make_record()
    asyncio.run(writer.append(record))
    lines = buf.getvalue().splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["ts"] == record.ts
    assert parsed["ip"] == record.ip
    assert parsed["ua"] == record.ua
    assert parsed["path"] == record.path
    assert parsed["reason"] == record.reason


def test_key_order() -> None:
    """JSON line must have keys in order: ts, ip, ua, path, reason."""
    buf = io.StringIO()
    writer = AbuseLogWriter(buf, asyncio.Lock())
    asyncio.run(writer.append(make_record()))
    line = buf.getvalue().strip()
    keys = list(json.loads(line).keys())
    assert keys == ["ts", "ip", "ua", "path", "reason"]


def test_ua_none_serialises_to_null() -> None:
    buf = io.StringIO()
    writer = AbuseLogWriter(buf, asyncio.Lock())
    asyncio.run(writer.append(make_record(ua=None)))
    parsed = json.loads(buf.getvalue().strip())
    assert parsed["ua"] is None


def test_concurrent_appends_produce_two_complete_lines() -> None:
    """Two concurrent append calls produce two complete, non-interleaved lines."""
    buf = io.StringIO()
    lock = asyncio.Lock()
    writer = AbuseLogWriter(buf, lock)

    record1 = make_record(ip="1.2.3.4", reason="per_ip_rate")
    record2 = make_record(ip="5.6.7.8", reason="global_rate")

    async def run_concurrent() -> None:
        await asyncio.gather(
            writer.append(record1),
            writer.append(record2),
        )

    asyncio.run(run_concurrent())

    lines = buf.getvalue().splitlines()
    assert len(lines) == 2
    parsed = [json.loads(line) for line in lines]
    ips = {p["ip"] for p in parsed}
    assert ips == {"1.2.3.4", "5.6.7.8"}
    # Each line must be fully valid JSON (non-interleaved)
    for line in lines:
        json.loads(line)  # would raise if corrupt
