"""Postgres access for Python services (psycopg 3 async pool)."""

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

# Procrastinate lives in its own schema (migration 20261106000006).
WORKER_SEARCH_PATH = "procrastinate, public, extensions"


async def configure_worker_connection(conn: AsyncConnection) -> None:
    """Runs on every new pooled connection. Session pooler keeps this state for the connection."""
    await conn.execute(f"set search_path to {WORKER_SEARCH_PATH}")
    await conn.execute("set app.channel to 'system'")
    await conn.commit()


def make_pool(conninfo: str, *, min_size: int = 1, max_size: int = 5) -> AsyncConnectionPool:
    return AsyncConnectionPool(
        conninfo,
        min_size=min_size,
        max_size=max_size,
        open=False,
        configure=configure_worker_connection,
        kwargs={
            "autocommit": False,
            "prepare_threshold": None,
        },  # poolers dislike prepared statements
    )
