import asyncio
import json
from dataclasses import dataclass
from typing import TextIO


@dataclass(frozen=True)
class DetectionRecord:
    ts: str              # ISO-8601 UTC with trailing 'Z'
    ip: str              # from request.client.host, "unknown" if absent
    ua: str | None       # User-Agent header value, None if absent
    path: str            # request.url.path, no query string
    triggers: list[str]  # from SIGNAL_* constants, in signals.py evaluate order


class DetectionLogWriter:
    def __init__(self, file: TextIO, lock: asyncio.Lock) -> None:
        self._file = file
        self._lock = lock

    async def append(self, record: DetectionRecord) -> None:
        """Serialise `record` to one JSON line (newline-terminated).
        Key order: ts, ip, ua, path, triggers.
        Blocking write + flush runs via asyncio.to_thread under the lock."""
        line = json.dumps(
            {
                "ts": record.ts,
                "ip": record.ip,
                "ua": record.ua,
                "path": record.path,
                "triggers": record.triggers,
            }
        ) + "\n"

        async with self._lock:
            await asyncio.to_thread(self._write_and_flush, line)

    def _write_and_flush(self, line: str) -> None:
        self._file.write(line)
        self._file.flush()
