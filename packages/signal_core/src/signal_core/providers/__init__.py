"""The provider registry: which providers exist, which have a key, and how they are doing.

Keys come only from the environment (`Settings`); the UI never takes one. A provider without a
key is left out of `configured()` and shows "Not set up yet" in `status()`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TypedDict

import httpx

from signal_core.config import Settings, get_settings
from signal_core.providers.base import (
    DEFAULT_PRICES,
    NOT_SET_UP,
    REJECTED_KEY,
    Provider,
    ProviderInput,
    ProviderKind,
    ProviderResult,
    health,
)
from signal_core.providers.findymail import FindymailProvider, FindymailVerifier
from signal_core.providers.leadmagic import LeadMagicProvider
from signal_core.providers.pdl import PdlCompany, PdlPerson
from signal_core.providers.prospeo import ProspeoProvider

__all__ = [
    "DEFAULT_ORDER",
    "NOT_SET_UP",
    "PROVIDERS",
    "REJECTED_KEY",
    "Provider",
    "ProviderInput",
    "ProviderKind",
    "ProviderResult",
    "ProviderSpec",
    "ProviderStatus",
    "api_key",
    "configured",
    "status",
]

Factory = Callable[[str, Decimal | None, httpx.AsyncClient | None], Provider]


@dataclass(frozen=True)
class ProviderSpec:
    key: str
    kind: ProviderKind
    setting: str  # the Settings attribute that holds the key
    build: Factory
    health_key: str | None = None  # where its last error is kept, when not `key`

    @property
    def health(self) -> str:
        return self.health_key or self.key


PROVIDERS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        "leadmagic",
        "email_finder",
        "leadmagic_api_key",
        lambda k, p, c: LeadMagicProvider(k, unit_price=p, client=c),
    ),
    ProviderSpec(
        "findymail",
        "email_finder",
        "findymail_api_key",
        lambda k, p, c: FindymailProvider(k, unit_price=p, client=c),
        "findymail_find",
    ),
    ProviderSpec(
        "prospeo",
        "email_finder",
        "prospeo_api_key",
        lambda k, p, c: ProspeoProvider(k, unit_price=p, client=c),
    ),
    ProviderSpec(
        "findymail",
        "email_verifier",
        "findymail_api_key",
        lambda k, p, c: FindymailVerifier(FindymailProvider(k, verify_price=p, client=c)),
        "findymail_verify",
    ),
    ProviderSpec(
        "pdl_company",
        "company",
        "pdl_api_key",
        lambda k, p, c: PdlCompany(k, unit_price=p, client=c),
    ),
    ProviderSpec(
        "pdl_person",
        "person_search",
        "pdl_api_key",
        lambda k, p, c: PdlPerson(k, unit_price=p, client=c),
    ),
)

# The waterfall order when the workspace has not set one (workspace_settings.provider_order).
DEFAULT_ORDER: tuple[str, ...] = ("leadmagic", "findymail", "prospeo")

# Keys for providers outside the registry's kinds (M7's Hunter domain search).
_EXTRA_SETTINGS = {"hunter": "hunter_api_key"}


def api_key(key: str, settings: Settings | None = None) -> str | None:
    """The provider's key from the environment, or None. Never log or return it to the web."""
    settings = settings or get_settings()
    attr = next((s.setting for s in PROVIDERS if s.key == key), None) or _EXTRA_SETTINGS.get(key)
    if attr is None:
        return None
    secret = getattr(settings, attr, None)
    if secret is None:
        return None
    value = str(secret.get_secret_value()).strip()
    return value or None


def configured(
    kind: ProviderKind,
    order: Iterable[str] | None = None,
    *,
    prices: Mapping[str, Decimal] | None = None,
    client: httpx.AsyncClient | None = None,
    settings: Settings | None = None,
) -> list[Provider]:
    """Keyed providers of one kind, in the workspace's order.

    Keys named in `order` come first, in that order; any other keyed provider of the kind follows
    in registry order. `prices` maps a provider key to its `model_price` row (verify uses the
    `findymail_verify` row).
    """
    settings = settings or get_settings()
    specs = [s for s in PROVIDERS if s.kind == kind]
    wanted = list(order) if order is not None else list(DEFAULT_ORDER)
    rank = {k: i for i, k in enumerate(wanted)}
    specs.sort(key=lambda s: rank.get(s.key, len(rank)))
    out: list[Provider] = []
    for spec in specs:
        key = api_key(spec.key, settings)
        if key is None:
            continue
        price_key = "findymail_verify" if spec.kind == "email_verifier" else spec.key
        price = (prices or {}).get(price_key)
        out.append(spec.build(key, price, client))
    return out


class ProviderStatus(TypedDict):
    key: str
    kind: ProviderKind
    configured: bool
    last_error: str | None
    last_error_at: datetime | None
    default_price_usd: str


def status(settings: Settings | None = None) -> list[ProviderStatus]:
    """One row per registry entry, for Settings → Providers. Never includes a key.

    Findymail has two rows (find, verify) whose health is kept apart.
    """
    settings = settings or get_settings()
    rows: list[ProviderStatus] = []
    for spec in PROVIDERS:
        h = health(spec.health)
        price_key = "findymail_verify" if spec.kind == "email_verifier" else spec.key
        rows.append(
            {
                "key": spec.key,
                "kind": spec.kind,
                "configured": api_key(spec.key, settings) is not None,
                "last_error": h.last_error,
                "last_error_at": h.last_error_at,
                "default_price_usd": str(DEFAULT_PRICES[price_key]),
            }
        )
    return rows
