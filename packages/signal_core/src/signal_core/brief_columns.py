"""A Brief's typed columns (predicate specs with a scoring role) and evidence rules.

Mirrors `app.validate_brief_columns` (migrations 0038 and 0039): Python accepts exactly what SQL
accepts, and also checks that a formula parses and that its `inputs` are the columns it reads.
AI output is cleaned, never coerced: an invalid column or rule drops only itself, and a rule
that points at a dropped column drops too. Legacy configs hold plain key strings in
`predicates`; every reader here treats those as "a key with no spec".
"""

import math
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from signal_core.formula import FormulaSyntaxError, parse, references

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
MAX_FORMULA = 500
MAX_INPUTS = 10

# Always fullmatch: with match, "^...$" lets a key with a trailing newline through.
_KEY_RE = re.compile(r"[a-z][a-z0-9_]{1,39}")

ValueType = Literal["boolean", "number", "enum", "text", "date"]
FillMethod = Literal["extract", "formula"]
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
    fill_method: FillMethod = "extract"
    formula: str | None = None
    inputs: list[str] | None = None

    @field_validator("key", mode="before")
    @classmethod
    def _key(cls, v: object) -> str:
        if not isinstance(v, str) or not _KEY_RE.fullmatch(v):
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

    @field_validator("fill_method", mode="before")
    @classmethod
    def _fill_method(cls, v: object) -> str:
        if v is None:
            return "extract"
        if not isinstance(v, str) or v not in ("extract", "formula"):
            raise ValueError("fill_method must be extract or formula")
        return v

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

    @model_validator(mode="after")
    def _fill(self) -> "ColumnSpec":
        f, ins = self.formula, self.inputs
        if self.fill_method != "formula":
            if f is not None:
                raise ValueError("only a formula column has a formula")
            if ins:
                raise ValueError("only a formula column has inputs")
            return self
        if f is None or not f.strip() or len(f) > MAX_FORMULA:
            raise ValueError(f"a formula column needs a formula of 1-{MAX_FORMULA} characters")
        if ins is None or not 1 <= len(ins) <= MAX_INPUTS or len(set(ins)) != len(ins):
            raise ValueError(f"a formula reads 1-{MAX_INPUTS} other columns")
        if self.key in ins:
            raise ValueError("a formula cannot read its own column")
        try:
            reads = references(parse(f))
        except FormulaSyntaxError as exc:
            raise ValueError(f"formula: {exc}") from None
        if sorted(ins) != reads:
            raise ValueError(
                f"inputs must be exactly the columns the formula reads ({', '.join(reads)})"
            )
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
    if spec.fill_method == "formula":  # derived claims carry no quote: no rule can hold
        return "an evidence rule cannot point at a formula column"
    if rule.op not in OPS_BY_TYPE[spec.value_type]:
        return f'a {spec.value_type} column has no rule "{rule.op}"'
    if rule.op == "one_of" and not set(rule.value) <= set(spec.allowed_values or []):
        return "one_of needs values from its allowed values"
    return None


def clean_columns(
    raw_predicates: list[Any],
    raw_rules: list[Any],
    existing_types: dict[str, str] | None = None,
) -> tuple[list[ColumnSpec], list[EvidenceRule], list[str]]:
    """Valid specs, valid rules, and the reasons anything was dropped. First duplicate wins.

    *existing_types* maps the workspace's predicate keys to their value_type: a column whose key
    exists there with another type drops (publishing it would fail), and so do its rules."""
    existing_types = existing_types or {}
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
        held = existing_types.get(spec.key)
        if held is not None and held != spec.value_type:
            dropped.append(
                f'column "{spec.key}" already exists in this workspace as {held};'
                " give it another key"
            )
        elif spec.key in seen:
            dropped.append(f'column "{spec.key}": key is used twice')
        elif len(specs) >= MAX_COLUMNS:
            dropped.append(f'column "{spec.key}": a Brief has at most {MAX_COLUMNS} columns')
        else:
            seen.add(spec.key)
            specs.append(spec)
    raw_keys = {r.get("key") for r in raw_predicates if isinstance(r, dict)}
    legacy = {r for r in raw_predicates if isinstance(r, str)}
    specs = _drop_bad_formulas(specs, raw_keys, legacy, dropped)
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


def formula_cycle(inputs: dict[str, list[str]]) -> list[str] | None:
    """A shortest dependency cycle among formula columns (key -> input keys), named from its
    first key back to itself (["a", "b", "a"]), or None. Mirrors app.formula_cycle: peel the
    columns whose inputs are all placed, then search from each remaining key in key order."""
    placed: set[str] = set()
    changed = True
    while changed:
        changed = False
        for key in sorted(inputs):
            if key not in placed and all(i not in inputs or i in placed for i in inputs[key]):
                placed.add(key)
                changed = True
    rest = sorted(k for k in inputs if k not in placed)
    best: list[str] | None = None
    for start in rest:
        parent = {start: start}
        front = [start]
        found: str | None = None
        while front and found is None:
            nxt: list[str] = []
            for u in front:
                for w in sorted(i for i in inputs[u] if i in rest):
                    if w == start:
                        found = u
                        break
                    if w not in parent:
                        parent[w] = u
                        nxt.append(w)
                if found is not None:
                    break
            front = nxt
        if found is not None:
            path = [found]
            while path[0] != start:
                path.insert(0, parent[path[0]])
            path.append(start)
            if best is None or len(path) < len(best):
                best = path
    return best


def _drop_bad_formulas(
    specs: list[ColumnSpec], raw_keys: set[Any], legacy: set[str], dropped: list[str]
) -> list[ColumnSpec]:
    """Drop formula columns whose inputs are not kept columns of this Brief, then any cycle;
    repeat, since a formula reading a dropped column drops too."""
    kept = list(specs)
    while True:
        keys = {s.key for s in kept}
        bad: tuple[ColumnSpec, str] | None = None
        for spec in kept:
            missing = next((k for k in spec.inputs or [] if k not in keys), None)
            if missing is None:
                continue
            if missing in raw_keys:
                reason = f'input "{missing}" was dropped'
            elif missing in legacy:
                reason = f'formula input "{missing}" has no column spec; give it one first'
            else:
                reason = f'input "{missing}" is not a column of this Brief'
            bad = (spec, f'column "{spec.key}": {reason}')
            break
        if bad is not None:
            kept.remove(bad[0])
            dropped.append(bad[1])
            continue
        cycle = formula_cycle(
            {s.key: list(s.inputs or []) for s in kept if s.fill_method == "formula"}
        )
        if cycle is None:
            return kept
        dropped.append(f"formula columns form a cycle: {' → '.join(cycle)}")
        kept = [s for s in kept if s.key not in cycle]


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


def extract_keys(config: dict[str, Any]) -> list[str]:
    """The keys extraction may fill, in order: legacy key strings and the extract columns. A
    column filled any other way (a formula) never reaches an extraction prompt."""
    keys: list[str] = []
    for raw in _items(config, "predicates"):
        if isinstance(raw, dict):
            if (raw.get("fill_method") or "extract") != "extract":
                continue
            key = raw.get("key")
        else:
            key = raw
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
