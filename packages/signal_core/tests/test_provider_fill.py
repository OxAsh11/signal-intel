"""The work-email waterfall and PDL company fills against fakes, with no vendor keys or calls."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from signal_core.fill import provider_fill
from signal_core.fill.base import (
    ALIAS_INSERT_SQL,
    CACHE_GET_SQL,
    CACHE_SET_SQL,
    CLAIM_SQL,
    EXTRACTOR_GET_SQL,
    EXTRACTOR_INSERT_SQL,
    LINKEDIN_SQL,
    PRICE_SQL,
    RECENT_CONTACT_SQL,
    RECORD_ATTEMPT_SQL,
    SOURCE_SQL,
    STORE_CONTACT_SQL,
    FillResult,
)
from signal_core.metering import BudgetPaused, CallContext
from signal_core.providers.base import (
    NOT_SET_UP,
    ProviderInput,
    ProviderResult,
    error_result,
    found,
)

RUN_ID = uuid4()
ORG = uuid4()
WS = uuid4()
ENTITY = uuid4()
PREDICATE = uuid4()
SOURCE = uuid4()
CLAIM = uuid4()
VERSION = uuid4()

_PRICES = {
    "leadmagic": Decimal("0.012"),
    "findymail": Decimal("0.015"),
    "findymail_verify": Decimal("0.004"),
    "prospeo": Decimal("0.010"),
    "pdl_company": Decimal("0.10"),
}


def _run(**over: Any) -> dict[str, Any]:
    run: dict[str, Any] = {
        "org_id": str(ORG),
        "workspace_id": str(WS),
        "entity_id": str(ENTITY),
        "predicate_id": str(PREDICATE),
        "brief_id": None,
        "key": "work_email",
        "fill_method": "provider",
        "fill_config": {},
        "value_type": "text",
        "allowed_values": None,
        "run_condition": None,
        "inputs": [],
        "name": "Jane Roe",
        "domain": "acme.example",
    }
    run.update(over)
    return run


class FakeCursor:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    async def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None

    async def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows


class FakePool:
    """Answers statements from *answers*: a row list or a callable of the SQL parameters."""

    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[FakePool]:
        yield self

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> FakeCursor:
        self.calls.append((sql, params))
        answer = self.answers.get(sql, [])
        if callable(answer):
            answer = answer(params)
        if isinstance(answer, BaseException):
            raise answer
        return FakeCursor(answer)

    def sent(self, sql: str) -> list[tuple[Any, ...]]:
        return [p for s, p in self.calls if s == sql]


def _pool(*, recent: Any = (), cached: Any = None) -> FakePool:
    store: dict[tuple[str, str, str], Any] = {("pdl_company", str(WS), str(ENTITY)): cached}

    def price(params: tuple[Any, ...]) -> list[tuple[Any, ...]]:
        # model_price stores USD per million; the fill helper converts to USD per call.
        return [(_PRICES.get(str(params[0]), Decimal(0)) * 1_000_000,)]

    def get_cache(params: tuple[Any, ...]) -> Any:
        return [(store.get((str(params[1]), str(params[0]), str(params[2]))),)]

    def set_cache(params: tuple[Any, ...]) -> Any:
        store[(str(params[1]), str(params[0]), str(params[2]))] = json.loads(str(params[3]))
        return []

    answers: dict[str, Any] = {
        PRICE_SQL: price,
        RECENT_CONTACT_SQL: recent,
        LINKEDIN_SQL: [],
        RECORD_ATTEMPT_SQL: [(uuid4(),)],
        SOURCE_SQL: [(SOURCE,)],
        STORE_CONTACT_SQL: [(uuid4(),)],
        CLAIM_SQL: [(CLAIM,)],
        EXTRACTOR_INSERT_SQL: [],
        EXTRACTOR_GET_SQL: [(VERSION,)],
        CACHE_GET_SQL: get_cache,
        CACHE_SET_SQL: set_cache,
        ALIAS_INSERT_SQL: [],
    }
    return FakePool(answers)


class FakeBudget:
    def __init__(self, pause_at: int | None = None) -> None:
        self.pause_at = pause_at
        self.reserves: list[tuple[CallContext, Decimal]] = []
        self.settles: list[dict[str, Any]] = []
        self.releases: list[UUID] = []
        self._n = 0

    async def reserve(self, ctx: CallContext, estimate: Decimal) -> UUID:
        self._n += 1
        self.reserves.append((ctx, estimate))
        if self._n == self.pause_at:
            raise BudgetPaused(uuid4(), "month", Decimal(10), Decimal(10))
        return uuid4()

    async def settle(
        self,
        reservation_id: UUID,
        ctx: CallContext,
        capability: str,
        provider: str,
        model: str,
        lines: list[Any],
        latency_ms: int,
        status: str,
    ) -> None:
        self.settles.append({"ctx": ctx, "provider": provider, "lines": lines, "status": status})

    async def release(self, reservation_id: UUID) -> None:
        self.releases.append(reservation_id)


class FakeProvider:
    def __init__(self, key: str, kind: str, *steps: Any) -> None:
        self.key = key
        self.kind = kind
        self.steps = list(steps)
        self.calls: list[ProviderInput] = []

    async def lookup(self, data: ProviderInput) -> ProviderResult:
        self.calls.append(data)
        step = self.steps.pop(0) if self.steps else None
        return step(data) if callable(step) else step


def _hit(key: str, email: str, confidence: int = 90) -> ProviderResult:
    return found(
        key, email, confidence=confidence, units=1, unit_price=_PRICES.get(key, Decimal(0))
    )


def attempts_of(result: FillResult) -> list[dict[str, Any]]:
    return list((result.inputs or {}).get("attempts", []))


# ── Review Focus 1: validation and the waterfall ───────────────────────────────


async def test_waterfall_stops_at_the_first_validated_hit() -> None:
    leadmagic = FakeProvider("leadmagic", "email_finder", _hit("leadmagic", "jane@acme.example"))
    prospeo = FakeProvider("prospeo", "email_finder", _hit("prospeo", "other@acme.example"))
    verifier = FakeProvider(
        "findymail",
        "email_verifier",
        found(
            "findymail", "valid", confidence=100, units=1, unit_price=_PRICES["findymail_verify"]
        ),
    )
    pool, budget = _pool(), FakeBudget()
    result = await provider_fill.fill_work_email(
        pool, budget, _run(), RUN_ID, providers=[leadmagic, prospeo], verifier=verifier
    )

    assert result.status == "succeeded"
    assert result.claim_id == CLAIM
    assert [a["provider"] for a in attempts_of(result)] == ["leadmagic", "findymail"]
    assert prospeo.calls == []
    assert verifier.calls[0].email == "jane@acme.example"
    # The full address is encrypted before it reaches SQL; only ciphertext and a mask are stored.
    contact = pool.sent(STORE_CONTACT_SQL)[0]
    assert "jane@acme.example" not in str(contact)
    assert "j•••@acme.example" in str(contact)
    assert "enc:v1:" in str(contact)
    # The receipt never carries a key, and the ledger line is not attributed to the run twice.
    assert "api_key" not in json.dumps(result.inputs)
    assert all(s["ctx"].predicate_run_id is None for s in budget.settles)


async def test_a_failed_verify_moves_on_to_the_next_finder() -> None:
    first = FakeProvider("leadmagic", "email_finder", _hit("leadmagic", "wrong@acme.example", 95))
    second = FakeProvider("findymail", "email_finder", _hit("findymail", "right@acme.example", 90))
    verifier = FakeProvider(
        "findymail",
        "email_verifier",
        found(
            "findymail", "invalid", confidence=100, units=1, unit_price=_PRICES["findymail_verify"]
        ),
        found(
            "findymail", "valid", confidence=100, units=1, unit_price=_PRICES["findymail_verify"]
        ),
    )
    pool, budget = _pool(), FakeBudget()
    result = await provider_fill.fill_work_email(
        pool, budget, _run(), RUN_ID, providers=[first, second], verifier=verifier
    )

    assert result.status == "succeeded"
    assert result.inputs["provider"] == "findymail"
    assert [a["provider"] for a in attempts_of(result)] == [
        "leadmagic",
        "findymail",
        "findymail",
        "findymail",
    ]
    assert [str(v.email) for v in verifier.calls] == ["wrong@acme.example", "right@acme.example"]
    # The rejected address never became a contact.
    assert "wrong@acme.example" not in str(pool.sent(STORE_CONTACT_SQL))


async def test_a_finder_without_a_verifier_needs_confidence_80() -> None:
    weak = FakeProvider("leadmagic", "email_finder", _hit("leadmagic", "weak@acme.example", 70))
    strong = FakeProvider("prospeo", "email_finder", _hit("prospeo", "good@acme.example", 85))
    result = await provider_fill.fill_work_email(
        _pool(), FakeBudget(), _run(), RUN_ID, providers=[weak, strong]
    )
    assert result.status == "succeeded"
    assert result.inputs["provider"] == "prospeo"
    assert [a["provider"] for a in attempts_of(result)] == ["leadmagic", "prospeo"]


# ── Review Focus 4: budget ─────────────────────────────────────────────────────


async def test_a_budget_pause_mid_waterfall_stops_with_earlier_attempts_billed() -> None:
    first = FakeProvider("leadmagic", "email_finder", _hit("leadmagic", "one@acme.example", 70))
    second = FakeProvider("findymail", "email_finder", _hit("findymail", "two@acme.example", 90))
    pool, budget = _pool(), FakeBudget(pause_at=2)
    result = await provider_fill.fill_work_email(
        pool, budget, _run(), RUN_ID, providers=[first, second], verifier=None
    )

    assert result.status == "error"
    assert result.error == "budget"
    assert [a["provider"] for a in attempts_of(result)] == ["leadmagic"]
    assert len(pool.sent(RECORD_ATTEMPT_SQL)) == 1
    assert second.calls == []
    assert budget.releases == []


# ── Review Focus 3: missing or rejected keys ───────────────────────────────────


async def test_a_missing_key_is_an_error_with_no_charge() -> None:
    pool, budget = _pool(), FakeBudget()
    result = await provider_fill.fill_work_email(pool, budget, _run(), RUN_ID, providers=[])
    assert result.status == "error"
    assert result.error == NOT_SET_UP
    assert budget.reserves == []
    assert pool.sent(RECORD_ATTEMPT_SQL) == []


async def test_a_rejected_key_is_an_error_with_no_charge() -> None:
    bad = FakeProvider(
        "leadmagic",
        "email_finder",
        error_result("leadmagic", "rejected the key", "leadmagic:http:401"),
    )
    pool, budget = _pool(), FakeBudget()
    result = await provider_fill.fill_work_email(
        pool, budget, _run(), RUN_ID, providers=[bad], verifier=None
    )
    assert result.status == "error"
    assert "rejected the key" in str(result.error)
    assert attempts_of(result)[0]["status"] == "error"
    params = pool.sent(RECORD_ATTEMPT_SQL)[0]
    assert params[7] == "leadmagic:http:401"
    assert params[9] == "vendor"
    assert budget.settles[0]["status"] == "rejected"


async def test_a_row_without_a_name_or_domain_spends_nothing() -> None:
    from signal_core.providers.base import input_error

    provider = FakeProvider("leadmagic", "email_finder", input_error("leadmagic", "needs a domain"))
    pool = _pool()
    result = await provider_fill.fill_work_email(
        pool,
        FakeBudget(),
        _run(domain=None, name=""),
        RUN_ID,
        providers=[provider],
        verifier=None,
    )
    assert result.status == "error"
    assert pool.sent(RECORD_ATTEMPT_SQL)[0][9] == "input"
    assert provider.calls[0].domain is None


# ── Never pay twice ────────────────────────────────────────────────────────────


async def test_a_recent_verified_email_is_skipped_without_a_call() -> None:
    pool, budget = (
        _pool(recent=[("enc:v1:old", datetime.now(UTC) - timedelta(days=2))]),
        FakeBudget(),
    )
    provider = FakeProvider("leadmagic", "email_finder", _hit("leadmagic", "x@acme.example"))
    result = await provider_fill.fill_work_email(
        pool, budget, _run(), RUN_ID, providers=[provider], verifier=None
    )
    assert result.status == "condition_not_met"
    assert "Verified on" in result.not_run_reason["skipped"]
    assert provider.calls == []
    assert budget.reserves == []
    assert pool.sent(RECORD_ATTEMPT_SQL) == []


# ── One PDL call per account ───────────────────────────────────────────────────


def _company_run(field: str, **over: Any) -> dict[str, Any]:
    run = _run(
        fill_config={"provider": "pdl_company", "field": field},
        value_type="number" if field == "employee_count" else "text",
    )
    run.update(over)
    return run


async def test_two_company_columns_make_one_call_and_a_free_cache_hit() -> None:
    payload = {
        "name": "Acme",
        "employee_count": 42,
        "industry": "robotics",
        "location": {"locality": "Delhi", "region": "DL", "country": "India"},
    }
    provider = FakeProvider(
        "pdl_company",
        "company",
        found(
            "pdl_company",
            payload,
            confidence=80,
            units=1,
            unit_price=_PRICES["pdl_company"],
            raw_ref="pdl:company:1",
        ),
    )
    pool, budget = _pool(), FakeBudget()
    first = await provider_fill.fill_company_field(
        pool, budget, _company_run("employee_count"), RUN_ID, provider=provider
    )
    second_run = _run(
        fill_config={"provider": "pdl_company", "field": "industry"},
        predicate_id=uuid4(),
    )
    second = await provider_fill.fill_company_field(
        pool, budget, second_run, RUN_ID, provider=provider
    )

    assert first.status == second.status == "succeeded"
    assert len(provider.calls) == 1
    assert len(pool.sent(CACHE_SET_SQL)) == 1
    assert len(budget.reserves) == 1
    assert attempts_of(first)[0]["cost_usd"] == "0.10"
    assert attempts_of(second)[0]["cost_usd"] == "0"
    assert attempts_of(second)[0]["cache"] is True
    assert pool.sent(CLAIM_SQL)[0][3] == "42"
    assert json.loads(pool.sent(CLAIM_SQL)[1][3]) == "robotics"
    aliases = [p[2] for p in pool.sent(ALIAS_INSERT_SQL)]
    assert aliases == ["name_country", "site"]


async def test_a_company_result_of_the_wrong_type_uses_the_p3_message() -> None:
    provider = FakeProvider(
        "pdl_company",
        "company",
        found(
            "pdl_company",
            {"employee_count": 42},
            confidence=80,
            units=1,
            unit_price=_PRICES["pdl_company"],
        ),
    )
    result = await provider_fill.fill_company_field(
        _pool(),
        FakeBudget(),
        _company_run("employee_count", value_type="boolean"),
        RUN_ID,
        provider=provider,
    )
    assert result.status == "error"
    assert result.error == "formula returned number, column is boolean"


async def test_a_company_column_needs_a_field() -> None:
    result = await provider_fill.fill_company_field(
        _pool(), FakeBudget(), _run(fill_config={"provider": "pdl_company"}), RUN_ID
    )
    assert result.status == "error"
    assert "needs a field" in str(result.error)
