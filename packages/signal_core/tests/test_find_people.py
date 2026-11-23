"""Find people against fakes: no PDL key, no HTTP call, no encryption key in CI."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

from signal_core.contracts.enums import UsageUnit
from signal_core.fill.base import PRICE_SQL
from signal_core.metering import CallContext
from signal_core.people import find_people as people
from signal_core.providers.base import ProviderInput, ProviderResult, found

ORG = uuid4()
WS = uuid4()
ACCOUNT = uuid4()
ENTITY = uuid4()
BRIEF = uuid4()
SOURCE = uuid4()
EXISTING = uuid4()
UNLINKED = uuid4()
PERSON_PRICE = Decimal("0.28")

PERSONAS = {
    "buyer": ["VP Engineering"],
    "user": ["Engineer"],
    "influencer": ["CTO"],
}

PEOPLE = [
    {
        "full_name": "Jane Roe",
        "job_title": "VP Engineering",
        "work_email": "jane@acme.example",
        "linkedin_url": "linkedin.com/in/jane",
        "job_company_name": "Acme",
        "job_company_website": "acme.example",
        "location_locality": "Delhi",
        "location_country": "India",
    },
    {
        "full_name": "Mike Mumbai",
        "job_title": "Senior Engineer",
        "work_email": "mike@acme.example",
        "linkedin_url": "linkedin.com/in/mike",
        "job_company_name": "Acme",
        "job_company_website": "acme.example",
        "location_locality": "Mumbai",
        "location_country": "India",
    },
    {
        "full_name": "Ada Owner",
        "job_title": "CTO",
        "work_email": "ada@acme.example",
        "linkedin_url": "linkedin.com/in/ada",
        "job_company_name": "Acme",
        "job_company_website": "acme.example",
        "location_locality": "Delhi",
        "location_country": "India",
    },
]


class FakeCursor:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    async def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None

    async def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows


class FakePool:
    """Answers each SQL statement from a table of rows or SQL-parameter callables."""

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
        return FakeCursor(answer)

    def sent(self, sql: str) -> list[tuple[Any, ...]]:
        return [params for statement, params in self.calls if statement == sql]


class FakeBudget:
    def __init__(self) -> None:
        self.reserves: list[tuple[CallContext, Decimal]] = []
        self.settles: list[dict[str, Any]] = []
        self.releases: list[UUID] = []

    async def reserve(self, ctx: CallContext, estimate: Decimal) -> UUID:
        self.reserves.append((ctx, estimate))
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
        self.settles.append(
            {"provider": provider, "lines": lines, "status": status, "capability": capability}
        )

    async def release(self, reservation_id: UUID) -> None:
        self.releases.append(reservation_id)


class FakeProvider:
    key = "pdl_person"

    def __init__(self, *records: dict[str, Any]) -> None:
        self.records = records
        self.inputs: list[ProviderInput] = []

    async def lookup(self, data: ProviderInput) -> ProviderResult:
        self.inputs.append(data)
        return found(
            self.key,
            list(self.records),
            units=len(self.records),
            unit_price=PERSON_PRICE,
            confidence=80,
        )


def _pool(*, unlinked: bool = False) -> FakePool:
    def upsert(params: tuple[Any, ...]) -> list[tuple[Any, ...]]:
        # Jane was already found by another flow: SQL attaches her, it does not make a second person.
        profile = params[6]
        return [(EXISTING if profile == "linkedin.com/in/jane" else uuid4(),)]

    def match_domain(params: tuple[Any, ...]) -> list[tuple[Any, ...]]:
        return [(ACCOUNT,)] if params[1] == "acme.example" else []

    def match_name(params: tuple[Any, ...]) -> list[tuple[Any, ...]]:
        return [(ACCOUNT,)] if params[1] == "acme" else []

    answers: dict[str, Any] = {
        people.ACCOUNT_SQL: [
            (ACCOUNT, ORG, WS, ENTITY, "Acme", "acme.example", "India", ["Delhi, India"])
        ],
        people.PERSONAS_SQL: [(PERSONAS,)],
        PRICE_SQL: [(PERSON_PRICE * 1_000_000,)],
        people.SOURCE_SQL: [(SOURCE,)],
        people.MATCH_DOMAIN_SQL: match_domain,
        people.MATCH_NAME_SQL: match_name,
        people.UPSERT_SQL: upsert,
        people.FLAG_SQL: [()],
        people.QUEUE_UNLINKED_SQL: [(UNLINKED,)],
        people.PROFILE_ALIAS_SQL: [],
        people.EMAIL_ALIAS_SQL: [],
        people.CREATE_ENTITY_SQL: [(UNLINKED,)],
        people.ALIAS_PROFILE_SQL: [],
        people.ALIAS_EMAIL_SQL: [],
        people.CONTACT_INSERT_SQL: [],
    }
    if unlinked:
        answers[people.QUEUE_UNLINKED_SQL] = [(UNLINKED,)]
    return FakePool(answers)


def _ctx() -> CallContext:
    return CallContext(
        org_id=ORG,
        workspace_id=WS,
        brief_id=BRIEF,
        job_id=None,
        job_type="people.find",
        job_args={"account_id": str(ACCOUNT), "limit": 3},
    )


@pytest.fixture(autouse=True)
def fake_crypto(monkeypatch: pytest.MonkeyPatch) -> None:
    """The worker encrypts, but this unit test only needs to see what would be stored."""
    monkeypatch.setattr(people, "encrypt_secret", lambda value: f"encrypted({value})")
    monkeypatch.setattr(people, "lookup_hash", lambda value: f"digest({value})")


async def test_three_people_are_linked_deduplicated_and_the_other_office_is_flagged() -> None:
    pool = _pool()
    budget = FakeBudget()
    provider = FakeProvider(*PEOPLE)

    result = await people.find_people(
        pool, _ctx(), ACCOUNT, 3, provider=provider, budget=budget, brief_id=BRIEF
    )

    assert result["found"] == 3
    assert len(result["linked"]) == 3
    assert str(EXISTING) in result["linked"]
    assert result["unlinked"] == []
    assert len(result["flagged"]) == 1
    flag = pool.sent(people.FLAG_SQL)[0]
    assert flag[2] == "Possibly a different office (Mumbai vs India)"

    assert provider.inputs[0].domain == "acme.example"
    assert provider.inputs[0].titles == ("VP Engineering", "Engineer", "CTO")
    assert provider.inputs[0].limit == 3
    assert pool.sent(people.PERSONAS_SQL) == [(BRIEF,)]

    # The full estimate is reserved; the vendor's actual returned count settles it.
    assert budget.reserves[0][1] == Decimal("0.84")
    assert budget.settles[0]["provider"] == "pdl_person"
    assert budget.settles[0]["lines"][0].unit == UsageUnit.ENRICHMENT_LOOKUP
    assert budget.settles[0]["lines"][0].quantity == 3
    assert budget.settles[0]["lines"][0].cost_usd == Decimal("0.84")

    # Person values stay out of the result; the table gets a keyed digest and a masked hint.
    assert not any(
        email in json.dumps(result) for person in PEOPLE for email in [person["work_email"]]
    )
    upserts = pool.sent(people.UPSERT_SQL)
    assert len(upserts) == 3
    assert upserts[0][2] == ACCOUNT
    assert upserts[0][5] == "buyer"
    assert upserts[0][7] == "encrypted(jane@acme.example)"
    assert upserts[0][8] == "digest(jane@acme.example)"
    assert upserts[0][9] == "j•••@acme.example"
    assert pool.sent(people.CREATE_ENTITY_SQL) == []


async def test_a_person_whose_company_is_not_an_account_goes_to_review() -> None:
    pool = _pool()
    provider = FakeProvider(
        {
            "full_name": "Sam Elsewhere",
            "job_title": "VP Engineering",
            "work_email": "sam@other.example",
            "linkedin_url": "linkedin.com/in/sam",
            "job_company_name": "Other Co",
            "job_company_website": "other.example",
        }
    )

    result = await people.find_people(
        pool, _ctx(), ACCOUNT, 1, provider=provider, budget=FakeBudget(), brief_id=BRIEF
    )

    assert result["found"] == 1
    assert result["linked"] == []
    assert result["unlinked"] == [str(UNLINKED)]
    assert pool.sent(people.QUEUE_UNLINKED_SQL) == [(UNLINKED, WS)]
    assert pool.sent(people.UPSERT_SQL) == []
    assert pool.sent(people.FLAG_SQL) == []


def test_persona_titles_and_masking_are_pure_helpers() -> None:
    assert people.persona_titles(PERSONAS) == ("VP Engineering", "Engineer", "CTO")
    assert people.persona_for("Senior Engineer", PERSONAS) == "user"
    assert people.persona_for(None, PERSONAS) is None
    assert people.mask_email("jane@acme.example") == "j•••@acme.example"
