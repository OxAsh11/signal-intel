"""FastAPI application entrypoint for Signal Intel external REST API."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

import api.mcp_server as mcp_module
from api.mcp_server import BearerAuthMiddleware, mcp
from api.routes import (
    accounts,
    activity,
    admin,
    briefs,
    campaigns,
    claims,
    entities,
    meetings,
    tasks,
)
from api.webhooks_inbound import router as webhooks_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Open the connection pool on startup and close it on shutdown."""
    database_url = os.environ.get("DATABASE_URL_SESSION", "")
    if database_url:
        try:
            from psycopg_pool import AsyncConnectionPool

            async def configure_connection(conn):  # type: ignore[no-untyped-def]
                await conn.execute("SET search_path TO public, extensions")
                await conn.execute("SET app.channel = 'api'")
                # The pool discards a connection that configure leaves inside a transaction.
                await conn.commit()

            pool = AsyncConnectionPool(
                conninfo=database_url,
                open=False,
                configure=configure_connection,
                kwargs={"prepare_threshold": None},
            )
            await pool.open()
            app.state.pool = pool
            mcp_module._pool = pool
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
        allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
        allow_credentials=False,  # Bearer token auth — no cookies
        allow_methods=["*"],
        allow_headers=["Authorization", "Content-Type"],
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
        admin.router,
    ):
        app.include_router(router, prefix="/v1")

    # Inbound webhook ingestion (no Bearer auth — HMAC-signed per integration)
    app.include_router(webhooks_router, prefix="/v1")

    # MCP server — SSE sub-app at /mcp, Bearer-auth gated
    app.mount("/mcp", BearerAuthMiddleware(mcp.sse_app(), mcp_module.get_pool))

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
