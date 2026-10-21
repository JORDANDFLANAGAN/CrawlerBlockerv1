import asyncio
import json
from dataclasses import dataclass
from typing import TextIO


@dataclass(frozen=True)
class AccessRecord:
    ts: str          # ISO-8601 UTC with trailing 'Z', e.g. "2026-10-21T10:40:12Z"
    ip: str          # dotted-quad or IPv6; "unknown" if neither header nor peer available
    ua: str | None   # value of User-Agent header, or None if absent
    path: str        # full request path, e.g. "/maze/a8f3"
    delay_s: float   # the applied delay, rounded to 2 d.p.


class AccessLogWriter:
    def __init__(self, file: TextIO, lock: asyncio.Lock) -> None:
        self._file = file
        self._lock = lock

    async def append(self, record: AccessRecord) -> None:
        """Serialise `record` to one JSON line (newline-terminated),
        write-and-flush under the lock. JSON key order: ts, ip, ua, path, delay_s.
        Blocking file I/O is run via asyncio.to_thread."""
        line = json.dumps(
            {
                "ts": record.ts,
                "ip": record.ip,
                "ua": record.ua,
                "path": record.path,
                "delay_s": round(record.delay_s, 2),
            }
        ) + "\n"

        async with self._lock:
            await asyncio.to_thread(self._write_and_flush, line)

    def _write_and_flush(self, line: str) -> None:
        self._file.write(line)
        self._file.flush()
