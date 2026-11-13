"""M2 Radar AI — generate search queries from brief hypotheses and ICP."""

from __future__ import annotations

from signal_core.ai_gateway.gateway import AiGateway
from signal_core.ai_gateway.prompts import Capability, load_prompt
from signal_core.metering import CallContext
from signal_core.modules.m02_radar import RadarQuerySet

PROMPT_NAME = "radar_queries"
PROMPT_VERSION = 2


async def generate_radar_queries(
    gateway: AiGateway,
    hypotheses: list[str],
    icp_summary: str,
    connector_type: str,
    ctx: CallContext,
    limit: int = 10,
    *,
    geographies: list[str] | None = None,
) -> RadarQuerySet:
    """Call the AI gateway to generate radar search queries.

    Args:
        gateway: The AI gateway to use for the call.
        hypotheses: List of hypothesis strings from the brief.
        icp_summary: Textual summary of the ICP.
        connector_type: The radar's connector type (news, job_post, website, registry,
            tender, event, directory, social_post).
        ctx: Metering call context.
        limit: Maximum number of queries to generate.
        geographies: The Brief's ICP geographies, for local-language queries and lexicon.

    Returns:
        A RadarQuerySet: queries sourced from "hypothesis" or "icp", plus the pre-filter lexicon.
    """
    prompt = load_prompt(PROMPT_NAME, PROMPT_VERSION)
    hypotheses_text = "\n".join(f"- {h}" for h in hypotheses) if hypotheses else "(none)"
    result = await gateway.run(
        Capability.WRITE,
        prompt,
        {
            "hypotheses": hypotheses_text,
            "icp_summary": icp_summary or "(none)",
            "connector_type": connector_type,
            "geographies": ", ".join(geographies or []) or "(any)",
            "limit": str(limit),
        },
        RadarQuerySet,
        ctx,
        max_tokens=1024,
    )
    return result.value
