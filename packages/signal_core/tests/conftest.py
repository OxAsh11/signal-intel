import asyncio
import os
import sys

import pytest

from signal_core.config import Settings


def _database_url() -> str | None:
    url = os.environ.get("DATABASE_URL_SESSION")
    if url:
        return url
    secret = Settings().database_url_session  # also reads the root .env
    return secret.get_secret_value() if secret else None


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if _database_url():
        return
    skip = pytest.mark.skip(reason="DATABASE_URL_SESSION is not set")
    for item in items:
        if "db" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def event_loop_policy() -> asyncio.AbstractEventLoopPolicy:
    # psycopg's async mode cannot run on Windows' default Proactor loop.
    if sys.platform == "win32":
        return asyncio.WindowsSelectorEventLoopPolicy()
    return asyncio.DefaultEventLoopPolicy()
