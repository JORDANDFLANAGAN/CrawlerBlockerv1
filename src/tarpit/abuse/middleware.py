import time
from datetime import UTC, datetime

from fastapi import FastAPI, Request, Response

from tarpit.abuse.log import AbuseLogWriter, AbuseRecord
from tarpit.abuse.reasons import REASON_GLOBAL_RATE, REASON_PER_IP_RATE
from tarpit.abuse.state import AbuseStateStore
from tarpit.settings import get_settings


def register_abuse_middleware(app: FastAPI) -> None:
    """Attach the abuse-protection middleware to `app`. Called once from
    create_app() after register_detection_middleware(app).

    FastAPI reverses middleware registration order, so this runs FIRST
    on the request path (satisfying REQ-SCOPE-03)."""

    @app.middleware("http")
    async def abuse_middleware(request: Request, call_next) -> Response:
        app_state = request.app.state
        settings = getattr(app_state, "settings", None) or get_settings()

        # Step 1: kill switch
        if not settings.abuse_protection_enabled:
            return await call_next(request)

        # Step 2: /robots.txt always passes through
        if request.url.path == "/robots.txt":
            return await call_next(request)

        # Step 3: scope check — only / and /maze/* are subject to limits
        path = request.url.path
        if path != "/" and not path.startswith("/maze/"):
            return await call_next(request)

        # Step 4: extract IP
        ip = request.client.host if request.client else "unknown"

        # Step 5: monotonic clock
        now = time.monotonic()

        store: AbuseStateStore = request.app.state.abuse_store

        # Step 6: record every request unconditionally (REQ-RL-05)
        store.record_request(ip, now)

        # Step 7: evaluate limits in fixed order, short-circuit on first breach
        breach_reason: str | None = None

        if store.is_per_ip_rate_exceeded(ip, now):
            breach_reason = REASON_PER_IP_RATE
        elif store.is_global_rate_exceeded(now):
            breach_reason = REASON_GLOBAL_RATE
        else:
            breach_reason = store.try_acquire_slot(ip)

        # Step 8: on breach — log, signal detection, return 429
        if breach_reason is not None:
            ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            ua = request.headers.get("user-agent")
            record = AbuseRecord(ts=ts, ip=ip, ua=ua, path=path, reason=breach_reason)
            log_writer: AbuseLogWriter = request.app.state.abuse_log_writer
            await log_writer.append(record)

            _signal_detection(request, ip, now, settings)

            return Response(
                status_code=429,
                content=b"",
                media_type="text/plain",
            )

        # Step 9: no breach — wrap call_next in try/finally to release slot
        try:
            return await call_next(request)
        finally:
            store.release_slot(ip)


def _signal_detection(request: Request, ip: str, now: float, settings) -> None:
    """Call flag_for_detection on the DetectionStateStore unless detection is disabled."""
    if not settings.detection_enabled:
        return
    detection_store = getattr(request.app.state, "detection_store", None)
    if detection_store is not None:
        detection_store.flag_for_detection(ip, now)
