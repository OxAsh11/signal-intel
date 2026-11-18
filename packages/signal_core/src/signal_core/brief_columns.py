"""A Brief's typed columns (predicate specs with a scoring role) and evidence rules.

Mirrors `app.validate_brief_columns` (migration 0038): Python accepts exactly what SQL accepts.
AI output is cleaned, never coerced: an invalid column or rule drops only itself, and a rule
that points at a dropped column drops too. Legacy configs hold plain key strings in
`predicates`; every reader here treats those as "a key with no spec".
"""

import math
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

ROLES: tuple[str, ...] = (
    "signal",
    "fit.vertical",
    "fit.geography",
    "fit.size",
    "fit.commercial_role",
    "scale",
    "info",
)

OPS_BY_TYPE: dict[str, tuple[str, ...]] = {
    "boolean": ("is_true", "is_false", "present"),
    "number": ("at_least", "present"),
    "enum": ("one_of", "present"),
    "text": ("contains", "present"),
    "date": ("present",),
}

ICP_LIST_BY_ROLE: dict[str, str] = {
    "fit.vertical": "icp.verticals",
    "fit.geography": "icp.geographies",
    "fit.size": "icp.size_bands",
    "fit.commercial_role": "icp.commercial_roles",
}

MAX_COLUMNS = 12
MAX_RULES = 10

_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,39}$")

ValueType = Literal["boolean", "number", "enum", "text", "date"]
Role = Literal[
    "signal", "fit.vertical", "fit.geography", "fit.size", "fit.commercial_role", "scale", "info"
]


def _is_number(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _whole(v: object, lo: int, hi: int, what: str) -> int:
    """An integral JSON number in [lo, hi] (365.0 counts, like SQL); never a bool or string."""
    if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v):
        raise ValueError(f"{what} must be a whole number")
    if v != int(v):
        raise ValueError(f"{what} must be a whole number")
    n = int(v)
    if not lo <= n <= hi:
        raise ValueError(f"{what} must be {lo}-{hi}")
    return n


class ColumnSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")

    key: str
    label: str
    value_type: ValueType
    allowed_values: list[str] | None = None
    extraction_hint: str | None = ""
    decay_half_life_days: int | None = None
    role: Role

    @field_validator("key", mode="before")
    @classmethod
    def _key(cls, v: object) -> str:
        if not isinstance(v, str) or not _KEY_RE.match(v):
            raise ValueError(
                "key must be 2-40 lowercase letters, digits or underscores, starting with a letter"
            )
        return v

    @field_validator("label", mode="before")
    @classmethod
    def _label(cls, v: object) -> str:
        if not isinstance(v, str) or not v.strip() or len(v) > 80:
            raise ValueError("label needs 1-80 characters")
        return v

    @field_validator("extraction_hint", mode="before")
    @classmethod
    def _hint(cls, v: object) -> str | None:
        if v is not None and (not isinstance(v, str) or len(v) > 300):
            raise ValueError("extraction_hint must be text of at most 300 characters")
        return v

    @field_validator("decay_half_life_days", mode="before")
    @classmethod
    def _decay(cls, v: object) -> int | None:
        return None if v is None else _whole(v, 1, 3650, "decay_half_life_days")

    @model_validator(mode="after")
    def _enum_values(self) -> "ColumnSpec":
        vals = self.allowed_values
        if self.value_type == "enum":
            if (
                vals is None
                or not 2 <= len(vals) <= 12
                or any(not v.strip() for v in vals)
                or len(set(vals)) != len(vals)
            ):
                raise ValueError("enum needs 2-12 allowed values")
        elif vals is not None:
            raise ValueError("only an enum has allowed values")
        return self


class EvidenceRule(BaseModel):
    model_config = ConfigDict(extra="ignore")

    predicate: str
    kind: Literal["requires", "disqualifies"]
    op: Literal["is_true", "is_false", "present", "at_least", "one_of", "contains"]
    value: Any = None
    min_sources: int = 1
    max_age_days: int | None = None

    @field_validator("min_sources", mode="before")
    @classmethod
    def _min_sources(cls, v: object) -> int:
        return 1 if v is None else _whole(v, 1, 5, "min_sources")

    @field_validator("max_age_days", mode="before")
    @classmethod
    def _max_age(cls, v: object) -> int | None:
        return None if v is None else _whole(v, 1, 3650, "max_age_days")

    @model_validator(mode="after")
    def _value_shape(self) -> "EvidenceRule":
        v = self.value
        if self.op == "at_least":
            if not _is_number(v):
                raise ValueError("at_least needs a number")
        elif self.op in ("one_of", "contains"):
            if not isinstance(v, list) or not v or any(not isinstance(e, str) for e in v):
                raise ValueError(f"{self.op} needs a non-empty list of strings")
            if self.op == "one_of" and any(not e.strip() for e in v):
                raise ValueError("one_of needs non-blank values")
        elif v is not None:
            raise ValueError(f'rule "{self.op}" takes no value')
        return self


def _first_error(exc: ValidationError) -> str:
    err = exc.errors()[0]
    msg = str(err["msg"]).removeprefix("Value error, ")
    loc = ".".join(str(p) for p in err["loc"])
    return f"{loc}: {msg}" if loc and loc not in msg and "__root__" not in loc else msg


def _rule_problem(rule: EvidenceRule, spec: ColumnSpec | None) -> str | None:
    if spec is None:
        return "points at a column this Brief does not define"
    if rule.op not in OPS_BY_TYPE[spec.value_type]:
        return f'a {spec.value_type} column has no rule "{rule.op}"'
    if rule.op == "one_of" and not set(rule.value) <= set(spec.allowed_values or []):
        return "one_of needs values from its allowed values"
    return None


def clean_columns(
    raw_predicates: list[Any], raw_rules: list[Any]
) -> tuple[list[ColumnSpec], list[EvidenceRule], list[str]]:
    """Valid specs, valid rules, and the reasons anything was dropped. First duplicate wins."""
    specs: list[ColumnSpec] = []
    rules: list[EvidenceRule] = []
    dropped: list[str] = []
    seen: set[str] = set()
    for raw in raw_predicates:
        if not isinstance(raw, dict):
            dropped.append(f'column "{raw}": not a column object')
            continue
        try:
            spec = ColumnSpec.model_validate(raw)
        except ValidationError as exc:
            dropped.append(f'column "{raw.get("key", "")}": {_first_error(exc)}')
            continue
        if spec.key in seen:
            dropped.append(f'column "{spec.key}": key is used twice')
        elif len(specs) >= MAX_COLUMNS:
            dropped.append(f'column "{spec.key}": a Brief has at most {MAX_COLUMNS} columns')
        else:
            seen.add(spec.key)
            specs.append(spec)
    by_key = {s.key: s for s in specs}
    for raw in raw_rules:
        if not isinstance(raw, dict):
            dropped.append(f'rule "{raw}": not a rule object')
            continue
        try:
            rule = EvidenceRule.model_validate(raw)
        except ValidationError as exc:
            dropped.append(f'rule on "{raw.get("predicate", "")}": {_first_error(exc)}')
            continue
        problem = _rule_problem(rule, by_key.get(rule.predicate))
        if problem:
            dropped.append(f'rule on "{rule.predicate}": {problem}')
        elif rule in rules:
            dropped.append(f'rule on "{rule.predicate}": duplicate rule')
        elif len(rules) >= MAX_RULES:
            dropped.append(f'rule on "{rule.predicate}": a Brief has at most {MAX_RULES} rules')
        else:
            rules.append(rule)
    return specs, rules, dropped


def _items(config: dict[str, Any], name: str) -> list[Any]:
    v = config.get(name)
    return v if isinstance(v, list) else []


def column_specs(config: dict[str, Any]) -> list[ColumnSpec]:
    """The config's valid column specs. Legacy key strings and invalid items are ignored."""
    out: list[ColumnSpec] = []
    for raw in _items(config, "predicates"):
        if isinstance(raw, dict):
            try:
                out.append(ColumnSpec.model_validate(raw))
            except ValidationError:
                continue
    return out


def predicate_keys(config: dict[str, Any]) -> list[str]:
    """Every column key in the config: legacy strings and spec objects alike, in order."""
    keys: list[str] = []
    for raw in _items(config, "predicates"):
        key = raw if isinstance(raw, str) else raw.get("key") if isinstance(raw, dict) else None
        if isinstance(key, str) and key and key not in keys:
            keys.append(key)
    return keys


def evidence_rules(config: dict[str, Any]) -> list[EvidenceRule]:
    """The config's valid rules, each pointing at a spec in the same config."""
    by_key = {s.key: s for s in column_specs(config)}
    out: list[EvidenceRule] = []
    for raw in _items(config, "evidence_rules"):
        if not isinstance(raw, dict):
            continue
        try:
            rule = EvidenceRule.model_validate(raw)
        except ValidationError:
            continue
        if _rule_problem(rule, by_key.get(rule.predicate)) is None:
            out.append(rule)
    return out
