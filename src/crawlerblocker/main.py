from fastapi import FastAPI

from crawlerblocker.routers import health


def create_app() -> FastAPI:
    app = FastAPI(title="CrawlerBlocker", version="0.1.0")
    app.include_router(health.router)
    return app


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run(
        "crawlerblocker:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
    )
