"""M1 Brief Studio — Python side: AI intake job."""

import json
from uuid import UUID

from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

from signal_core.ai_gateway.gateway import AiGateway
from signal_core.ai_gateway.outputs import BriefDraft
from signal_core.ai_gateway.prompts import Capability, load_prompt
from signal_core.brief_columns import clean_columns
from signal_core.logging import get_logger
from signal_core.metering import CallContext

log = get_logger(__name__)

PROMPT_NAME = "brief_intake"
PROMPT_VERSION = 3
MAX_ASSUMPTIONS = 5


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
        existing = await _existing_predicates(pool, inp.workspace_id)
        # The prompt sees key, label and value_type only (no prompt change in P3).
        shown = [{k: p[k] for k in ("key", "label", "value_type")} for p in existing]
        result = await gateway.run(
            Capability.WRITE,
            prompt,
            {"description": inp.description, "existing_predicates": json.dumps(shown)},
            BriefDraft,
            ctx,
        )
        config = result.value.model_dump(mode="json")
    except Exception:
        async with pool.connection() as conn:
            await conn.execute("select app.fail_brief_intake(%s)", (str(inp.brief_id),))
        raise
    raw_name = config.pop("name", None)
    name = raw_name.strip() if isinstance(raw_name, str) else ""
    config["assumptions"] = [
        a.strip() for a in config.get("assumptions") or [] if isinstance(a, str) and a.strip()
    ][:MAX_ASSUMPTIONS]
    existing_types = {p["key"]: p["value_type"] for p in existing}
    existing_fills = {p["key"]: p["fill_method"] for p in existing}
    specs, rules, dropped = clean_columns(
        config["predicates"], config["evidence_rules"], existing_types, existing_fills
    )
    for reason in dropped:
        log.warning("brief_intake.column_dropped", brief_id=str(inp.brief_id), reason=reason)
    config["predicates"] = [s.model_dump(mode="json") for s in specs]
    config["evidence_rules"] = [r.model_dump(mode="json") for r in rules]
    async with pool.connection() as conn:
        await conn.execute(
            "select app.save_brief_draft(%s, %s)",
            (str(inp.brief_id), json.dumps(config)),
        )
        if name:
            await conn.execute("select app.apply_drafted_name(%s, %s)", (str(inp.brief_id), name))


async def _existing_predicates(
    pool: AsyncConnectionPool, workspace_id: UUID
) -> list[dict[str, str]]:
    """The workspace's predicates the intake prompt asks the AI to reuse, with the fill_method
    each is held with (a drafted column may not reuse a key filled another way).

    Global predicates (no workspace) are not offered: a Brief's columns live in its workspace."""
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "SELECT key, label, value_type, fill_method FROM public.predicate"
            " WHERE workspace_id = %s ORDER BY key",
            (str(workspace_id),),
        )
        rows = await cur.fetchall()
    return [{"key": r[0], "label": r[1], "value_type": r[2], "fill_method": r[3]} for r in rows]
