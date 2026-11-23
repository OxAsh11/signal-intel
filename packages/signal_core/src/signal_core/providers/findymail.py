"""Findymail: find an email from a name, and verify an email.

Docs: https://app.findymail.com/docs/ (read 2026-11-23)

* Auth: `Authorization: Bearer <key>`, JSON bodies.
* Find: `POST https://app.findymail.com/api/search/name` with `{"name", "domain"}`.
  Found: `{"contact": {"name", "domain", "email"}}`, 1 finder credit. Not found is free; the docs
  do not show its body, so a 200 without `contact.email` (or a 404) is read as not found.
* Verify: `POST https://app.findymail.com/api/verify` with `{"email"}`.
  Answer: `{"email", "verified": true|false, "provider"}`, 1 verifier credit whatever the result.
* Errors: 402 not enough credits, 423 subscription paused, 429 capacity reached.
"""

from __future__ import annotations

from decimal import Decimal

import httpx

from signal_core.providers.base import (
    DEFAULT_PRICES,
    RETRY_BACKOFF_SECONDS,
    HttpProvider,
    ProviderInput,
    ProviderKind,
    ProviderResult,
    error_result,
    found,
    not_found,
)

FIND_URL = "https://app.findymail.com/api/search/name"
VERIFY_URL = "https://app.findymail.com/api/verify"


class FindymailProvider(HttpProvider):
    key = "findymail"
    kind: ProviderKind = "email_finder"

    def __init__(
        self,
        api_key: str,
        *,
        unit_price: Decimal | None = None,
        verify_price: Decimal | None = None,
        client: httpx.AsyncClient | None = None,
        backoff_seconds: float = RETRY_BACKOFF_SECONDS,
    ) -> None:
        super().__init__(
            api_key, unit_price=unit_price, client=client, backoff_seconds=backoff_seconds
        )
        self._verify_price = (
            DEFAULT_PRICES["findymail_verify"] if verify_price is None else verify_price
        )

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}

    async def lookup(self, input: ProviderInput) -> ProviderResult:
        name = input.name()
        company = input.domain or input.company_name
        if not name or not company:
            return error_result(self.key, "needs a name and a domain")
        outcome = await self._call(
            "POST",
            FIND_URL,
            self._headers(),
            json={"name": name, "domain": company},
            pass_through=frozenset({404}),
        )
        if outcome.error:
            return error_result(self.key, outcome.error, f"findymail:http:{outcome.status_code}")
        data = outcome.body if isinstance(outcome.body, dict) else {}
        contact = data.get("contact") if outcome.status_code != 404 else None
        email = contact.get("email") if isinstance(contact, dict) else None
        if not email:
            return not_found(self.key, "findymail:search-name")
        # Findymail returns only addresses it has verified.
        return found(
            self.key,
            str(email),
            confidence=90,
            units=1,
            unit_price=self._price,
            raw_ref="findymail:search-name",
        )

    async def verify(self, email: str) -> ProviderResult:
        """The vendor's verdict on one address: value "valid" or "invalid" (always 1 credit)."""
        if not email or "@" not in email:
            return error_result(self.key, "needs an email to verify")
        outcome = await self._call("POST", VERIFY_URL, self._headers(), json={"email": email})
        if outcome.error:
            return error_result(self.key, outcome.error, f"findymail:http:{outcome.status_code}")
        data = outcome.body if isinstance(outcome.body, dict) else {}
        if "verified" not in data:
            return error_result(self.key, "unexpected answer", "findymail:verify")
        verified = data.get("verified") is True
        return found(
            self.key,
            "valid" if verified else "invalid",
            confidence=100 if verified else 0,
            units=1,
            unit_price=self._verify_price,
            raw_ref="findymail:verify",
        )


class FindymailVerifier:
    """Findymail verify under the email_verifier kind: `input.email` is the address checked."""

    key = "findymail"
    kind: ProviderKind = "email_verifier"

    def __init__(self, finder: FindymailProvider) -> None:
        self._finder = finder

    def __repr__(self) -> str:
        return "FindymailVerifier(key='findymail')"

    async def lookup(self, input: ProviderInput) -> ProviderResult:
        return await self._finder.verify(input.email or "")
