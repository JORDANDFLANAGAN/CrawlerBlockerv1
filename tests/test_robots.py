from fastapi import FastAPI
from fastapi.testclient import TestClient

from tarpit.routers.root import router


def make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


client = TestClient(make_app())


def test_robots_status_200() -> None:
    response = client.get("/robots.txt")
    assert response.status_code == 200


def test_robots_content_type() -> None:
    response = client.get("/robots.txt")
    assert "text/plain" in response.headers["content-type"]
    assert "utf-8" in response.headers["content-type"]


def test_robots_body_exact() -> None:
    response = client.get("/robots.txt")
    assert response.text == "User-agent: *\nDisallow: /maze/\n"
