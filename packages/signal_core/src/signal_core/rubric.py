"""The deterministic rubric: a Brief's evidence rules decide a verdict (P2). Pure, no IO.

A claim satisfies a rule when its key is the rule's predicate, the op holds, it is no older
than `max_age_days` (when set) and it carries a non-empty quote. A rule is met when the
satisfying claims come from at least `min_sources` distinct sources; a source is the
lower-cased URL host of the claim's record, else the record id, so two pages of one site
count once. A rule is touched when any claim is on its predicate.

Order: a met `disqualifies` rule → Contradicted; else at least one `requires` rule and all
met → Confirmed; else nothing touched → Unverified; else the case is open (None) and the
AI judges it.
"""

import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any, Literal
from urllib.parse import urlparse

from signal_core.brief_columns import EvidenceRule

Decided = Literal["Confirmed", "Contradicted", "Unverified"]


@dataclass(frozen=True)
class RubricClaim:
    id: str
    key: str
    value: Any
    quote: str | None
    confidence: int | None
    observed_at: datetime | None
    source: str


@dataclass(frozen=True)
class RuleResult:
    predicate: str
    kind: str
    op: str
    met: bool
    touched: bool
    sources: int
    claim_ids: list[str]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RubricOutcome:
    value: Decided | None  # None: open, the AI judges it
    rules: list[RuleResult]
    claim_ids: list[str]


def source_of(url: str | None, record_id: str) -> str:
    """The claim's source for independence: the URL's lower-cased host, else the record id."""
    if url:
        try:
            host = urlparse(url).hostname
        except ValueError:
            host = None
        if host:
            return host.lower()
    return record_id


def _is_number(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _op_holds(rule: EvidenceRule, value: Any) -> bool:
    op = rule.op
    if op == "is_true":
        return value is True
    if op == "is_false":
        return value is False
    if op == "present":
        return True
    if op == "at_least":
        return _is_number(value) and value >= rule.value
    if op == "one_of":
        return isinstance(value, str) and value.casefold() in {
            str(v).casefold() for v in rule.value
        }
    if op == "contains":
        return isinstance(value, str) and any(
            str(item).casefold() in value.casefold() for item in rule.value
        )
    return False


def _satisfies(rule: EvidenceRule, claim: RubricClaim, now: datetime) -> bool:
    if claim.key != rule.predicate or not _op_holds(rule, claim.value):
        return False
    if rule.max_age_days is not None and (
        claim.observed_at is None or now - claim.observed_at > timedelta(days=rule.max_age_days)
    ):
        return False
    return bool(claim.quote and claim.quote.strip())


def _union(results: list[RuleResult]) -> list[str]:
    out: list[str] = []
    for r in results:
        out.extend(i for i in r.claim_ids if i not in out)
    return out


def evaluate_rubric(
    rules: list[EvidenceRule], claims: list[RubricClaim], now: datetime
) -> RubricOutcome:
    results: list[RuleResult] = []
    for rule in rules:
        satisfying = [c for c in claims if _satisfies(rule, c, now)]
        sources = len({c.source for c in satisfying})
        results.append(
            RuleResult(
                predicate=rule.predicate,
                kind=rule.kind,
                op=rule.op,
                met=sources >= rule.min_sources,
                touched=any(c.key == rule.predicate for c in claims),
                sources=sources,
                claim_ids=[c.id for c in satisfying],
            )
        )

    disqualified = [r for r in results if r.kind == "disqualifies" and r.met]
    if disqualified:
        return RubricOutcome("Contradicted", results, _union(disqualified))
    required = [r for r in results if r.kind == "requires"]
    if required and all(r.met for r in required):
        return RubricOutcome("Confirmed", results, _union(required))
    if not any(r.touched for r in results):
        return RubricOutcome("Unverified", results, [])
    return RubricOutcome(None, results, [])


def describe_rule(rule: EvidenceRule) -> str:
    """The rule in plain words, for the verdict prompt."""
    what = {
        "is_true": "is true",
        "is_false": "is false",
        "present": "has any value",
        "at_least": f"is at least {rule.value}",
        "one_of": f"is one of: {', '.join(map(str, rule.value or []))}",
        "contains": f"contains any of: {', '.join(map(str, rule.value or []))}",
    }[rule.op]
    text = f"{rule.kind} {rule.predicate} {what}"
    text += f" (quoted, from at least {rule.min_sources} independent source"
    text += "s" if rule.min_sources > 1 else ""
    if rule.max_age_days is not None:
        text += f", observed within {rule.max_age_days} days"
    return text + ")"


def describe_result(rule: EvidenceRule, result: RuleResult) -> str:
    """What the evidence showed for the rule, for the verdict prompt."""
    state = "met" if result.met else "not met"
    seen = "claims on it" if result.touched else "no claims on it"
    return (
        f"{rule.kind} {rule.predicate} {rule.op}: {state}"
        f" ({result.sources} of {rule.min_sources} sources needed; {seen})"
    )
