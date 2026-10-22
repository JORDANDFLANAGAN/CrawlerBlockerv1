import time
from datetime import UTC, datetime

from fastapi import FastAPI, Request, Response

from tarpit.detection.log import DetectionRecord
from tarpit.detection.signals import evaluate_request
from tarpit.routers.maze import generate_maze_response
from tarpit.settings import get_settings

BAIT_ID = "root"


def register_detection_middleware(app: FastAPI) -> None:
    """Attach the detection middleware to `app`. Called once from
    `create_app()` after routers are included.

    The middleware body implements §2.1 Middleware contract exactly."""

    @app.middleware("http")
    async def detection_middleware(request: Request, call_next) -> Response:
        # Use app.state.settings if available (allows test injection without
        # clearing the module-level lru_cache); fall back to get_settings().
        app_state = request.app.state
        settings = getattr(app_state, "settings", None) or get_settings()

        # Step 1: kill switch
        if not settings.detection_enabled:
            return await call_next(request)

        # Step 2: always pass /robots.txt through
        if request.url.path == "/robots.txt":
            return await call_next(request)

        # Step 3: extract IP
        ip = request.client.host if request.client else "unknown"

        # Step 4: monotonic clock
        now = time.monotonic()

        # Step 5: record the request (mutates state)
        store = request.app.state.detection_store
        store.record_request(ip, now)

        # Step 6: evaluate signals (pure read)
        triggers = evaluate_request(request, store, now)

        # Step 7: mark maze visit AFTER evaluate so current request doesn't
        # trip prior_maze_visit for itself
        if request.url.path.startswith("/maze/"):
            store.mark_maze_visit(ip)

        # Step 8: act on triggers
        if triggers:
            ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            ua = request.headers.get("user-agent")
            record = DetectionRecord(
                ts=ts,
                ip=ip,
                ua=ua,
                path=request.url.path,
                triggers=triggers,
            )
            await request.app.state.detection_log_writer.append(record)

            if request.url.path.startswith("/maze/"):
                # Flagged on any maze path (valid or invalid) — forward to
                # the route pipeline. Valid ids hit the maze handler; invalid
                # ids hit the path-validation 404 handler. Either way, the
                # detection log line was already written above.
                return await call_next(request)
            else:
                # Flagged on a non-maze path — serve maze content inline
                chain = request.app.state.chain
                log_writer = request.app.state.log_writer
                return await generate_maze_response(
                    seed=BAIT_ID,
                    chain=chain,
                    log_writer=log_writer,
                    settings=settings,
                    request=request,
                )

        # Step 9: not flagged
        return await call_next(request)
