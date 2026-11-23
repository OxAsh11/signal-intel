"""The worker's /providers/status endpoint: token-gated and free of key material."""

import asyncio
import json
from typing import Any

from pydantic import SecretStr

from signal_core.config import Settings
from signal_core.providers import status as provider_status
from worker.health import PROVIDER_STATUS_PATH, HealthState, handle_provider_status, serve_health

SECRET = "sk-this-key-must-never-leave-the-worker"


def rows() -> list[dict[str, Any]]:
    settings = Settings(
        leadmagic_api_key=SecretStr(SECRET),
        findymail_api_key=SecretStr(SECRET),
        prospeo_api_key=SecretStr(SECRET),
        pdl_api_key=SecretStr(SECRET),
    )
    return provider_status(settings)  # type: ignore[return-value]


def call(
    token: str | None = "secret",
    method: str = "GET",
    path: str = PROVIDER_STATUS_PATH,
    auth: str = "Bearer secret",
    status: Any = rows,
):
    return handle_provider_status(token, method, path, {"authorization": auth}, status)


def test_provider_status_needs_the_shared_token() -> None:
    assert call(token=None)[0] == 503
    assert call(auth="Bearer wrong")[0] == 401
    assert call(auth="")[0] == 401


def test_provider_status_only_answers_its_route_and_get() -> None:
    assert call(path="/healthz")[0] == 404
    assert call(method="POST")[0] == 405


def test_provider_status_never_returns_key_material() -> None:
    code, body = call()
    assert code == 200
    assert len(body["providers"]) == 6
    assert body["providers"][0]["key"] == "leadmagic"
    assert body["providers"][0]["configured"] is True
    assert json.dumps(body).count(SECRET) == 0
    assert "api_key" not in json.dumps(body)
    assert "secret" not in json.dumps(body).lower()


async def test_provider_status_over_http() -> None:
    server = await serve_health(HealthState(), port=0, token="secret", status=rows)
    port = server.sockets[0].getsockname()[1]

    async def get(auth: str) -> tuple[str, str]:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(
            f"GET {PROVIDER_STATUS_PATH} HTTP/1.1\r\nHost: x\r\nAuthorization: {auth}\r\n\r\n".encode()
        )
        await writer.drain()
        raw = await reader.read()
        writer.close()
        head, _, body = raw.partition(b"\r\n\r\n")
        return head.decode(), body.decode()

    ok_head, ok_body = await get("Bearer secret")
    bad_head, _ = await get("Bearer wrong")
    server.close()
    assert ok_head.startswith("HTTP/1.1 200")
    assert json.loads(ok_body)["providers"][0]["configured"] is True
    assert SECRET not in ok_body
    assert bad_head.startswith("HTTP/1.1 401")
