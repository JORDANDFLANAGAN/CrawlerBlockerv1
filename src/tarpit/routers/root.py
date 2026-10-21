from fastapi import APIRouter
from fastapi.responses import HTMLResponse, PlainTextResponse

from tarpit.renderer import render_robots_txt, render_root_page

router = APIRouter()


@router.get("/robots.txt")
def get_robots_txt() -> PlainTextResponse:
    return PlainTextResponse(render_robots_txt())


@router.get("/")
def get_root() -> HTMLResponse:
    return HTMLResponse(render_root_page())
