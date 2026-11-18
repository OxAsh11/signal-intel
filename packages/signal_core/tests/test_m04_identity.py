"""Tests for M4 identity resolution: alias matching, review queue, duplicate sweep."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from signal_core.ai_gateway.gateway import AiGateway, MemoryExtractorVersions
from signal_core.ai_gateway.providers.fake import FakeProvider
from signal_core.config import Settings
from signal_core.contracts.enums import UsageUnit
from signal_core.metering import CallContext, MemoryBudgetGuard, MemoryPriceBook
from signal_core.modules.m04_identity import (
    _lookup_aliases,
    name_alias_value,
    normalise_company_name,
    resolve_mention,
)

# ---------------------------------------------------------------------------
# Shared IDs
# ---------------------------------------------------------------------------

ORG_ID = uuid4()
WORKSPACE_ID = uuid4()
MENTION_ID = uuid4()
ENTITY_ID = uuid4()
SOURCE_RECORD_ID = uuid4()


# ---------------------------------------------------------------------------
# Fake pool infrastructure
# ---------------------------------------------------------------------------


def _emulate_name_query(sql: str, args: Any, rows: list[tuple]) -> list[tuple]:
    """Stand-in for the exact-name SELECT: applies only the filters the SQL actually carries.

    rows: (entity_id, country) or (entity_id, country, entity_type, status); the defaults are
    an active company. Rows come back in table order, cut at LIMIT 5 when the SQL says so.
    """
    full = [(*r, "company", "active")[:4] for r in rows]
    # Positional args of the current query: (org, name, entity_type, country, country).
    if "e.entity_type = %s" in sql:
        full = [r for r in full if r[2] == args[2]]
    if "e.status <> 'merged'" in sql:
        full = [r for r in full if r[3] != "merged"]
    if "in ('', %s::text)" in sql and args[3]:
        full = [r for r in full if r[1] in ("", args[3])]
    if "limit 5" in sql:
        full = full[:5]
    return [(r[0], r[1]) for r in full]


class FakeCursor:
    """Cursor-like object that routes queries through its parent FakeConn."""

    def __init__(self, conn: FakeConn) -> None:
        self._conn = conn
        self._rows: list[tuple[Any, ...]] = []

    async def execute(self, sql: str, args: Any = None) -> FakeCursor:
        self._rows = self._conn._route(sql, args)
        return self

    async def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None

    async def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows

    async def __aenter__(self) -> FakeCursor:
        return self

    async def __aexit__(self, *_: Any) -> None:
        pass


class FakeConn:
    """Connection-like object that routes SQL to canned responses."""

    def __init__(
        self,
        alias_rows: list[tuple],
        *,
        entity_id: UUID | None = None,
        exact_rows: list[tuple] | None = None,
        name_rows: list[tuple] | None = None,
        mention_row: tuple | None = None,
    ) -> None:
        # name_rows: (entity_id, country_part) rows for the exact-name query
        self._name_rows = name_rows or []
        self._mention_row = mention_row
        # alias_rows: returned for fuzzy (similarity) queries
        # exact_rows: returned for exact alias lookups (default = alias_rows for
        #             tests that don't distinguish; pass [] to force fuzzy path)
        self._alias_rows = alias_rows
        self._exact_rows = exact_rows if exact_rows is not None else alias_rows
        self._entity_id = entity_id or uuid4()
        self.calls: list[tuple[str, Any]] = []

    def _route(self, sql: str, args: Any) -> list[tuple[Any, ...]]:
        self.calls.append((sql, args))
        sql_lower = sql.lower()
        if "public.mention" in sql_lower:
            if self._mention_row is not None:
                return [self._mention_row]
            return [
                (
                    str(MENTION_ID),
                    str(ORG_ID),
                    str(SOURCE_RECORD_ID),
                    "company",
                    "Acme Corp",
                    "acme.com",
                    "US",
                    {},
                    None,  # entity_id (not yet resolved)
                    None,  # match_method
                )
            ]
        if "public.entity_alias" in sql_lower or "public.entity e" in sql_lower:
            if "group by" in sql_lower:
                # Enrichment query: SELECT e.id, e.display_name, array_agg(aliases)
                # Build 3-column tuples from candidate entity IDs.
                return [(r[0], "Acme Corp", []) for r in self._alias_rows]
            if "split_part" in sql_lower:
                return _emulate_name_query(sql_lower, args, self._name_rows)
            if "similarity" in sql_lower:
                # Fuzzy name_country query → return fuzzy alias rows.
                return self._alias_rows
            # Exact alias query → return exact alias rows (or empty).
            return self._exact_rows
        if "app.create_entity(" in sql_lower and "alias" not in sql_lower:
            return [(str(self._entity_id),)]
        # Everything else (resolve_mention, create_account, create_review_item,
        # create_entity_alias) returns nothing meaningful.
        return []

    async def execute(self, sql: str, args: Any = None) -> FakeCursor:
        rows = self._route(sql, args)
        cur = FakeCursor(self)
        cur._rows = rows
        return cur

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    async def __aenter__(self) -> FakeConn:
        return self

    async def __aexit__(self, *_: Any) -> None:
        pass


class FakeConnCtx:
    def __init__(self, conn: FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> FakeConn:
        return self._conn

    async def __aexit__(self, *_: Any) -> None:
        pass


class FakePool:
    """Pool-like object backed by a single FakeConn."""

    def __init__(
        self,
        alias_rows: list[tuple],
        *,
        entity_id: UUID | None = None,
        exact_rows: list[tuple] | None = None,
        name_rows: list[tuple] | None = None,
        mention_row: tuple | None = None,
    ) -> None:
        self.conn = FakeConn(
            alias_rows,
            entity_id=entity_id,
            exact_rows=exact_rows,
            name_rows=name_rows,
            mention_row=mention_row,
        )

    def connection(self) -> FakeConnCtx:
        return FakeConnCtx(self.conn)


# ---------------------------------------------------------------------------
# Gateway factory
# ---------------------------------------------------------------------------

_HAIKU = "claude-haiku-5-5"
_SONNET = "claude-sonnet-5-5"


def _prices() -> MemoryPriceBook:
    return MemoryPriceBook(
        {
            (_HAIKU, UsageUnit.AI_INPUT_TOKENS): Decimal("0.10"),
            (_HAIKU, UsageUnit.AI_OUTPUT_TOKENS): Decimal("0.50"),
            (_SONNET, UsageUnit.AI_INPUT_TOKENS): Decimal("2.00"),
            (_SONNET, UsageUnit.AI_OUTPUT_TOKENS): Decimal("10.00"),
        }
    )


def _make_gateway(response: str) -> AiGateway:
    return AiGateway(
        settings=Settings(_env_file=None),  # type: ignore[call-arg]
        chat=FakeProvider({"*": response}),
        embedder=None,
        prices=_prices(),
        budget=MemoryBudgetGuard(),
        versions=MemoryExtractorVersions(),
    )


def _ctx() -> CallContext:
    return CallContext(ORG_ID, WORKSPACE_ID, None, None, "test.job", {})


# ---------------------------------------------------------------------------
# Tests: exact match (confidence 95 → 'attached')
# ---------------------------------------------------------------------------


async def test_resolve_mention_exact_match_attaches() -> None:
    """An exact alias match (confidence 95) resolves the mention and returns 'attached'."""
    alias_rows = [(str(ENTITY_ID),)]  # exact domain match
    pool = FakePool(alias_rows)
    gw = _make_gateway("{}")  # AI should not be called

    result = await resolve_mention(pool, gw, MENTION_ID, WORKSPACE_ID, _ctx())

    assert result == "attached"
    # resolve_mention RPC should have been called
    resolve_calls = [args for sql, args in pool.conn.calls if "app.resolve_mention" in sql]
    assert len(resolve_calls) == 1
    # create_account RPC should have been called
    account_calls = [args for sql, args in pool.conn.calls if "app.create_account" in sql]
    assert len(account_calls) == 1


# ---------------------------------------------------------------------------
# Tests: no match (confidence 0 → 'created')
# ---------------------------------------------------------------------------


async def test_resolve_mention_low_confidence_creates_entity() -> None:
    """No alias match → new entity created; returns 'created'."""
    pool = FakePool(alias_rows=[], exact_rows=[])  # empty alias table — no match
    gw = _make_gateway("{}")  # AI should not be called

    result = await resolve_mention(pool, gw, MENTION_ID, WORKSPACE_ID, _ctx())

    assert result == "created"
    # create_entity RPC should have been called (not create_entity_alias)
    entity_calls = [
        args
        for sql, args in pool.conn.calls
        if "app.create_entity(" in sql.lower() and "alias" not in sql.lower()
    ]
    assert len(entity_calls) == 1
    # resolve_mention RPC should have been called (linking new entity to mention)
    resolve_calls = [args for sql, args in pool.conn.calls if "app.resolve_mention" in sql]
    assert len(resolve_calls) == 1


# ---------------------------------------------------------------------------
# Tests: fuzzy match (confidence 70-89 → 'queued')
# ---------------------------------------------------------------------------


async def test_resolve_mention_mid_confidence_queues() -> None:
    """A fuzzy name_country match (confidence 40–89) queues a review item."""
    # Simulate a fuzzy match with similarity 0.75 → confidence = 70 + round(0.75*20) = 85
    sim = 0.75
    fuzzy_rows = [(str(ENTITY_ID), sim)]
    # exact_rows=[] forces _lookup_aliases to skip exact path and fall into fuzzy
    pool = FakePool(alias_rows=fuzzy_rows, exact_rows=[])
    gw = _make_gateway("{}")  # AI should not be called (single candidate)

    result = await resolve_mention(pool, gw, MENTION_ID, WORKSPACE_ID, _ctx())

    assert result == "queued"
    review_calls = [args for sql, args in pool.conn.calls if "app.create_review_item" in sql]
    assert len(review_calls) == 1
    # resolve_mention and create_account should NOT have been called
    resolve_calls = [args for sql, args in pool.conn.calls if "app.resolve_mention" in sql]
    assert resolve_calls == []


# ---------------------------------------------------------------------------
# Tests: multiple candidates → AI tiebreak
# ---------------------------------------------------------------------------


async def test_resolve_mention_tiebreak_attaches_on_high_confidence() -> None:
    """AI tiebreak picks winner with confidence >= 90 → 'attached'."""
    entity_a = uuid4()
    entity_b = uuid4()
    # Two exact-match candidates — domain lookup returns two rows
    alias_rows = [(str(entity_a),), (str(entity_b),)]
    pool = FakePool(alias_rows)
    tiebreak_resp = json.dumps({"winner_index": 0, "confidence": 92, "reason": "better match"})
    gw = _make_gateway(tiebreak_resp)

    result = await resolve_mention(pool, gw, MENTION_ID, WORKSPACE_ID, _ctx())

    assert result == "attached"


async def test_resolve_mention_tiebreak_queues_on_mid_confidence() -> None:
    """AI tiebreak picks winner with confidence 40–89 → 'queued'."""
    entity_a = uuid4()
    entity_b = uuid4()
    alias_rows = [(str(entity_a),), (str(entity_b),)]
    pool = FakePool(alias_rows)
    tiebreak_resp = json.dumps({"winner_index": 1, "confidence": 65, "reason": "uncertain"})
    gw = _make_gateway(tiebreak_resp)

    result = await resolve_mention(pool, gw, MENTION_ID, WORKSPACE_ID, _ctx())

    assert result == "queued"


async def test_resolve_mention_tiebreak_no_winner_queues() -> None:
    """AI tiebreak returns winner_index=None → queued for human review (not created).

    When two exact-match candidates exist but the AI cannot choose, we queue rather
    than creating a potential duplicate entity.
    """
    entity_a = uuid4()
    entity_b = uuid4()
    alias_rows = [(str(entity_a),), (str(entity_b),)]
    pool = FakePool(alias_rows)
    tiebreak_resp = json.dumps({"winner_index": None, "confidence": 0, "reason": "no match"})
    gw = _make_gateway(tiebreak_resp)

    result = await resolve_mention(pool, gw, MENTION_ID, WORKSPACE_ID, _ctx())

    assert result == "queued"


# ---------------------------------------------------------------------------
# Tests: exact normalised-name match (repeat sightings)
# ---------------------------------------------------------------------------


def _mention(name: str, country: str | None, entity_type: str = "company") -> dict[str, Any]:
    return {
        "entity_type": entity_type,
        "raw_name": name,
        "raw_country": country,
        "raw_domain": None,
        "raw_attributes": {},
    }


def _mention_row(name: str, country: str | None, entity_type: str = "company") -> tuple:
    return (
        str(MENTION_ID),
        str(ORG_ID),
        str(SOURCE_RECORD_ID),
        entity_type,
        name,
        None,
        country,
        {},
        None,
        None,
    )


async def test_exact_name_same_country_attaches_95() -> None:
    conn = FakeConn([], exact_rows=[], name_rows=[(str(ENTITY_ID), "in")])
    found = await _lookup_aliases(conn, ORG_ID, _mention("Acme", "IN"))
    assert found == [(str(ENTITY_ID), "name_country", 95)]


async def test_exact_name_missing_country_attaches_95() -> None:
    # Alias has no country, mention has one.
    conn = FakeConn([], exact_rows=[], name_rows=[(str(ENTITY_ID), "")])
    assert await _lookup_aliases(conn, ORG_ID, _mention("Acme", "IN")) == [
        (str(ENTITY_ID), "name_country", 95)
    ]
    # Alias has a country, mention has none.
    conn = FakeConn([], exact_rows=[], name_rows=[(str(ENTITY_ID), "in")])
    assert await _lookup_aliases(conn, ORG_ID, _mention("Acme", None)) == [
        (str(ENTITY_ID), "name_country", 95)
    ]


async def test_exact_name_different_country_not_candidate() -> None:
    conn = FakeConn([], exact_rows=[], name_rows=[(str(ENTITY_ID), "no")])
    assert await _lookup_aliases(conn, ORG_ID, _mention("Fugro", "IN")) == []


async def test_exact_name_query_uses_normalised_name() -> None:
    """Old-style aliases (lower+strip) still match: the mention side is normalised."""
    conn = FakeConn([], exact_rows=[], name_rows=[(str(ENTITY_ID), "in")])
    await _lookup_aliases(conn, ORG_ID, _mention("  ACME Engineering ", "IN"))
    name_calls = [args for sql, args in conn.calls if "split_part" in sql.lower()]
    assert name_calls == [(str(ORG_ID), "acme engineering", "company", "in", "in")]
    assert name_alias_value("Acme Engineering", "IN") == "acme engineering|in"
    assert name_alias_value("Acme Engineering", None) == "acme engineering"


def test_normalise_company_name() -> None:
    assert normalise_company_name("  ACME  Engineering, ") == "acme engineering"
    assert normalise_company_name('"Acme"') == "acme"


async def test_resolve_mention_exact_name_returns_attached() -> None:
    pool = FakePool(
        [],
        exact_rows=[],
        name_rows=[(str(ENTITY_ID), "us")],
        mention_row=_mention_row("Acme Corp", "US"),
    )
    gw = _make_gateway("{}")  # AI must not be called

    result = await resolve_mention(pool, gw, MENTION_ID, WORKSPACE_ID, _ctx())

    assert result == "attached"
    resolve_calls = [args for sql, args in pool.conn.calls if "app.resolve_mention" in sql]
    assert len(resolve_calls) == 1
    assert resolve_calls[0][1] == str(ENTITY_ID)
    assert resolve_calls[0][3] == 95


# ---------------------------------------------------------------------------
# Tests: the exact-name rule is company-only, type-safe and country-filtered in SQL
# ---------------------------------------------------------------------------


async def test_person_same_name_never_reaches_95() -> None:
    """Two different John Smiths: a person mention keeps the capped fuzzy path (<= 89)."""
    conn = FakeConn(
        [(str(ENTITY_ID), 1.0)],  # a perfect trigram match on the person alias
        exact_rows=[],
        name_rows=[(str(ENTITY_ID), "gb", "person", "active")],
    )
    found = await _lookup_aliases(conn, ORG_ID, _mention("John Smith", "GB", "person"))
    assert found == [(str(ENTITY_ID), "name_country", 89)]
    assert not [sql for sql, _ in conn.calls if "split_part" in sql.lower()]


async def test_resolve_person_same_name_is_queued_not_attached() -> None:
    pool = FakePool(
        [(str(ENTITY_ID), 1.0)],
        exact_rows=[],
        name_rows=[(str(ENTITY_ID), "gb", "person", "active")],
        mention_row=_mention_row("John Smith", "GB", "person"),
    )
    result = await resolve_mention(pool, _make_gateway("{}"), MENTION_ID, WORKSPACE_ID, _ctx())
    assert result == "queued"
    assert not [a for sql, a in pool.conn.calls if "app.resolve_mention" in sql]
    assert not [a for sql, a in pool.conn.calls if "app.merge_entities" in sql]


async def test_company_mention_ignores_person_alias_of_same_name() -> None:
    conn = FakeConn([], exact_rows=[], name_rows=[(str(ENTITY_ID), "in", "person", "active")])
    assert await _lookup_aliases(conn, ORG_ID, _mention("Jordan", "IN")) == []


async def test_company_mention_ignores_merged_entity_alias() -> None:
    conn = FakeConn([], exact_rows=[], name_rows=[(str(ENTITY_ID), "in", "company", "merged")])
    assert await _lookup_aliases(conn, ORG_ID, _mention("Acme", "IN")) == []


async def test_compatible_alias_found_past_five_foreign_ones() -> None:
    """LIMIT 5 must not cut off the one compatible row behind five other-country rows."""
    compatible = uuid4()
    foreign = [(str(uuid4()), c) for c in ("no", "de", "fr", "us", "br", "jp")]
    conn = FakeConn([], exact_rows=[], name_rows=[*foreign, (str(compatible), "in")])
    found = await _lookup_aliases(conn, ORG_ID, _mention("Fugro", "IN"))
    assert found == [(str(compatible), "name_country", 95)]


def test_exact_name_sql_filters_type_status_country_and_orders() -> None:
    import inspect

    from signal_core.modules import m04_identity

    src = inspect.getsource(m04_identity._lookup_aliases)
    assert "JOIN public.entity e ON e.id = ea.entity_id" in src
    assert "e.entity_type = %s::public.entity_type" in src
    assert "e.status <> 'merged'" in src
    assert "(%s::text = '' OR split_part(ea.value_normalised, '|', 2) IN ('', %s::text))" in src
    assert "ORDER BY ea.created_at, ea.entity_id" in src


# ---------------------------------------------------------------------------
# Tests: punctuation-only names never produce an empty alias
# ---------------------------------------------------------------------------


def test_normalise_punctuation_only_names_is_empty() -> None:
    assert normalise_company_name("-") == ""
    assert normalise_company_name("“”") == ""
    assert normalise_company_name("«Acme»") == "acme"


async def test_create_new_skips_empty_name_alias() -> None:
    row = (
        str(MENTION_ID),
        str(ORG_ID),
        str(SOURCE_RECORD_ID),
        "company",
        "“”",
        "acme.com",
        "IN",
        {},
        None,
        None,
    )
    pool = FakePool([], exact_rows=[], mention_row=row)

    result = await resolve_mention(pool, _make_gateway("{}"), MENTION_ID, WORKSPACE_ID, _ctx())

    assert result == "created"
    alias_args = [a for sql, a in pool.conn.calls if "app.create_entity_alias" in sql]
    assert [a[2] for a in alias_args] == ["domain"]  # no empty name_country alias
