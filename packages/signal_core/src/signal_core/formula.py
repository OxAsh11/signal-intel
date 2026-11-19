"""The formula column language (D5): a small, safe expression language.

A hand-written tokenizer, a recursive-descent parser and a tree-walking evaluator: nothing here
uses eval, the ast module or builds code from text. The rules (grammar, functions, null, truth,
types, limits) are fixed in the P3 Global Constraints, and packages/formula/cases.json is the
parity contract this module shares with its TypeScript twin (@signal/formula).

Number semantics, chosen to match a JavaScript double exactly:
  * an integer literal and the results of + - * on integers stay Python ints while they fit in
    +-(2**53 - 1); beyond that they become floats (as every JS number is);
  * / always divides as floats; division by zero and any non-finite result give null;
  * concat prints numbers the way JavaScript's String(n) does (2.0 -> "2", 1e-7 -> "1e-7");
  * round works on the shortest decimal form of the number, half away from zero, so
    round(1.005, 2) is 1.01 in both languages.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

JsonScalar = str | int | float | bool | None

MAX_LENGTH = 500
MAX_TOKENS = 200
MAX_DEPTH = 32
MAX_SAFE_INT = 2**53 - 1

KEYWORDS = frozenset({"and", "or", "not", "true", "false", "null"})
# name -> (fewest, most) arguments; None means no upper bound.
FUNCTIONS: dict[str, tuple[int, int | None]] = {
    "coalesce": (1, None),
    "concat": (1, None),
    "if": (3, 3),
    "contains": (2, 2),
    "lower": (1, 1),
    "upper": (1, 1),
    "round": (1, 2),
    "len": (1, 1),
    "is_empty": (1, 1),
    "days_since": (1, 1),
}
SYSTEM_VALUES = ("name", "domain")
COMPARISONS = ("=", "!=", "<", "<=", ">", ">=")
MAX_ROUND_PLACES = 6


class FormulaSyntaxError(ValueError):
    """The formula cannot be parsed; *position* is the 0-based character it fails at."""

    def __init__(self, message: str, position: int) -> None:
        super().__init__(f"{message} (at character {position})")
        self.message = message
        self.position = position


class FormulaTypeError(ValueError):
    """A value of the wrong type reached an operator or function while evaluating."""


# ---------------------------------------------------------------------------
# The tree
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Num:
    value: int | float


@dataclass(frozen=True, slots=True)
class Str:
    value: str


@dataclass(frozen=True, slots=True)
class Bool:
    value: bool


@dataclass(frozen=True, slots=True)
class Null:
    pass


@dataclass(frozen=True, slots=True)
class SysRef:
    name: str  # "name" or "domain"


@dataclass(frozen=True, slots=True)
class Ref:
    key: str


@dataclass(frozen=True, slots=True)
class Unary:
    op: str  # "-" or "not"
    operand: Node


@dataclass(frozen=True, slots=True)
class Binary:
    op: str  # "or" "and" "=" "!=" "<" "<=" ">" ">=" "+" "-" "*" "/"
    left: Node
    right: Node


@dataclass(frozen=True, slots=True)
class Call:
    name: str
    args: tuple[Node, ...]


Node = Num | Str | Bool | Null | SysRef | Ref | Unary | Binary | Call


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Token:
    kind: str  # "num" "str" "ident" "sys" "op" "eof"
    text: str
    pos: int
    value: int | float | str | None = None


def _digit(c: str) -> bool:
    return "0" <= c <= "9"


def _lower(c: str) -> bool:
    return "a" <= c <= "z"


def _word(c: str) -> bool:
    return _lower(c) or _digit(c) or c == "_"


def _int_or_float(text: str) -> int | float:
    if "." in text:
        return float(text)  # a literal too large to hold is inf, made null on evaluation
    n = int(text)
    return n if n <= MAX_SAFE_INT else float(text)


def _tokenize(expr: str) -> list[_Token]:
    toks: list[_Token] = []
    i, n = 0, len(expr)

    def add(tok: _Token) -> None:
        if len(toks) >= MAX_TOKENS:
            raise FormulaSyntaxError(f"a formula has at most {MAX_TOKENS} tokens", tok.pos)
        toks.append(tok)

    while i < n:
        c = expr[i]
        start = i
        if c in " \t\r\n":
            i += 1
        elif _digit(c):
            while i < n and _digit(expr[i]):
                i += 1
            if i < n and expr[i] == ".":
                if not (i + 1 < n and _digit(expr[i + 1])):
                    raise FormulaSyntaxError("a number needs digits after its point", i)
                i += 1
                while i < n and _digit(expr[i]):
                    i += 1
            text = expr[start:i]
            add(_Token("num", text, start, _int_or_float(text)))
        elif c == '"':
            i += 1
            chars: list[str] = []
            while True:
                if i >= n:
                    raise FormulaSyntaxError("text is not closed with a quote", start)
                ch = expr[i]
                if ch == '"':
                    i += 1
                    break
                if ch == "\\":
                    if i + 1 < n and expr[i + 1] in '"\\':
                        chars.append(expr[i + 1])
                        i += 2
                        continue
                    raise FormulaSyntaxError('the only escapes are \\" and \\\\', i)
                chars.append(ch)
                i += 1
            add(_Token("str", expr[start:i], start, "".join(chars)))
        elif c == "@":
            i += 1
            while i < n and _word(expr[i]):
                i += 1
            name = expr[start + 1 : i]
            if name not in SYSTEM_VALUES:
                raise FormulaSyntaxError(
                    f'unknown system value "@{name}"; use @name or @domain', start
                )
            add(_Token("sys", expr[start:i], start, name))
        elif _lower(c):
            while i < n and _word(expr[i]):
                i += 1
            add(_Token("ident", expr[start:i], start))
        elif expr.startswith(("!=", "<=", ">="), i):
            i += 2
            add(_Token("op", expr[start:i], start))
        elif c in "=<>+-*/(),":
            i += 1
            add(_Token("op", c, start))
        else:
            raise FormulaSyntaxError(f'unexpected character "{c}"', i)
    toks.append(_Token("eof", "", n))
    return toks


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


class _Parser:
    def __init__(self, toks: list[_Token]) -> None:
        self.toks = toks
        self.i = 0
        self.depth = 0

    @property
    def peek(self) -> _Token:
        return self.toks[self.i]

    def advance(self) -> _Token:
        tok = self.toks[self.i]
        if tok.kind != "eof":
            self.i += 1
        return tok

    def at(self, kind: str, text: str) -> bool:
        tok = self.peek
        return tok.kind == kind and tok.text == text

    @staticmethod
    def error(message: str, tok: _Token) -> FormulaSyntaxError:
        return FormulaSyntaxError(message, tok.pos)

    def describe(self, tok: _Token) -> str:
        return "the end of the formula" if tok.kind == "eof" else f'"{tok.text}"'

    def open_paren(self) -> None:
        tok = self.advance()  # "("
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise self.error(f"a formula nests at most {MAX_DEPTH} parentheses deep", tok)

    def close_paren(self) -> None:
        if not self.at("op", ")"):
            raise self.error(f'expected ")", found {self.describe(self.peek)}', self.peek)
        self.advance()
        self.depth -= 1

    def expr(self) -> Node:
        return self.or_()

    def or_(self) -> Node:
        left = self.and_()
        while self.at("ident", "or"):
            self.advance()
            left = Binary("or", left, self.and_())
        return left

    def and_(self) -> Node:
        left = self.not_()
        while self.at("ident", "and"):
            self.advance()
            left = Binary("and", left, self.not_())
        return left

    def not_(self) -> Node:
        if self.at("ident", "not"):
            self.advance()
            return Unary("not", self.not_())
        return self.cmp()

    def cmp(self) -> Node:
        left = self.add()
        if self.peek.kind == "op" and self.peek.text in COMPARISONS:
            op = self.advance().text
            left = Binary(op, left, self.add())
            if self.peek.kind == "op" and self.peek.text in COMPARISONS:
                raise self.error("comparisons do not chain; join them with and", self.peek)
        return left

    def add(self) -> Node:
        left = self.mul()
        while self.peek.kind == "op" and self.peek.text in ("+", "-"):
            op = self.advance().text
            left = Binary(op, left, self.mul())
        return left

    def mul(self) -> Node:
        left = self.unary()
        while self.peek.kind == "op" and self.peek.text in ("*", "/"):
            op = self.advance().text
            left = Binary(op, left, self.unary())
        return left

    def unary(self) -> Node:
        if self.at("op", "-"):
            self.advance()
            return Unary("-", self.unary())
        return self.atom()

    def atom(self) -> Node:
        tok = self.peek
        if tok.kind == "num":
            self.advance()
            assert isinstance(tok.value, (int, float))
            return Num(tok.value)
        if tok.kind == "str":
            self.advance()
            assert isinstance(tok.value, str)
            return Str(tok.value)
        if tok.kind == "sys":
            self.advance()
            assert isinstance(tok.value, str)
            return SysRef(tok.value)
        if tok.kind == "ident":
            return self.word()
        if tok.kind == "op" and tok.text == "(":
            self.open_paren()
            node = self.expr()
            self.close_paren()
            return node
        raise self.error(f"expected a value, found {self.describe(tok)}", tok)

    def word(self) -> Node:
        tok = self.advance()
        text = tok.text
        if text in ("true", "false"):
            return Bool(text == "true")
        if text == "null":
            return Null()
        if text in KEYWORDS:
            raise self.error(f'"{text}" is a keyword, not a value', tok)
        if self.at("op", "("):
            return self.call(tok)
        if not 2 <= len(text) <= 40:
            raise self.error(f'"{text}" is not a column key: keys have 2-40 characters', tok)
        return Ref(text)

    def call(self, name_tok: _Token) -> Node:
        name = name_tok.text
        if name not in FUNCTIONS:
            raise self.error(f'unknown function "{name}"', name_tok)
        self.open_paren()
        args: list[Node] = []
        if not self.at("op", ")"):
            args.append(self.expr())
            while self.at("op", ","):
                self.advance()
                args.append(self.expr())
        self.close_paren()
        fewest, most = FUNCTIONS[name]
        if len(args) < fewest or (most is not None and len(args) > most):
            if most is None:
                takes = f"at least {fewest} argument{'s' if fewest > 1 else ''}"
            elif fewest == most:
                takes = f"{fewest} argument{'s' if fewest > 1 else ''}"
            else:
                takes = f"{fewest} to {most} arguments"
            raise self.error(f"{name} takes {takes}, not {len(args)}", name_tok)
        return Call(name, tuple(args))


def parse(expr: str) -> Node:
    """The formula's tree, or FormulaSyntaxError (with the 0-based position it fails at)."""
    if len(expr) > MAX_LENGTH:
        raise FormulaSyntaxError(f"a formula has at most {MAX_LENGTH} characters", MAX_LENGTH)
    parser = _Parser(_tokenize(expr))
    node = parser.expr()
    if parser.peek.kind != "eof":
        raise parser.error(f"unexpected {parser.describe(parser.peek)}", parser.peek)
    return node


def references(node: Node) -> list[str]:
    """The column keys the formula reads, sorted and distinct (system values excluded)."""
    keys: set[str] = set()
    stack: list[Node] = [node]
    while stack:
        n = stack.pop()
        match n:
            case Ref(key):
                keys.add(key)
            case Unary(_, operand):
                stack.append(operand)
            case Binary(_, left, right):
                stack += [left, right]
            case Call(_, args):
                stack += args
    return sorted(keys)


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------


def _is_num(v: object) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _type_name(v: object) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "boolean"
    if _is_num(v):
        return "number"
    if isinstance(v, str):
        return "text"
    return "a value of another kind"


def _number(x: int | float) -> int | float | None:
    """A JS-compatible number: big ints become floats, a non-finite float becomes null."""
    if isinstance(x, int):
        return x if -MAX_SAFE_INT <= x <= MAX_SAFE_INT else float(x)
    return x if math.isfinite(x) else None


def parse_iso_date(text: str) -> date | None:
    """A real calendar date written exactly as YYYY-MM-DD; None for anything else."""
    if len(text) != 10 or text[4] != "-" or text[7] != "-":
        return None
    parts = (text[0:4], text[5:7], text[8:10])
    if not all(all(_digit(c) for c in p) for p in parts):
        return None
    try:
        return date(int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        return None


def number_text(x: int | float) -> str:
    """The number as JavaScript's String(x) prints it: shortest round-trip digits."""
    if isinstance(x, int):
        return str(x)
    if not math.isfinite(x):
        return "NaN" if math.isnan(x) else ("Infinity" if x > 0 else "-Infinity")
    if x == int(x) and abs(x) < 1e21:
        return str(int(x))
    sign = "-" if x < 0 else ""
    tup = Decimal(repr(abs(x))).as_tuple()
    digits = "".join(str(d) for d in tup.digits)
    assert isinstance(tup.exponent, int)
    exp = tup.exponent
    while len(digits) > 1 and digits.endswith("0"):
        digits = digits[:-1]
        exp += 1
    k = len(digits)
    point = k + exp  # the value is 0.<digits> x 10^point
    if k <= point <= 21:
        body = digits + "0" * (point - k)
    elif 0 < point <= 21:
        body = digits[:point] + "." + digits[point:]
    elif -6 < point <= 0:
        body = "0." + "0" * (-point) + digits
    else:
        e = point - 1
        mantissa = digits[0] + ("." + digits[1:] if k > 1 else "")
        body = f"{mantissa}e{'+' if e >= 0 else '-'}{abs(e)}"
    return sign + body


def _text(v: JsonScalar) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return number_text(v)
    return v


def _round(x: int | float, places: int) -> int | float | None:
    if isinstance(x, int):
        return x
    if x == math.floor(x):  # whole already (every float of 2**52 or more is)
        return _number(int(x)) if places == 0 else x
    q = Decimal(repr(x)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    return _number(int(q)) if places == 0 else float(q)


class _Evaluator:
    def __init__(
        self, values: Mapping[str, JsonScalar], system: Mapping[str, str | None], today: date
    ) -> None:
        self.values = values
        self.system = system
        self.today = today

    def truth(self, v: JsonScalar, where: str) -> bool:
        if v is None or v is False:
            return False
        if v is True:
            return True
        raise FormulaTypeError(f"{where} needs true or false, got {_type_name(v)}")

    def run(self, node: Node) -> JsonScalar:
        match node:
            case Num(value):
                return _number(value)
            case Str(value):
                return value
            case Bool(value):
                return value
            case Null():
                return None
            case SysRef(name):
                return self.system.get(name)
            case Ref(key):
                v = self.values.get(key)
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    return _number(v)  # a column number behaves like a JS number too
                return v
            case Unary(op, operand):
                return self.unary(op, self.run(operand))
            case Binary(op, left, right):
                return self.binary(op, self.run(left), self.run(right))
            case Call(name, args):
                return self.call(name, args)
        raise AssertionError(f"unknown node {node!r}")  # pragma: no cover

    def unary(self, op: str, v: JsonScalar) -> JsonScalar:
        if op == "not":
            return not self.truth(v, "not")
        if v is None:
            return None
        if not _is_num(v):
            raise FormulaTypeError(f'"-" needs a number, got {_type_name(v)}')
        assert isinstance(v, (int, float))
        return _number(-v)

    def binary(self, op: str, a: JsonScalar, b: JsonScalar) -> JsonScalar:
        # Both sides are always evaluated: and/or never short-circuit, so a type error on
        # either side always shows.
        if op == "and":
            left = self.truth(a, "and")
            return self.truth(b, "and") and left
        if op == "or":
            left = self.truth(a, "or")
            return self.truth(b, "or") or left
        if a is None or b is None:
            return None
        if op in ("+", "-", "*", "/"):
            if not (_is_num(a) and _is_num(b)):
                raise FormulaTypeError(
                    f'"{op}" needs two numbers, got {_type_name(a)} and {_type_name(b)}'
                )
            assert isinstance(a, (int, float)) and isinstance(b, (int, float))
            try:
                if op == "+":
                    return _number(a + b)
                if op == "-":
                    return _number(a - b)
                if op == "*":
                    return _number(a * b)
                return None if b == 0 else _number(a / b)
            except OverflowError:
                return None
        same = (
            (_is_num(a) and _is_num(b))
            or (isinstance(a, str) and isinstance(b, str))
            or (isinstance(a, bool) and isinstance(b, bool))
        )
        if op in ("=", "!="):
            if not same:
                raise FormulaTypeError(
                    f'"{op}" compares values of one type, got {_type_name(a)} and {_type_name(b)}'
                )
            return (a == b) == (op == "=")
        if isinstance(a, bool) or not same:
            raise FormulaTypeError(
                f'"{op}" compares two numbers or two texts, got {_type_name(a)} and {_type_name(b)}'
            )
        assert isinstance(a, (int, float, str)) and isinstance(b, (int, float, str))
        if op == "<":
            return a < b  # type: ignore[operator]
        if op == "<=":
            return a <= b  # type: ignore[operator]
        if op == ">":
            return a > b  # type: ignore[operator]
        return a >= b  # type: ignore[operator]

    def text_arg(self, name: str, v: JsonScalar) -> str:
        if not isinstance(v, str):
            raise FormulaTypeError(f"{name} needs text, got {_type_name(v)}")
        return v

    def call(self, name: str, arg_nodes: tuple[Node, ...]) -> JsonScalar:
        if name == "if":  # only the branch taken is evaluated
            cond = self.truth(self.run(arg_nodes[0]), "the condition of if")
            return self.run(arg_nodes[1] if cond else arg_nodes[2])
        args = [self.run(a) for a in arg_nodes]
        if name == "coalesce":
            return next((a for a in args if a is not None and a != ""), None)
        if name == "concat":
            return "".join(_text(a) for a in args)
        if name == "is_empty":
            return args[0] is None or args[0] == ""
        if any(a is None for a in args):
            return None
        if name == "contains":
            text = self.text_arg("contains", args[0])
            sub = self.text_arg("contains", args[1])
            return sub.lower() in text.lower()
        if name == "lower":
            return self.text_arg("lower", args[0]).lower()
        if name == "upper":
            return self.text_arg("upper", args[0]).upper()
        if name == "len":
            return len(self.text_arg("len", args[0]))
        if name == "days_since":
            raw = args[0]
            day = parse_iso_date(raw) if isinstance(raw, str) else None
            if day is None:
                raise FormulaTypeError(f"days_since needs a YYYY-MM-DD date, got {_type_name(raw)}")
            return (self.today - day).days
        if name == "round":
            x = args[0]
            if isinstance(x, bool) or not isinstance(x, (int, float)):
                raise FormulaTypeError(f"round needs a number, got {_type_name(x)}")
            places = args[1] if len(args) > 1 else 0
            if (
                isinstance(places, bool)
                or not isinstance(places, (int, float))
                or places != int(places)
                or not 0 <= places <= MAX_ROUND_PLACES
            ):
                raise FormulaTypeError(
                    f"round takes a whole number of places from 0 to {MAX_ROUND_PLACES}"
                )
            return _round(x, int(places))
        raise AssertionError(f"unknown function {name}")  # pragma: no cover


def evaluate(
    node: Node,
    values: Mapping[str, JsonScalar],
    system: Mapping[str, str | None],
    today: date,
) -> JsonScalar:
    """The formula's value for one entity.

    *values* maps column keys to their current value (a missing key is null); *system* holds
    "name" and "domain"; *today* is the evaluation date for days_since. Raises FormulaTypeError.
    """
    return _Evaluator(values, system, today).run(node)


def check_result(
    value: JsonScalar, value_type: str, allowed_values: list[str] | None
) -> str | None:
    """Why *value* cannot fill a column of *value_type*, or None when it can (or is null).

    A null result is the caller's no_data, not an error."""
    if value is None:
        return None
    non_finite = isinstance(value, float) and not math.isfinite(value)
    if value_type == "boolean":
        ok = isinstance(value, bool)
    elif value_type == "number":
        ok = _is_num(value) and not non_finite
    elif value_type == "text":
        ok = isinstance(value, str)
    elif value_type == "enum":
        ok = isinstance(value, str) and value in (allowed_values or [])
    elif value_type == "date":
        ok = isinstance(value, str) and parse_iso_date(value) is not None
    else:
        raise ValueError(f"unknown value_type {value_type!r}")
    if ok:
        return None
    if non_finite:
        got = "non-finite number"
    elif value_type == "enum" and isinstance(value, str):
        got = "text outside the allowed values"
    else:
        got = _type_name(value)
    return f"formula returned {got}, column is {value_type}"
