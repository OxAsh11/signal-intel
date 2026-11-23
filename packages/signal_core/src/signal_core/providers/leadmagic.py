"""LeadMagic email finder.

Docs: https://leadmagic.io/docs/reference/email-finder (read 2026-11-23)

* `POST https://api.leadmagic.io/v1/people/email-finder`, header `X-API-Key`.
* Body: `first_name` + `last_name` (or `full_name`) and `domain` (or `company_name`), or
  `profile_url` alone.
* Found: `{"email": "...", "status": "valid", "credits_consumed": 1, ...}`. LeadMagic returns only
  addresses that passed its live validation.
* Not found: `{"email": null, "status": null, "credits_consumed": 0, ...}`. A domain mismatch
  (`domain_mismatch: true`) is free and treated as not found.
"""

from __future__ import annotations

from typing import Any

from signal_core.providers.base import (
    HttpProvider,
    ProviderInput,
    ProviderKind,
    ProviderResult,
    error_result,
    found,
    not_found,
)

URL = "https://api.leadmagic.io/v1/people/email-finder"


class LeadMagicProvider(HttpProvider):
    key = "leadmagic"
    kind: ProviderKind = "email_finder"

    async def lookup(self, input: ProviderInput) -> ProviderResult:
        body: dict[str, Any] = {}
        if input.first_name and input.last_name:
            body |= {"first_name": input.first_name, "last_name": input.last_name}
        elif input.name():
            body["full_name"] = input.name()
        if input.domain:
            body["domain"] = input.domain
        elif input.company_name:
            body["company_name"] = input.company_name
        if input.linkedin_url:
            body["profile_url"] = input.linkedin_url
        has_person = "first_name" in body or "full_name" in body
        has_company = "domain" in body or "company_name" in body
        if not (has_person and has_company) and "profile_url" not in body:
            return error_result(self.key, "needs a name and a domain")

        outcome = await self._call(
            "POST",
            URL,
            {"X-API-Key": self._api_key, "Content-Type": "application/json"},
            json=body,
        )
        if outcome.error:
            return error_result(self.key, outcome.error, f"leadmagic:http:{outcome.status_code}")
        data = outcome.body if isinstance(outcome.body, dict) else {}
        email = data.get("email")
        if not email or data.get("domain_mismatch") or data.get("status") != "valid":
            return not_found(self.key, "leadmagic:email-finder")
        units = int(data.get("credits_consumed") or 1)
        return found(
            self.key,
            str(email),
            confidence=95,
            units=units,
            unit_price=self._price,
            raw_ref="leadmagic:email-finder",
        )
