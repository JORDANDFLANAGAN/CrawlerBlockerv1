from fastapi import FastAPI
from fastapi.testclient import TestClient

from tarpit.routers.root import router


def make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


app = make_app()
client = TestClient(app)


def test_root_status_200() -> None:
    response = client.get("/")
    assert response.status_code == 200


def test_root_content_type() -> None:
    response = client.get("/")
    assert "text/html" in response.headers["content-type"]
    assert "utf-8" in response.headers["content-type"]


def test_root_contains_human_copy() -> None:
    response = client.get("/")
    assert "If you can read this" in response.text
    assert "probably a human" in response.text


def test_root_contains_hidden_anchor() -> None:
    response = client.get("/")
    assert 'href="/maze/root"' in response.text
    assert 'style="display:none"' in response.text
