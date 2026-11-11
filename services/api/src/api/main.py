"""FastAPI application entrypoint for Signal Intel external REST API."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from api.routes import accounts, activity, briefs, campaigns, claims, entities, meetings, tasks


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Open the connection pool on startup and close it on shutdown."""
    database_url = os.environ.get("DATABASE_URL_SESSION", "")
    if database_url:
        try:
            from psycopg_pool import AsyncConnectionPool

            async def configure_connection(conn):  # type: ignore[no-untyped-def]
                await conn.execute("SET app.channel = 'api'")

            pool = AsyncConnectionPool(
                conninfo=database_url,
                open=False,
                configure=configure_connection,
                kwargs={"prepare_threshold": None},
            )
            await pool.open()
            app.state.pool = pool
        except Exception:
            app.state.pool = None
    else:
        app.state.pool = None

    yield

    if getattr(app.state, "pool", None) is not None:
        await app.state.pool.close()


def build_app() -> FastAPI:
    app = FastAPI(
        title="Signal Intel API",
        version="1.0.0",
        docs_url="/v1/docs",
        openapi_url="/v1/openapi.json",
        redoc_url=None,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next: object) -> Response:
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        response: Response = await call_next(request)  # type: ignore[call-arg]
        response.headers["X-Request-ID"] = request_id
        return response

    # Health check — no auth required
    @app.get("/v1/health", tags=["health"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    # Register all resource routers under /v1
    for router in (
        entities.router,
        accounts.router,
        claims.router,
        briefs.router,
        campaigns.router,
        activity.router,
        tasks.router,
        meetings.router,
    ):
        app.include_router(router, prefix="/v1")

    return app


application = build_app()


def run() -> None:
    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(
        "api.main:application",
        host="0.0.0.0",
        port=port,
        log_level="info",
    )


if __name__ == "__main__":
    run()
