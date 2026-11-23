import asyncio
import json

from worker.reveal import REVEAL_PATH, handle_reveal, serve_reveal

WS = "11111111-1111-4111-8111-111111111111"
CP = "22222222-2222-4222-8222-222222222222"
BODY = json.dumps({"workspace_id": WS, "contact_point_id": CP}).encode()


async def lookup(workspace_id: str, contact_id: str) -> str | None:
    return "jane@acme.example" if (workspace_id, contact_id) == (WS, CP) else None


def call(
    token: str | None = "secret",
    body: bytes = BODY,
    path: str = REVEAL_PATH,
    method: str = "POST",
    auth: str = "Bearer secret",
):
    return handle_reveal(lookup, token, method, path, {"authorization": auth}, body)


async def test_reveal_needs_the_shared_token() -> None:
    assert (await call(token=None))[0] == 503
    assert (await call(auth="Bearer wrong"))[0] == 401
    assert (await call(auth=""))[0] == 401


async def test_reveal_validates_its_request() -> None:
    assert (await call(path="/healthz"))[0] == 404
    assert (await call(method="GET"))[0] == 405
    assert (await call(body=b"not json"))[0] == 400
    assert (await call(body=json.dumps({"workspace_id": "x", "contact_point_id": CP}).encode()))[
        0
    ] == 400
    assert (
        await call(body=json.dumps({"workspace_id": WS, "contact_point_id": "not-a-uuid"}).encode())
    )[0] == 400


async def test_reveal_returns_one_email_for_one_contact() -> None:
    status, payload = await call()
    assert status == 200
    assert payload == {"email": "jane@acme.example"}
    assert (await call(body=json.dumps({"workspace_id": WS, "contact_point_id": WS}).encode()))[
        0
    ] == 404


async def test_reveal_http_endpoint() -> None:
    server = await serve_reveal(lookup, "secret", port=0)
    port = server.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    request = (
        f"POST {REVEAL_PATH} HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer secret\r\n"
        f"Content-Type: application/json\r\nContent-Length: {len(BODY)}\r\n\r\n"
    ).encode() + BODY
    writer.write(request)
    await writer.drain()
    raw = await reader.read()
    writer.close()
    server.close()
    head, _, body = raw.partition(b"\r\n\r\n")
    assert head.startswith(b"HTTP/1.1 200")
    assert json.loads(body)["email"] == "jane@acme.example"
