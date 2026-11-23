"""People Data Labs: Company Enrichment and Person Search.

Docs (read 2026-11-23):
* Auth: https://docs.peopledatalabs.com/docs/authentication — header `X-Api-Key`; 401 on a bad key.
* Company: https://docs.peopledatalabs.com/docs/reference-company-enrichment-api and
  https://docs.peopledatalabs.com/docs/output-response-company-enrichment-api
  `GET https://api.peopledatalabs.com/v5/company/enrich?website=<domain>` (or `profile`, `name`).
  200: the company's fields at the top level with `status` and `likelihood` (1-10); 404: no match
  (charged per match only). Field names follow the Company Schema
  (https://docs.peopledatalabs.com/docs/company-schema): `name`, `size`, `employee_count`,
  `industry`, `founded`, `website`, `linkedin_url`, `location.{locality, region, country}`.
* Person: https://docs.peopledatalabs.com/docs/reference-person-search-api
  `POST https://api.peopledatalabs.com/v5/person/search` with `{"query": <Elasticsearch>, "size"}`.
  200: `{"status", "data": [...], "total", "scroll_token"}`; one credit per record returned. The
  reference says no matches is a 200 with `total: 0`; a 404 is also read as no matches.
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

COMPANY_URL = "https://api.peopledatalabs.com/v5/company/enrich"
PERSON_URL = "https://api.peopledatalabs.com/v5/person/search"

COMPANY_FIELDS = (
    "name",
    "size",
    "employee_count",
    "industry",
    "founded",
    "website",
    "linkedin_url",
)
PERSON_FIELDS = (
    "full_name",
    "first_name",
    "last_name",
    "job_title",
    "work_email",
    "linkedin_url",
    "location_name",
    "location_locality",
    "location_region",
    "location_country",
)


class PdlCompany(HttpProvider):
    key = "pdl_company"
    kind: ProviderKind = "company"

    async def lookup(self, input: ProviderInput) -> ProviderResult:
        params: dict[str, Any]
        if input.domain:
            params = {"website": input.domain}
        elif input.linkedin_url:
            params = {"profile": input.linkedin_url}
        elif input.company_name:
            params = {"name": input.company_name}
        else:
            return error_result(self.key, "needs a domain")
        outcome = await self._call(
            "GET",
            COMPANY_URL,
            {"X-Api-Key": self._api_key, "Accept": "application/json"},
            params=params | {"titlecase": "true"},
            pass_through=frozenset({404}),
        )
        if outcome.error:
            return error_result(self.key, outcome.error, f"pdl:http:{outcome.status_code}")
        body = outcome.body if isinstance(outcome.body, dict) else {}
        if outcome.status_code == 404 or not body.get("name"):
            return not_found(self.key, "pdl:company-enrich")
        value: dict[str, Any] = {f: body.get(f) for f in COMPANY_FIELDS}
        raw_location = body.get("location")
        location: dict[str, Any] = raw_location if isinstance(raw_location, dict) else {}
        value["location"] = {k: location.get(k) for k in ("name", "locality", "region", "country")}
        value["id"] = body.get("id")
        likelihood = int(body.get("likelihood") or 0)
        return found(
            self.key,
            value,
            confidence=likelihood * 10,
            units=1,
            unit_price=self._price,
            raw_ref=f"pdl:company:{body.get('id') or ''}",
        )


def person_query(domain: str, titles: tuple[str, ...]) -> dict[str, Any]:
    """People at the company's website whose job title matches any persona title."""
    must: list[dict[str, Any]] = [{"term": {"job_company_website": domain.lower()}}]
    wanted = [t.strip() for t in titles if t and t.strip()]
    if wanted:
        must.append(
            {
                "bool": {
                    "should": [{"match_phrase": {"job_title": t.lower()}} for t in wanted],
                    "minimum_should_match": 1,
                }
            }
        )
    return {"bool": {"must": must}}


def _person(record: dict[str, Any]) -> dict[str, Any]:
    """The fields we keep. On plans without a field's entitlement PDL sends `true` in place of
    the value, so anything that is not a string is dropped; a work email must look like one."""
    person: dict[str, Any] = {}
    for field in (*PERSON_FIELDS, "id"):
        raw = record.get(field)
        person[field] = raw if isinstance(raw, str) and raw.strip() else None
    if person["work_email"] and "@" not in person["work_email"]:
        person["work_email"] = None
    return person


class PdlPerson(HttpProvider):
    key = "pdl_person"
    kind: ProviderKind = "person_search"

    async def lookup(self, input: ProviderInput) -> ProviderResult:
        if not input.domain:
            return error_result(self.key, "needs a domain")
        outcome = await self._call(
            "POST",
            PERSON_URL,
            {
                "X-Api-Key": self._api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json={
                "query": person_query(input.domain, input.titles),
                "size": input.limit,
                "titlecase": True,
            },
            pass_through=frozenset({404}),
        )
        if outcome.error:
            return error_result(self.key, outcome.error, f"pdl:http:{outcome.status_code}")
        body = outcome.body if isinstance(outcome.body, dict) else {}
        records = body.get("data") if outcome.status_code != 404 else None
        rows = records if isinstance(records, list) else []
        people = [_person(r) for r in rows if isinstance(r, dict)][: input.limit]
        if not people:
            return not_found(self.key, "pdl:person-search")
        return found(
            self.key,
            people,
            confidence=80,
            units=len(people),
            unit_price=self._price,
            raw_ref=f"pdl:person-search:{body.get('total', len(people))}",
        )
