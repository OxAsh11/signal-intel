"""One contract for every data provider (P6).

An adapter turns a vendor's API into `lookup(ProviderInput) -> ProviderResult`:

* `found` / `not_found` come from the vendor's answer. A vendor "not found" never raises.
* HTTP 4xx/5xx and timeouts give `error` with a short reason ("rejected the key", "timed out").
* Every request has a 15 s timeout and one retry on 429/5xx, with backoff.
* The API key travels only in a request header. It is never logged, never put in a result and
  never in an error reason: `scrub()` removes it from any text that leaves the helper.

Costs: an adapter reports `billable_units` (what the vendor charges for: one find, one verify,
one person returned) and `cost_usd = billable_units * unit price`. The unit price is handed in by
the caller from `public.model_price`; `DEFAULT_PRICES` only mirrors the seeded placeholders for
callers that have no table row yet.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal, Protocol, runtime_checkable

import httpx
from pydantic import BaseModel, ConfigDict, Field

from signal_core.logging import get_logger

log = get_logger(__name__)

ProviderKind = Literal["email_finder", "email_verifier", "company", "person_search"]
ResultStatus = Literal["found", "not_found", "error"]

TIMEOUT_SECONDS = 15.0
RETRY_BACKOFF_SECONDS = 1.0

# Seeded placeholder prices (USD per billable unit). The owner edits them in public.model_price;
# estimates read that table. These are only the fallback when no price is handed in.
DEFAULT_PRICES: dict[str, Decimal] = {
    "leadmagic": Decimal("0.012"),
    "findymail": Decimal("0.015"),
    "findymail_verify": Decimal("0.004"),
    "prospeo": Decimal("0.010"),
    "pdl_company": Decimal("0.10"),
    "pdl_person": Decimal("0.28"),
}

REJECTED_KEY = "rejected the key"
NOT_SET_UP = "not set up"


class ProviderInput(BaseModel):
    """What a lookup may use. Each adapter reads the fields its vendor needs."""

    model_config = ConfigDict(frozen=True)

    first_name: str | None = None
    last_name: str | None = None
    full_name: str | None = None
    domain: str | None = None
    company_name: str | None = None
    linkedin_url: str | None = None
    email: str | None = None
    # Person search: persona titles and how many people to return (1-10).
    titles: tuple[str, ...] = ()
    limit: int = Field(default=5, ge=1, le=10)

    def name(self) -> str | None:
        if self.full_name:
            return self.full_name
        joined = " ".join(p for p in (self.first_name, self.last_name) if p)
        return joined or None


class ProviderResult(BaseModel):
    """The one result shape. `value` depends on the kind:

    * email_finder: the email address (str)
    * email_verifier: "valid" or "invalid" (status `found` means the vendor gave a verdict)
    * company: a dict of company fields (dotted paths address it)
    * person_search: a list of person dicts
    """

    model_config = ConfigDict(frozen=True)

    provider: str
    status: ResultStatus
    value: Any = None
    confidence: int = Field(default=0, ge=0, le=100)
    raw_ref: str | None = None
    billable_units: int = 0
    cost_usd: Decimal = Decimal(0)
    error: str | None = None


@runtime_checkable
class Provider(Protocol):
    key: str
    kind: ProviderKind

    async def lookup(self, input: ProviderInput) -> ProviderResult: ...


def scrub(text: str, secret: str | None) -> str:
    """Remove the key from any text before it is logged or returned."""
    if secret:
        text = text.replace(secret, "[redacted]")
    return text


@dataclass(frozen=True)
class HttpOutcome:
    """A vendor response the adapter interprets, or an error reason when there is none to read."""

    status_code: int | None
    body: Any = None
    error: str | None = None


def _json_or_none(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def _reason_for(status_code: int) -> str:
    if status_code in (401, 403):
        return REJECTED_KEY
    if status_code == 402:
        return "out of credits"
    if status_code == 423:
        return "subscription paused"
    if status_code == 429:
        return "rate limited"
    if status_code >= 500:
        return f"vendor error {status_code}"
    return f"request refused ({status_code})"


async def request_json(
    *,
    provider: str,
    secret: str,
    method: str,
    url: str,
    headers: dict[str, str],
    json: Any = None,
    params: dict[str, Any] | None = None,
    client: httpx.AsyncClient | None = None,
    backoff_seconds: float = RETRY_BACKOFF_SECONDS,
    pass_through: frozenset[int] = frozenset(),
) -> HttpOutcome:
    """One vendor call: 15 s timeout, one retry on 429/5xx, the key scrubbed from every reason.

    A 2xx, or a status in `pass_through` (a vendor that says "not found" with 404 or 400), comes
    back with its body for the adapter to read; any other status comes back as an error reason.
    """
    owned = client is None
    http = client or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)
    try:
        for attempt in (1, 2):
            try:
                response = await http.request(
                    method, url, headers=headers, json=json, params=params, timeout=TIMEOUT_SECONDS
                )
            except httpx.TimeoutException:
                log.warning("provider.timeout", provider=provider, attempt=attempt)
                return HttpOutcome(None, error="timed out")
            except httpx.HTTPError as exc:
                reason = scrub(type(exc).__name__, secret)
                log.warning("provider.transport_error", provider=provider, error=reason)
                return HttpOutcome(None, error="could not reach the provider")

            code = response.status_code
            if 200 <= code < 300 or code in pass_through:
                return HttpOutcome(code, _json_or_none(response))
            retryable = code == 429 or code >= 500
            if retryable and attempt == 1:
                log.info("provider.retry", provider=provider, status_code=code)
                await asyncio.sleep(_retry_after(response, backoff_seconds))
                continue
            reason = _reason_for(code)
            log.warning("provider.http_error", provider=provider, status_code=code, reason=reason)
            return HttpOutcome(code, _json_or_none(response), error=reason)
        raise AssertionError("unreachable")  # pragma: no cover
    finally:
        if owned:
            await http.aclose()


def _retry_after(response: httpx.Response, default: float) -> float:
    """Honour a short Retry-After (capped at 5 s so one cell never stalls a run)."""
    raw = response.headers.get("Retry-After")
    if raw is None or default == 0:
        return default
    try:
        return min(max(float(raw), 0.0), 5.0)
    except ValueError:
        return default


# ── result helpers ────────────────────────────────────────────────────────────


@dataclass
class ProviderHealth:
    last_error: str | None = None
    last_error_at: datetime | None = None


_HEALTH: dict[str, ProviderHealth] = {}


def record(result: ProviderResult) -> ProviderResult:
    """Remember the provider's latest error for Settings → Providers ("Failing")."""
    health = _HEALTH.setdefault(result.provider, ProviderHealth())
    if result.status == "error":
        health.last_error = result.error
        health.last_error_at = datetime.now(UTC)
    else:
        health.last_error = None
        health.last_error_at = None
    return result


def health(key: str) -> ProviderHealth:
    return _HEALTH.get(key, ProviderHealth())


def reset_health() -> None:
    _HEALTH.clear()


def error_result(provider: str, reason: str, raw_ref: str | None = None) -> ProviderResult:
    return record(ProviderResult(provider=provider, status="error", error=reason, raw_ref=raw_ref))


def not_found(provider: str, raw_ref: str | None = None) -> ProviderResult:
    return record(ProviderResult(provider=provider, status="not_found", raw_ref=raw_ref))


def found(
    provider: str,
    value: Any,
    *,
    confidence: int,
    units: int,
    unit_price: Decimal,
    raw_ref: str | None = None,
) -> ProviderResult:
    return record(
        ProviderResult(
            provider=provider,
            status="found",
            value=value,
            confidence=max(0, min(100, confidence)),
            raw_ref=raw_ref,
            billable_units=units,
            cost_usd=unit_price * units,
        )
    )


class HttpProvider:
    """Shared constructor: the key, the unit price, an optional client and the retry backoff."""

    key: str
    kind: ProviderKind

    def __init__(
        self,
        api_key: str,
        *,
        unit_price: Decimal | None = None,
        client: httpx.AsyncClient | None = None,
        backoff_seconds: float = RETRY_BACKOFF_SECONDS,
    ) -> None:
        self._api_key = api_key
        self._price = DEFAULT_PRICES[self.key] if unit_price is None else unit_price
        self._client = client
        self._backoff = backoff_seconds

    def __repr__(self) -> str:  # never show the key
        return f"{type(self).__name__}(key={self.key!r})"

    async def _call(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        pass_through: frozenset[int] = frozenset(),
    ) -> HttpOutcome:
        return await request_json(
            provider=self.key,
            secret=self._api_key,
            method=method,
            url=url,
            headers=headers,
            json=json,
            params=params,
            client=self._client,
            backoff_seconds=self._backoff,
            pass_through=pass_through,
        )
