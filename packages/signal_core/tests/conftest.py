import asyncio
import os
import selectors
import sys
from collections.abc import Callable

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


def pytest_asyncio_loop_factories(
    config: pytest.Config, item: pytest.Item
) -> dict[str, Callable[[], asyncio.AbstractEventLoop]]:
    # psycopg's async mode cannot run on Windows' default Proactor loop.
    if sys.platform == "win32":
        return {"selector": lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())}
    return {"default": asyncio.new_event_loop}
