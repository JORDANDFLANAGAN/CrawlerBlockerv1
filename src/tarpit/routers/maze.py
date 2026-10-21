import asyncio
import logging
import random
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

from tarpit.access_log import AccessLogWriter, AccessRecord
from tarpit.dependencies import get_chain, get_log_writer, get_settings_dep
from tarpit.ids import generate_new_ids
from tarpit.markov import MarkovChain
from tarpit.renderer import render_maze_page
from tarpit.settings import Settings

logger = logging.getLogger("tarpit")

router = APIRouter()

WORDS_PER_PAGE = 200


@router.get("/maze/{id}")
async def get_maze_page(
    request: Request,
    id: str = Path(..., pattern=r"^[A-Za-z0-9._~-]{1,64}$"),
    chain: MarkovChain = Depends(get_chain),
    log_writer: AccessLogWriter = Depends(get_log_writer),
    settings: Settings = Depends(get_settings_dep),
) -> HTMLResponse:
    try:
        rng = random.Random(id)
        words = chain.generate(rng, WORDS_PER_PAGE)
        k = rng.randint(5, 10)
        new_ids = generate_new_ids(rng, k)
        html = render_maze_page(id, words, new_ids)
    except Exception as exc:
        logger.error("Generation failed for id=%r: %s", id, exc, exc_info=True)
        return PlainTextResponse("internal error", status_code=500)

    delay = random.uniform(settings.delay_min_s, settings.delay_max_s)
    await asyncio.sleep(delay)

    ip = request.client.host if request.client else "unknown"
    ua = request.headers.get("user-agent")
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    record = AccessRecord(
        ts=ts,
        ip=ip,
        ua=ua,
        path=request.url.path,
        delay_s=round(delay, 2),
    )
    await log_writer.append(record)

    return HTMLResponse(html)
