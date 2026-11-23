"""Prospeo person enrichment, used as an email finder.

Docs: https://prospeo.io/api-docs/enrich-person (read 2026-11-23). Prospeo's current API has no
separate "email finder" endpoint; Enrich Person is the one that returns a work email.

* `POST https://api.prospeo.io/enrich-person`, header `X-KEY`.
* Body: `{"data": {first_name, last_name | full_name, company_website | company_name,
  linkedin_url}, "only_verified_email": true}`.
* Found: 200 `{"error": false, "free_enrichment": bool, "person": {"email": {"email", "status":
  "VERIFIED", "revealed": bool, ...}}, "company": {...}}`.
* Not found: 400 `{"error": true, "error_code": "NO_MATCH"}` (free).
* Other errors are also 400 with an `error_code`: `INVALID_API_KEY`, `INSUFFICIENT_CREDITS`,
  `INVALID_DATAPOINTS`, `INVALID_REQUEST`, `INTERNAL_ERROR`; 429 for the rate limit.
"""

from __future__ import annotations

from typing import Any

from signal_core.providers.base import (
    REJECTED_KEY,
    HttpProvider,
    ProviderInput,
    ProviderKind,
    ProviderResult,
    error_result,
    found,
    not_found,
)

URL = "https://api.prospeo.io/enrich-person"

_ERRORS = {
    "INVALID_API_KEY": REJECTED_KEY,
    "INSUFFICIENT_CREDITS": "out of credits",
    "INVALID_DATAPOINTS": "not enough to match on",
    "INVALID_REQUEST": "request refused",
    "INTERNAL_ERROR": "vendor error",
}


class ProspeoProvider(HttpProvider):
    key = "prospeo"
    kind: ProviderKind = "email_finder"

    async def lookup(self, input: ProviderInput) -> ProviderResult:
        data: dict[str, Any] = {}
        if input.first_name and input.last_name:
            data |= {"first_name": input.first_name, "last_name": input.last_name}
        elif input.name():
            data["full_name"] = input.name()
        if input.domain:
            data["company_website"] = input.domain
        elif input.company_name:
            data["company_name"] = input.company_name
        if input.linkedin_url:
            data["linkedin_url"] = input.linkedin_url
        has_person = "first_name" in data or "full_name" in data
        has_company = "company_website" in data or "company_name" in data
        if not (has_person and has_company) and "linkedin_url" not in data:
            return error_result(self.key, "needs a name and a domain")

        outcome = await self._call(
            "POST",
            URL,
            {"X-KEY": self._api_key, "Content-Type": "application/json"},
            json={"data": data, "only_verified_email": True},
            pass_through=frozenset({400}),
        )
        if outcome.error:
            return error_result(self.key, outcome.error, f"prospeo:http:{outcome.status_code}")
        body = outcome.body if isinstance(outcome.body, dict) else {}
        if body.get("error"):
            code = str(body.get("error_code") or "")
            if code == "NO_MATCH":
                return not_found(self.key, "prospeo:enrich-person")
            return error_result(self.key, _ERRORS.get(code, "request refused"), f"prospeo:{code}")

        raw_person = body.get("person")
        person: dict[str, Any] = raw_person if isinstance(raw_person, dict) else {}
        email_obj = person.get("email")
        if not isinstance(email_obj, dict):
            return not_found(self.key, "prospeo:enrich-person")
        email = email_obj.get("email")
        # A masked address ("j.*****@acme.com") is not a found email.
        if not email or not email_obj.get("revealed", True) or "*" in str(email):
            return not_found(self.key, "prospeo:enrich-person")
        verified = str(email_obj.get("status") or "").upper() == "VERIFIED"
        return found(
            self.key,
            str(email),
            confidence=95 if verified else 60,
            units=0 if body.get("free_enrichment") else 1,
            unit_price=self._price,
            raw_ref="prospeo:enrich-person",
        )
