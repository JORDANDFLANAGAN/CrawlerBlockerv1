import asyncio
import json
from dataclasses import dataclass
from typing import TextIO


@dataclass(frozen=True)
class AbuseRecord:
    ts: str          # ISO-8601 UTC with trailing 'Z'
    ip: str          # request.client.host, "unknown" if absent
    ua: str | None   # User-Agent header, None if absent
    path: str        # request.url.path, no query string
    reason: str      # one of the REASON_* constants


class AbuseLogWriter:
    def __init__(self, file: TextIO, lock: asyncio.Lock) -> None:
        self._file = file
        self._lock = lock

    async def append(self, record: AbuseRecord) -> None:
        """One JSON line, newline-terminated. Key order:
        ts, ip, ua, path, reason. Flush under lock via asyncio.to_thread."""
        line = json.dumps(
            {
                "ts": record.ts,
                "ip": record.ip,
                "ua": record.ua,
                "path": record.path,
                "reason": record.reason,
            }
        ) + "\n"

        async with self._lock:
            await asyncio.to_thread(self._write_and_flush, line)

    def _write_and_flush(self, line: str) -> None:
        self._file.write(line)
        self._file.flush()
