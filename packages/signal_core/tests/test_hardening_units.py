"""Logic that needs no database. What these modules do against the schema is proven end to end
in services/worker/tests/test_pipeline_e2e.py."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from cryptography.fernet import Fernet

import signal_core.modules.m05_verdict as m05
import signal_core.modules.m13_integrations as m13
from signal_core import crypto
from signal_core.ai_gateway.errors import AiProviderError
from signal_core.ai_gateway.prompts import load_prompt, render
from signal_core.briefs import ActiveBrief
from signal_core.config import get_settings
from signal_core.metering import CallContext
from signal_core.modules.m07_enrichment import HunterProvider, enrich_account, match_persona
from signal_core.modules.m08_scoring import _band_from_priority, _decay, _settings
from signal_core.modules.m09_activation import _variables
from signal_core.modules.m10_inbox import classify_reply
from signal_core.modules.m11_stage import TRANSITIONS


def _brief(
    config: dict[str, Any] | None = None, weights: dict[str, Any] | None = None
) -> ActiveBrief:
    return ActiveBrief(uuid4(), uuid4(), uuid4(), config or {}, weights or {})


def _ctx() -> CallContext:
    return CallContext(uuid4(), uuid4(), None, None, "test", {})


@pytest.fixture
def secrets_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRETS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ── crypto ────────────────────────────────────────────────────────────────────


def test_secret_round_trip_and_ciphertext_differs(secrets_key: None) -> None:
    stored = crypto.encrypt_secret("s3cret")
    assert stored.startswith(crypto.PREFIX) and "s3cret" not in stored
    assert crypto.decrypt_secret(stored) == "s3cret"
    assert crypto.encrypt_secret("s3cret") != stored  # never deterministic


def test_lookup_hash_is_stable_and_normalised(secrets_key: None) -> None:
    assert crypto.lookup_hash(" Jane@Acme.com ") == crypto.lookup_hash("jane@acme.com")
    assert crypto.lookup_hash("jane@acme.com") != crypto.lookup_hash("john@acme.com")


def test_rotation_reads_old_ciphertext(monkeypatch: pytest.MonkeyPatch) -> None:
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    monkeypatch.setenv("SECRETS_ENCRYPTION_KEY", old)
    get_settings.cache_clear()
    stored, digest = crypto.encrypt_secret("value"), crypto.lookup_hash("value")
    monkeypatch.setenv("SECRETS_ENCRYPTION_KEY", f"{new},{old}")
    get_settings.cache_clear()
    assert crypto.decrypt_secret(stored) == "value"
    assert crypto.lookup_hash("value") == digest
    get_settings.cache_clear()


def test_secrets_need_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SECRETS_ENCRYPTION_KEY", raising=False)
    monkeypatch.setattr(crypto, "get_settings", lambda: MagicMock(secrets_encryption_key=None))
    with pytest.raises(crypto.SecretsNotConfigured):
        crypto.encrypt_secret("x")
    with pytest.raises(ValueError):
        crypto.decrypt_secret("plain text")


# ── M13 webhooks ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/hook",
        "https://127.0.0.1/hook",
        "https://10.0.0.8/x",
        "ftp://example.com",
        "nonsense",
    ],
)
def test_webhook_url_must_be_public_https(url: str) -> None:
    with pytest.raises(m13.WebhookUrlBlocked):
        m13.check_webhook_url(url)


def test_webhook_body_is_canonical_and_signed() -> None:
    event_id = uuid4()
    a = m13._serialise(m13.canonical_body(event_id, "task.created", {"b": 1, "a": 2}))
    b = m13._serialise(m13.canonical_body(event_id, "task.created", {"a": 2, "b": 1}))
    assert a == b
    assert m13.sign("k", a) == m13.sign("k", b) != m13.sign("other", a)


def test_retry_schedule_covers_most_of_a_day() -> None:
    assert len(m13.RETRY_DELAYS) + 1 == m13.MAX_ATTEMPTS
    assert sorted(m13.RETRY_DELAYS) == m13.RETRY_DELAYS
    assert 12 * 3600 < sum(m13.RETRY_DELAYS) < 24 * 3600


# ── M5 verdict ────────────────────────────────────────────────────────────────


def _pool(entity_row: Any, claims: list[tuple[Any, ...]]) -> MagicMock:
    cur = MagicMock()
    cur.execute = AsyncMock()
    # First the entity's name; after that "no verdict stored yet" for each Brief.
    cur.fetchone = AsyncMock(side_effect=[entity_row, *([None] * 8)])
    cur.fetchall = AsyncMock(return_value=claims)
    conn = MagicMock()
    conn.cursor.return_value.__aenter__ = AsyncMock(return_value=cur)
    conn.cursor.return_value.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.connection.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.connection.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool


async def test_verdict_none_without_an_active_brief(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(m05, "active_brief_versions", AsyncMock(return_value=[]))
    gateway = MagicMock()
    assert await m05.compute_verdict(MagicMock(), gateway, uuid4(), uuid4(), _ctx()) is None
    gateway.run.assert_not_called()


async def test_verdict_is_written_for_every_active_brief(monkeypatch: pytest.MonkeyPatch) -> None:
    briefs = [_brief({"hypothesis": "one"}), _brief({"hypothesis": "two"})]
    monkeypatch.setattr(m05, "active_brief_versions", AsyncMock(return_value=briefs))
    monkeypatch.setattr(m05, "_defer_account_card", AsyncMock())
    upsert = AsyncMock()
    monkeypatch.setattr(m05, "upsert_verdict", upsert)

    # No claims: Unverified under each Brief, without an AI call.
    gateway = MagicMock()
    result = await m05.compute_verdict(_pool(("Acme",), []), gateway, uuid4(), uuid4(), _ctx())
    assert result == "Unverified"
    gateway.run.assert_not_called()
    assert [c.args[1] for c in upsert.call_args_list] == [b.brief_version_id for b in briefs]


async def test_confirmed_needs_a_quoted_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(m05, "active_brief_versions", AsyncMock(return_value=[_brief()]))
    monkeypatch.setattr(m05, "_defer_account_card", AsyncMock())
    upsert = AsyncMock()
    monkeypatch.setattr(m05, "upsert_verdict", upsert)
    answer = m05.VerdictOutput(value="Confirmed", reasoning="r", supporting_claim_indices=[0, 7])
    gateway = MagicMock()
    gateway.run = AsyncMock(return_value=MagicMock(value=answer, extractor_version_id=uuid4()))

    unquoted = [(uuid4(), "uses_tool", True, None, 90)]
    assert (
        await m05.compute_verdict(_pool(("Acme",), unquoted), gateway, uuid4(), uuid4(), _ctx())
        == "Likely"
    )

    quoted = [(uuid4(), "uses_tool", True, "designs in Revit", 90)]
    assert (
        await m05.compute_verdict(_pool(("Acme",), quoted), gateway, uuid4(), uuid4(), _ctx())
        == "Confirmed"
    )
    assert upsert.call_args.args[4] == [quoted[0][0]]  # out-of-range index 7 is ignored
    assert upsert.call_args.args[5] == [
        {"claim_id": str(quoted[0][0]), "quote": "designs in Revit"}
    ]


async def test_ai_failure_never_writes_a_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(m05, "active_brief_versions", AsyncMock(return_value=[_brief()]))
    upsert = AsyncMock()
    monkeypatch.setattr(m05, "upsert_verdict", upsert)
    gateway = MagicMock()
    gateway.run = AsyncMock(side_effect=AiProviderError("5xx"))
    with pytest.raises(AiProviderError):
        await m05.compute_verdict(
            _pool(("Acme",), [(uuid4(), "k", 1, "q", 50)]), gateway, uuid4(), uuid4(), _ctx()
        )
    upsert.assert_not_called()


# ── M8 scoring ────────────────────────────────────────────────────────────────


def test_scoring_settings_defaults_and_overrides() -> None:
    weights, thresholds, roles = _settings(_brief())
    assert weights == {"fit": 0.25, "evidence": 0.25, "scale": 0.25, "timing": 0.25}
    assert thresholds == {"High": 70.0, "Medium": 40.0} and roles == []

    brief = _brief(
        {
            "personas": {"buyer": ["CFO"], "user": [], "influencer": ["Architect"]},
            "weights": {"fit": 0.1},
        },
        {"evidence": 0.7, "band_thresholds": {"High": 80}},
    )
    weights, thresholds, roles = _settings(brief)
    assert weights["fit"] == 0.1 and weights["evidence"] == 0.7
    assert thresholds == {"High": 80.0, "Medium": 40.0}
    assert roles == ["buyer", "influencer"]  # only roles the Brief actually names titles for


def test_claim_weight_halves_each_half_life() -> None:
    now = datetime.now(UTC)
    assert _decay(now, 90, now) == 1.0
    assert _decay(now - timedelta(days=90), 90, now) == pytest.approx(0.5)
    assert _decay(now - timedelta(days=180), 90, now) == pytest.approx(0.25)
    assert _decay(now - timedelta(days=500), None, now) == 1.0


def test_band_thresholds() -> None:
    thresholds = {"High": 70.0, "Medium": 40.0}
    assert [_band_from_priority(p, thresholds) for p in (70, 69.9, 40, 39.9)] == [
        "High",
        "Medium",
        "Medium",
        "Low",
    ]


# ── M9 / M10 / M11 ────────────────────────────────────────────────────────────


def test_push_variables_carry_the_quote() -> None:
    variables = _variables("High", "Confirmed", [{"claim_id": "c1", "quote": "uses X"}])
    assert variables == {
        "band": "High",
        "verdict": "Confirmed",
        "quote": "uses X",
        "quote_claim_id": "c1",
        "copy_variant": None,
    }
    assert _variables(None, None, None)["quote"] is None
    assert _variables("Low", "Likely", '[{"quote": "q"}]')["quote"] == "q"


async def test_classify_reply_rejects_unknown_class() -> None:
    with pytest.raises(ValueError):
        await classify_reply(MagicMock(), uuid4(), "Maybe", None, "ai")


def test_stage_transitions_follow_the_prd() -> None:
    assert "reply_received" not in TRANSITIONS  # a raw reply never moves a stage
    assert TRANSITIONS["genuine_reply"]["Contacted"] == "Engaged"
    assert TRANSITIONS["genuine_reply"]["Dormant"] == "Engaged"  # back into the flow
    assert TRANSITIONS["campaign_member_added"] == {"Untouched": "Queued"}
    assert TRANSITIONS["message_sent"]["Queued"] == "Contacted"
    assert TRANSITIONS["marked_disqualified"] == {"*": "Disqualified"}
    assert set(TRANSITIONS["dormant_timeout"].values()) == {"Dormant"}


# ── M7 enrichment ─────────────────────────────────────────────────────────────


def test_match_persona() -> None:
    personas = {"buyer": ["Head of BIM", "CTO"], "user": ["BIM coordinator"]}
    assert match_persona("Global Head of BIM", personas) == "buyer"
    assert match_persona("Senior BIM Coordinator", personas) == "user"
    assert match_persona("Receptionist", personas) is None
    assert match_persona(None, personas) is None


async def test_hunter_maps_statuses(monkeypatch: pytest.MonkeyPatch) -> None:
    body = {
        "data": {
            "emails": [
                {
                    "value": "jane@acme.com",
                    "first_name": "Jane",
                    "last_name": "Doe",
                    "position": "CTO",
                    "verification": {"status": "valid", "date": "2026-11-01"},
                },
                {"value": "info@acme.com", "verification": {"status": "accept_all"}},
                {"value": None},
            ]
        }
    }
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["key"] = request.headers.get("X-API-KEY")
        seen["url"] = str(request.url)
        return httpx.Response(200, json=body)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        "signal_core.modules.m07_enrichment.httpx.AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    )
    found = await HunterProvider("secret-key").lookup("Acme", "acme.com")
    assert seen["key"] == "secret-key" and "secret-key" not in seen["url"]
    assert [(c.name, c.title, c.status) for c in found] == [
        ("Jane Doe", "CTO", "valid"),
        ("info", None, "risky"),
    ]
    assert found[0].verified_at == datetime(2026, 11, 1, tzinfo=UTC)
    assert HunterProvider.cost_usd > Decimal(0)


async def test_enrichment_does_nothing_without_a_provider() -> None:
    pool = MagicMock()
    assert await enrich_account(pool, MagicMock(), uuid4(), uuid4(), _ctx(), providers=[]) == 0
    pool.connection.assert_not_called()


# ── prompts ───────────────────────────────────────────────────────────────────


def test_new_prompts_render_with_the_variables_their_callers_pass() -> None:
    assert "Acme" in render(
        load_prompt("next_step", 2),
        {"account_name": "Acme", "stage": "Engaged", "recent_activity": "none"},
    )
    assert "hello" in render(load_prompt("reply_classify", 1), {"reply_text": "hello"})


def test_conflicting_claims_need_two_and_catch_opposite_answers() -> None:
    from uuid import UUID

    from signal_core.modules.m05_verdict import conflicting_claims

    a, b, c = (UUID(int=i) for i in (1, 2, 3))
    claims = [
        (a, "uses_revit", True, "q", 90),
        (b, "uses_revit", False, "q", 80),
        (c, "sites", 12, None, 70),
    ]
    assert conflicting_claims(claims, []) == [a, b]  # a yes/no predicate holding both answers
    assert conflicting_claims(claims, [0, 2]) == [a, b, c]  # plus what the judge flagged
    assert conflicting_claims(claims[2:], [0]) == []  # one claim cannot conflict
    assert conflicting_claims(claims[:1] + claims[2:], [7, -1]) == []  # bad indices are ignored
