"""Radar query generation, prompt v2: per-type query styles and the pre-filter lexicon."""

from typing import Any
from uuid import uuid4

import pytest

from signal_core.ai_gateway.prompts import load_prompt, render
from signal_core.metering import CallContext
from signal_core.modules import m02_radar_ai
from signal_core.modules.m02_radar import RadarQuerySet


def test_radar_queries_v2_renders_all_kinds():
    prompt = load_prompt("radar_queries", 2)
    text = render(
        prompt,
        {
            "hypotheses": "- x",
            "icp_summary": "y",
            "connector_type": "tender",
            "geographies": "Germany",
            "limit": "10",
        },
    )
    assert "Germany" in text
    for word in ("registry", "tender", "event", "directory", "social_post", "lexicon"):
        assert word in prompt.system


class StubGateway:
    def __init__(self, value: Any) -> None:
        self.value = value
        self.variables: dict[str, str] = {}

    async def run(self, capability, prompt, variables, model, ctx, **_: Any):
        self.variables = dict(variables)
        assert prompt.version == 2

        class R:
            pass

        r = R()
        r.value = model.model_validate(self.value)  # type: ignore[attr-defined]
        return r


@pytest.mark.asyncio
async def test_generate_returns_lexicon_and_passes_geographies():
    gw = StubGateway(
        {
            "queries": [{"query_text": "hotel tender", "source": "icp"}],
            "lexicon": ["tender", "Ausschreibung"],
        }
    )
    ctx = CallContext(uuid4(), uuid4(), None, None, "radar.generate_queries", {})
    result = await m02_radar_ai.generate_radar_queries(
        gw,
        ["Hotels"],
        "CAD",
        "tender",
        ctx,
        geographies=["Germany", "Austria"],  # type: ignore[arg-type]
    )
    assert isinstance(result, RadarQuerySet)
    assert result.lexicon == ["tender", "Ausschreibung"]
    assert gw.variables["geographies"] == "Germany, Austria"
