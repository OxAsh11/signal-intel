"""Provider adapters against recorded vendor responses (respx). No live vendor calls."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
import structlog
from pydantic import SecretStr

from signal_core import providers
from signal_core.config import Settings
from signal_core.providers import base
from signal_core.providers.base import (
    REJECTED_KEY,
    TIMEOUT_SECONDS,
    HttpProvider,
    Provider,
    ProviderInput,
    ProviderResult,
    reset_health,
)
from signal_core.providers.findymail import FindymailProvider, FindymailVerifier
from signal_core.providers.leadmagic import LeadMagicProvider
from signal_core.providers.pdl import PdlCompany, PdlPerson, person_query
from signal_core.providers.prospeo import ProspeoProvider

FIXTURES = Path(__file__).parent / "fixtures"
KEY = "sk-live-SECRET-0123456789"

PERSON = ProviderInput(first_name="Jane", last_name="Doe", domain="acme.com")
ACCOUNT = ProviderInput(domain="acme.com", titles=("VP Operations", "Head of Drone Operations"))


def fixture(vendor: str, name: str) -> httpx.Response:
    data: dict[str, Any] = json.loads((FIXTURES / vendor / f"{name}.json").read_text("utf-8"))
    return httpx.Response(data["status_code"], json=data["json"], headers=data.get("headers") or {})


@dataclass(frozen=True)
class Case:
    vendor: str  # fixture folder
    make: Callable[[], HttpProvider]
    method: str
    url: str
    input: ProviderInput
    found_value: Any
    found_cost: Decimal
    invalid_key: str = "401"  # fixture for a rejected key (Prospeo answers 400 INVALID_API_KEY)


CASES = [
    Case(
        "leadmagic",
        lambda: LeadMagicProvider(KEY, backoff_seconds=0),
        "POST",
        "https://api.leadmagic.io/v1/people/email-finder",
        PERSON,
        "jane.doe@acme.com",
        Decimal("0.012"),
    ),
    Case(
        "findymail",
        lambda: FindymailProvider(KEY, backoff_seconds=0),
        "POST",
        "https://app.findymail.com/api/search/name",
        PERSON,
        "jane.doe@acme.com",
        Decimal("0.015"),
    ),
    Case(
        "prospeo",
        lambda: ProspeoProvider(KEY, backoff_seconds=0),
        "POST",
        "https://api.prospeo.io/enrich-person",
        PERSON,
        "jane.doe@acme.com",
        Decimal("0.010"),
        invalid_key="invalid_key",
    ),
    Case(
        "pdl_company",
        lambda: PdlCompany(KEY, backoff_seconds=0),
        "GET",
        "https://api.peopledatalabs.com/v5/company/enrich",
        ACCOUNT,
        None,  # checked in its own test
        Decimal("0.10"),
    ),
    Case(
        "pdl_person",
        lambda: PdlPerson(KEY, backoff_seconds=0),
        "POST",
        "https://api.peopledatalabs.com/v5/person/search",
        ACCOUNT,
        None,
        Decimal("0.56"),  # two people returned x $0.28
    ),
]
IDS = [c.vendor for c in CASES]


@pytest.fixture(autouse=True)
def _clean_health() -> Iterator[None]:
    reset_health()
    yield
    reset_health()


@pytest.fixture(autouse=True)
def _capturable_logs(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """capture_logs only sees loggers that are not cached yet. The worker's configure_logging sets
    cache_logger_on_first_use=True, so in a full-suite run the providers' module logger may be
    bound already: reset structlog and hand the module a fresh logger for each test."""
    saved = structlog.get_config()
    structlog.reset_defaults()
    monkeypatch.setattr(base, "log", structlog.get_logger(base.__name__))
    yield
    structlog.configure(**saved)


def _events(logs: list[dict[str, Any]]) -> set[str]:
    return {str(r.get("event")) for r in logs}


def _route(mock: respx.MockRouter, case: Case) -> respx.Route:
    return mock.request(case.method, case.url)


def _assert_key_never_logged(logs: list[dict[str, Any]], result: ProviderResult) -> None:
    for record in logs:
        assert KEY not in repr(record)
    assert KEY not in result.model_dump_json()


async def _run_logged(
    case: Case, *responses: httpx.Response | Exception
) -> tuple[ProviderResult, int, list[dict[str, Any]]]:
    with respx.mock(assert_all_called=False) as mock:
        route = _route(mock, case)
        route.side_effect = list(responses)
        with structlog.testing.capture_logs() as logs:
            result = await case.make().lookup(case.input)
        _assert_key_never_logged(logs, result)
        return result, route.call_count, logs


async def _run(case: Case, *responses: httpx.Response | Exception) -> tuple[ProviderResult, int]:
    result, calls, _ = await _run_logged(case, *responses)
    return result, calls


@pytest.mark.parametrize("case", CASES, ids=IDS)
async def test_found(case: Case) -> None:
    result, calls = await _run(case, fixture(case.vendor, "found"))
    assert calls == 1
    assert result.status == "found" and result.provider == case.make().key
    assert result.cost_usd == case.found_cost
    assert 0 < result.confidence <= 100
    if case.found_value is not None:
        assert result.value == case.found_value


@pytest.mark.parametrize("case", CASES, ids=IDS)
async def test_not_found_is_free_and_never_raises(case: Case) -> None:
    result, _ = await _run(case, fixture(case.vendor, "not_found"))
    assert result.status == "not_found"
    assert result.cost_usd == 0 and result.error is None


@pytest.mark.parametrize("case", CASES, ids=IDS)
async def test_401_rejected_the_key(case: Case) -> None:
    result, calls, logs = await _run_logged(case, fixture(case.vendor, case.invalid_key))
    assert calls == 1  # never retried
    assert result.status == "error" and result.error == REJECTED_KEY
    assert result.error_kind == "vendor" and result.cost_usd == 0
    assert logs and "provider.http_error" in _events(logs)  # the no-key check saw real records
    row = next(r for r in providers.status(_settings()) if r["key"] == result.provider)
    assert row["last_error"] == REJECTED_KEY and row["last_error_at"] is not None


@pytest.mark.parametrize("case", CASES, ids=IDS)
async def test_429_then_success_retries_once(case: Case) -> None:
    result, calls = await _run(case, fixture(case.vendor, "429"), fixture(case.vendor, "found"))
    assert calls == 2
    assert result.status == "found"


@pytest.mark.parametrize("case", CASES, ids=IDS)
async def test_500_twice_is_an_error(case: Case) -> None:
    result, calls = await _run(case, fixture(case.vendor, "500"), fixture(case.vendor, "500"))
    assert calls == 2
    assert result.status == "error" and result.error is not None
    assert "500" in result.error or result.error == "vendor error"
    assert result.cost_usd == 0


@pytest.mark.parametrize("case", CASES, ids=IDS)
async def test_timeout_is_an_error(case: Case) -> None:
    result, _, logs = await _run_logged(case, httpx.ReadTimeout(f"read timed out {KEY}"))
    assert result.status == "error" and result.error == "timed out"
    assert result.cost_usd == 0
    assert result.raw_ref is not None and result.raw_ref.endswith(":timeout")
    assert logs and "provider.timeout" in _events(logs)


@pytest.mark.parametrize("case", CASES, ids=IDS)
async def test_every_call_has_the_15_second_timeout(case: Case) -> None:
    assert TIMEOUT_SECONDS == 15.0
    with respx.mock() as mock:
        route = _route(mock, case)
        route.side_effect = [fixture(case.vendor, "found")]
        await case.make().lookup(case.input)
    timeout = route.calls.last.request.extensions["timeout"]
    assert timeout == {"connect": 15.0, "read": 15.0, "write": 15.0, "pool": 15.0}


@pytest.mark.parametrize("case", CASES, ids=IDS)
async def test_key_goes_only_in_a_header(case: Case) -> None:
    with respx.mock() as mock:
        route = _route(mock, case)
        route.side_effect = [fixture(case.vendor, "found")]
        await case.make().lookup(case.input)
    request = route.calls.last.request
    assert KEY not in str(request.url)
    assert KEY not in request.content.decode()
    assert any(KEY in v for v in request.headers.values())
    assert KEY not in repr(case.make())


# ── vendor specifics ──────────────────────────────────────────────────────────


async def test_leadmagic_request_and_domain_mismatch() -> None:
    case = CASES[0]
    with respx.mock() as mock:
        route = _route(mock, case)
        route.side_effect = [fixture("leadmagic", "domain_mismatch")]
        result = await case.make().lookup(case.input)
    assert result.status == "not_found"
    sent = json.loads(route.calls.last.request.content)
    assert sent == {"first_name": "Jane", "last_name": "Doe", "domain": "acme.com"}
    assert route.calls.last.request.headers["X-API-Key"] == KEY


async def test_findymail_verify_valid_and_invalid() -> None:
    url = "https://app.findymail.com/api/verify"
    verifier = FindymailVerifier(FindymailProvider(KEY, backoff_seconds=0))
    for name, verdict, confidence in (
        ("verify_valid", "valid", 100),
        ("verify_invalid", "invalid", 0),
    ):
        with respx.mock() as mock:
            route = mock.post(url)
            route.side_effect = [fixture("findymail", name)]
            with structlog.testing.capture_logs() as logs:
                result = await verifier.lookup(ProviderInput(email="jane.doe@acme.com"))
        _assert_key_never_logged(logs, result)
        assert json.loads(route.calls.last.request.content) == {"email": "jane.doe@acme.com"}
        assert route.calls.last.request.headers["Authorization"] == f"Bearer {KEY}"
        assert (result.status, result.value, result.confidence) == ("found", verdict, confidence)
        assert result.cost_usd == Decimal("0.004")  # charged whatever the verdict


async def test_findymail_out_of_credits() -> None:
    with respx.mock() as mock:
        mock.post("https://app.findymail.com/api/search/name").side_effect = [
            fixture("findymail", "402")
        ]
        result = await FindymailProvider(KEY, backoff_seconds=0).lookup(PERSON)
    assert result.status == "error" and result.error == "out of credits"


async def test_prospeo_request_shape() -> None:
    with respx.mock() as mock:
        route = mock.post("https://api.prospeo.io/enrich-person")
        route.side_effect = [fixture("prospeo", "found")]
        result = await ProspeoProvider(KEY, unit_price=Decimal("0.02")).lookup(PERSON)
    assert result.cost_usd == Decimal("0.02")  # the price handed in from model_price wins
    assert result.confidence == 95
    assert json.loads(route.calls.last.request.content) == {
        "data": {"first_name": "Jane", "last_name": "Doe", "company_website": "acme.com"},
        "only_verified_email": True,
    }
    assert route.calls.last.request.headers["X-KEY"] == KEY


async def test_pdl_company_fields() -> None:
    with respx.mock() as mock:
        route = mock.get("https://api.peopledatalabs.com/v5/company/enrich")
        route.side_effect = [fixture("pdl_company", "found")]
        result = await PdlCompany(KEY).lookup(ACCOUNT)
    assert route.calls.last.request.url.params["website"] == "acme.com"
    assert route.calls.last.request.headers["X-Api-Key"] == KEY
    assert result.confidence == 80
    assert result.value["employee_count"] == 312
    assert result.value["industry"] == "Computer Software"
    assert result.value["location"] == {
        "name": "Bengaluru, Karnataka, India",
        "locality": "Bengaluru",
        "region": "Karnataka",
        "country": "India",
    }
    assert result.value["founded"] == 2012 and result.value["linkedin_url"]


async def test_pdl_person_people_and_query() -> None:
    with respx.mock() as mock:
        route = mock.post("https://api.peopledatalabs.com/v5/person/search")
        route.side_effect = [fixture("pdl_person", "found")]
        result = await PdlPerson(KEY).lookup(ACCOUNT)
    sent = json.loads(route.calls.last.request.content)
    assert sent["size"] == 5
    assert sent["query"] == person_query("acme.com", ACCOUNT.titles)
    people = result.value
    assert [p["full_name"] for p in people] == ["Jane Doe", "Ravi Kumar"]
    assert people[0]["work_email"] == "jane.doe@acme.com"
    assert people[1]["work_email"] is None  # PDL sends `true` without the email entitlement
    assert people[1]["location_locality"] == "Mumbai"
    assert result.billable_units == 2


async def test_pdl_person_404_is_not_found() -> None:
    with respx.mock() as mock:
        mock.post("https://api.peopledatalabs.com/v5/person/search").side_effect = [
            fixture("pdl_person", "not_found_404")
        ]
        result = await PdlPerson(KEY).lookup(ACCOUNT)
    assert result.status == "not_found" and result.cost_usd == 0


async def test_missing_inputs_are_errors_without_a_call() -> None:
    with respx.mock(assert_all_called=False) as mock:
        any_call = mock.route()
        empty = ProviderInput()
        for provider in (
            LeadMagicProvider(KEY),
            FindymailProvider(KEY),
            ProspeoProvider(KEY),
            PdlCompany(KEY),
            PdlPerson(KEY),
        ):
            result = await provider.lookup(empty)
            assert result.status == "error" and result.cost_usd == 0
            assert result.error_kind == "input"
        verdict = await FindymailVerifier(FindymailProvider(KEY)).lookup(empty)
        assert verdict.status == "error" and verdict.error_kind == "input"
        assert any_call.call_count == 0
    # A row without a domain says nothing about the provider: Settings must not show Failing.
    for row in providers.status(_settings()):
        assert row["last_error"] is None and row["last_error_at"] is None


async def test_input_error_does_not_clear_a_real_failure() -> None:
    with respx.mock() as mock:
        mock.post("https://api.leadmagic.io/v1/people/email-finder").side_effect = [
            fixture("leadmagic", "401")
        ]
        await LeadMagicProvider(KEY).lookup(PERSON)
    await LeadMagicProvider(KEY).lookup(ProviderInput())
    row = next(r for r in providers.status(_settings()) if r["key"] == "leadmagic")
    assert row["last_error"] == REJECTED_KEY


async def test_findymail_find_and_verify_health_are_separate() -> None:
    finder = FindymailProvider(KEY, backoff_seconds=0)
    with respx.mock() as mock:
        mock.post("https://app.findymail.com/api/verify").side_effect = [
            fixture("findymail", "402")
        ]
        mock.post("https://app.findymail.com/api/search/name").side_effect = [
            fixture("findymail", "found")
        ]
        assert (await finder.verify("jane.doe@acme.com")).error == "out of credits"
        assert (await finder.lookup(PERSON)).status == "found"
    rows = {(r["key"], r["kind"]): r for r in providers.status(_settings())}
    assert rows[("findymail", "email_finder")]["last_error"] is None
    assert rows[("findymail", "email_verifier")]["last_error"] == "out of credits"


@pytest.mark.parametrize(
    "response",
    [
        fixture("prospeo", "bad_400"),
        httpx.Response(400, text="<html>Bad Request</html>"),
        httpx.Response(200, text="not json"),
    ],
    ids=["400-without-error-field", "400-not-json", "200-not-json"],
)
async def test_prospeo_unreadable_answer_is_an_error(response: httpx.Response) -> None:
    with respx.mock() as mock:
        mock.post("https://api.prospeo.io/enrich-person").side_effect = [response]
        with structlog.testing.capture_logs() as logs:
            result = await ProspeoProvider(KEY, backoff_seconds=0).lookup(PERSON)
    assert result.status == "error" and result.error_kind == "vendor"
    assert result.cost_usd == 0
    assert logs and "provider.http_error" in _events(logs)
    _assert_key_never_logged(logs, result)


async def test_prospeo_internal_error_is_retried_like_a_5xx() -> None:
    with respx.mock() as mock:
        route = mock.post("https://api.prospeo.io/enrich-person")
        route.side_effect = [fixture("prospeo", "internal_error"), fixture("prospeo", "found")]
        result = await ProspeoProvider(KEY, backoff_seconds=0).lookup(PERSON)
    assert route.call_count == 2 and result.status == "found"
    with respx.mock() as mock:
        route = mock.post("https://api.prospeo.io/enrich-person")
        route.side_effect = [fixture("prospeo", "internal_error")] * 2
        result = await ProspeoProvider(KEY, backoff_seconds=0).lookup(PERSON)
    assert route.call_count == 2
    assert result.status == "error" and result.error == "vendor error"


async def test_transport_error_never_leaks_the_key() -> None:
    with respx.mock() as mock:
        mock.post(
            "https://api.leadmagic.io/v1/people/email-finder"
        ).side_effect = httpx.ConnectError(f"failed with header {KEY}")
        with structlog.testing.capture_logs() as logs:
            result = await LeadMagicProvider(KEY).lookup(PERSON)
    assert result.status == "error" and result.raw_ref == "leadmagic:transport"
    assert logs and "provider.transport_error" in _events(logs)
    _assert_key_never_logged(logs, result)


# ── registry ──────────────────────────────────────────────────────────────────


def _settings(**keys: str) -> Settings:
    values: dict[str, Any] = {
        "leadmagic_api_key": None,
        "findymail_api_key": None,
        "prospeo_api_key": None,
        "pdl_api_key": None,
        "hunter_api_key": None,
    }
    values |= {k: SecretStr(v) for k, v in keys.items()}
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def test_configured_keyed_only_in_workspace_order() -> None:
    s = _settings(leadmagic_api_key=KEY, prospeo_api_key=KEY)
    assert [p.key for p in providers.configured("email_finder", settings=s)] == [
        "leadmagic",
        "prospeo",
    ]
    ordered = providers.configured("email_finder", ["prospeo", "leadmagic"], settings=s)
    assert [p.key for p in ordered] == ["prospeo", "leadmagic"]
    assert providers.configured("email_verifier", settings=s) == []
    assert providers.configured("company", settings=s) == []


def test_configured_builds_every_kind_with_table_prices() -> None:
    s = _settings(findymail_api_key=KEY, pdl_api_key=KEY)
    verifiers = providers.configured(
        "email_verifier", settings=s, prices={"findymail_verify": Decimal("0.005")}
    )
    assert len(verifiers) == 1 and verifiers[0].kind == "email_verifier"
    assert isinstance(verifiers[0], Provider)
    assert [p.key for p in providers.configured("company", settings=s)] == ["pdl_company"]
    assert [p.key for p in providers.configured("person_search", settings=s)] == ["pdl_person"]


def test_blank_key_is_not_configured() -> None:
    s = _settings(leadmagic_api_key="  ")
    assert providers.configured("email_finder", settings=s) == []
    assert providers.api_key("leadmagic", s) is None


def test_status_rows_never_carry_a_key() -> None:
    s = _settings(pdl_api_key=KEY)
    rows = providers.status(s)
    assert {r["key"] for r in rows} == {
        "leadmagic",
        "findymail",
        "prospeo",
        "pdl_company",
        "pdl_person",
    }
    by_key = {(r["key"], r["kind"]): r for r in rows}
    assert by_key[("pdl_company", "company")]["configured"] is True
    assert by_key[("leadmagic", "email_finder")]["configured"] is False
    assert by_key[("findymail", "email_verifier")]["default_price_usd"] == "0.004"
    assert KEY not in repr(rows)


def test_hunter_is_an_optional_extra() -> None:
    assert providers.api_key("hunter", _settings()) is None
    assert providers.api_key("hunter", _settings(hunter_api_key="h-key")) == "h-key"
    assert providers.api_key("nobody", _settings()) is None
