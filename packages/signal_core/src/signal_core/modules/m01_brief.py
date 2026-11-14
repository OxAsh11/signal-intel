"""M1 Brief Studio — Python side: AI intake job."""

import json
from uuid import UUID

from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

from signal_core.ai_gateway.gateway import AiGateway
from signal_core.ai_gateway.outputs import BriefDraft
from signal_core.ai_gateway.prompts import Capability, load_prompt
from signal_core.metering import CallContext

PROMPT_NAME = "brief_intake"
PROMPT_VERSION = 2


class BriefIntakeInput(BaseModel):
    brief_id: UUID
    description: str
    org_id: UUID
    workspace_id: UUID


async def run_brief_intake(
    inp: BriefIntakeInput,
    gateway: AiGateway,
    pool: AsyncConnectionPool,
) -> None:
    prompt = load_prompt(PROMPT_NAME, PROMPT_VERSION)
    ctx = CallContext(
        org_id=inp.org_id,
        workspace_id=inp.workspace_id,
        brief_id=inp.brief_id,
        job_id=None,
        job_type="brief.intake",
        job_args={"description": inp.description},
    )
    try:
        result = await gateway.run(
            Capability.WRITE, prompt, {"description": inp.description}, BriefDraft, ctx
        )
        config = result.value.model_dump()
    except Exception:
        async with pool.connection() as conn:
            await conn.execute("select app.fail_brief_intake(%s)", (str(inp.brief_id),))
        raise
    async with pool.connection() as conn:
        await conn.execute(
            "select app.save_brief_draft(%s, %s)",
            (str(inp.brief_id), json.dumps(config)),
        )
