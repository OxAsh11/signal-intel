"""M8 Business context: generate a workspace's business context from its website."""

from __future__ import annotations

import json
from uuid import UUID

import aiohttp
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

from signal_core.ai_gateway.gateway import AiGateway
from signal_core.ai_gateway.prompts import Capability, load_prompt
from signal_core.connectors.base import FetchGuard
from signal_core.logging import get_logger
from signal_core.metering import CallContext

log = get_logger(__name__)

PROMPT_NAME = "business_context"


class BusinessContext(BaseModel):
    company_name: str | None = None
    offer: str | None = None
    proof_points: list[str] = []


PROMPT_VERSION = 1


class BusinessContextInput:
    def __init__(self, workspace_id: UUID, domain: str, org_id: UUID) -> None:
        self.workspace_id = workspace_id
        self.domain = domain
        self.org_id = org_id


async def run_business_context(
    inp: BusinessContextInput,
    gateway: AiGateway,
    pool: AsyncConnectionPool,
) -> None:
    host = inp.domain.strip().lower()
    if "://" in host:
        from urllib.parse import urlparse

        host = urlparse(host).hostname or host
    base_url = f"https://{host}"
    guard = FetchGuard()
    await guard.check(base_url)
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(base_url) as resp:
            if resp.status >= 400:
                raise RuntimeError(f"http {resp.status}")
            raw = await resp.read()
    text = raw.decode("utf-8", errors="replace")
    import re

    text = re.sub(r"<script.*?</script>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()[:12000]

    prompt = load_prompt(PROMPT_NAME, PROMPT_VERSION)
    ctx = CallContext(
        org_id=inp.org_id,
        workspace_id=inp.workspace_id,
        brief_id=None,
        job_id=None,
        job_type="business.generate",
        job_args={"domain": inp.domain},
    )
    result = await gateway.run(Capability.WRITE, prompt, {"site_text": text}, BusinessContext, ctx)
    value = result.value
    lines = value.proof_points or []
    context = {
        "company_name": value.company_name,
        "offer": value.offer,
        "lines": [str(x) for x in lines][:5],
    }
    async with pool.connection() as conn:
        sql = "select app.set_business_context(%s, %s)"
        await conn.execute(sql, (str(inp.workspace_id), json.dumps(context)))
