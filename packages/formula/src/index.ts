/**
 * The formula column language (D5): a small, safe expression language.
 *
 * A hand-written tokenizer, a recursive-descent parser and a tree-walking evaluator: nothing here
 * uses eval, Function or builds code from text. This is the TypeScript twin of
 * packages/signal_core/src/signal_core/formula.py; both are held to ../cases.json.
 *
 * Parity notes (Python is the reference):
 *  - positions, the 500-character limit, len and text ordering all count Unicode code points,
 *    as Python's str does, never UTF-16 units;
 *  - a JS number is already the double Python mirrors, so + - * / are plain JS arithmetic and a
 *    non-finite result is null;
 *  - concat prints numbers with String(n), which Python's number_text copies;
 *  - round works on the shortest decimal form of the number, half away from zero, so
 *    round(1.005, 2) is 1.01 in both languages.
 */

export type Scalar = string | number | boolean | null;

export type Node =
  | { readonly kind: 'num'; readonly value: number }
  | { readonly kind: 'str'; readonly value: string }
  | { readonly kind: 'bool'; readonly value: boolean }
  | { readonly kind: 'null' }
  | { readonly kind: 'sys'; readonly name: 'name' | 'domain' }
  | { readonly kind: 'ref'; readonly key: string }
  | { readonly kind: 'unary'; readonly op: '-' | 'not'; readonly operand: Node }
  | { readonly kind: 'binary'; readonly op: BinaryOp; readonly left: Node; readonly right: Node }
  | { readonly kind: 'call'; readonly name: string; readonly args: readonly Node[] };

export type BinaryOp = 'or' | 'and' | '=' | '!=' | '<' | '<=' | '>' | '>=' | '+' | '-' | '*' | '/';

/** The entity's system values: its display name and primary domain (null when it has none). */
export type SystemValues = { readonly name?: string | null; readonly domain?: string | null };

export const MAX_LENGTH = 500;
export const MAX_TOKENS = 200;
export const MAX_DEPTH = 32;
const MAX_ROUND_PLACES = 6;

const KEYWORDS = new Set(['and', 'or', 'not', 'true', 'false', 'null']);
// name -> [fewest, most] arguments; null means no upper bound.
const FUNCTIONS: ReadonlyMap<string, readonly [number, number | null]> = new Map([
  ['coalesce', [1, null]],
  ['concat', [1, null]],
  ['if', [3, 3]],
  ['contains', [2, 2]],
  ['lower', [1, 1]],
  ['upper', [1, 1]],
  ['round', [1, 2]],
  ['len', [1, 1]],
  ['is_empty', [1, 1]],
  ['days_since', [1, 1]],
]);
const COMPARISONS: ReadonlySet<string> = new Set(['=', '!=', '<', '<=', '>', '>=']);
const ADDITIVE: ReadonlySet<string> = new Set(['+', '-']);
const MULTIPLICATIVE: ReadonlySet<string> = new Set(['*', '/']);

/** The formula cannot be parsed; `position` is the 0-based character (code point) it fails at. */
export class FormulaSyntaxError extends Error {
  readonly position: number;
  /** The message without the position suffix. */
  readonly reason: string;

  constructor(reason: string, position: number) {
    super(`${reason} (at character ${position})`);
    this.name = 'FormulaSyntaxError';
    this.reason = reason;
    this.position = position;
  }
}

/** A value of the wrong type reached an operator or function while evaluating. */
export class FormulaTypeError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'FormulaTypeError';
  }
}

// ---------------------------------------------------------------------------
// Tokenizer
// ---------------------------------------------------------------------------

type Token = {
  readonly kind: 'num' | 'str' | 'ident' | 'sys' | 'op' | 'eof';
  readonly text: string;
  readonly pos: number;
  readonly num?: number;
  readonly str?: string;
};

const isDigit = (c: string | undefined): boolean => c !== undefined && c >= '0' && c <= '9';
const isLower = (c: string | undefined): boolean => c !== undefined && c >= 'a' && c <= 'z';
const isWord = (c: string | undefined): boolean => isLower(c) || isDigit(c) || c === '_';

/** Tokenizes `chars` (the formula split into code points). */
function tokenize(chars: readonly string[]): Token[] {
  const toks: Token[] = [];
  const n = chars.length;
  let i = 0;
  const slice = (a: number, b: number): string => chars.slice(a, b).join('');
  const add = (tok: Token): void => {
    if (toks.length >= MAX_TOKENS) {
      throw new FormulaSyntaxError(`a formula has at most ${MAX_TOKENS} tokens`, tok.pos);
    }
    toks.push(tok);
  };

  while (i < n) {
    const c = chars[i] as string;
    const start = i;
    if (c === ' ' || c === '\t' || c === '\r' || c === '\n') {
      i += 1;
    } else if (isDigit(c)) {
      while (i < n && isDigit(chars[i])) i += 1;
      if (i < n && chars[i] === '.') {
        if (!isDigit(chars[i + 1])) {
          throw new FormulaSyntaxError('a number needs digits after its point', i);
        }
        i += 1;
        while (i < n && isDigit(chars[i])) i += 1;
      }
      const text = slice(start, i);
      // Number() rounds correctly, like Python's int/float; too large a literal is Infinity,
      // made null on evaluation.
      add({ kind: 'num', text, pos: start, num: Number(text) });
    } else if (c === '"') {
      i += 1;
      let value = '';
      for (;;) {
        if (i >= n) throw new FormulaSyntaxError('text is not closed with a quote', start);
        const ch = chars[i] as string;
        if (ch === '"') {
          i += 1;
          break;
        }
        if (ch === '\\') {
          const next = chars[i + 1];
          if (next === '"' || next === '\\') {
            value += next;
            i += 2;
            continue;
          }
          throw new FormulaSyntaxError('the only escapes are \\" and \\\\', i);
        }
        value += ch;
        i += 1;
      }
      add({ kind: 'str', text: slice(start, i), pos: start, str: value });
    } else if (c === '@') {
      i += 1;
      while (i < n && isWord(chars[i])) i += 1;
      const name = slice(start + 1, i);
      if (name !== 'name' && name !== 'domain') {
        throw new FormulaSyntaxError(
          `unknown system value "@${name}"; use @name or @domain`,
          start,
        );
      }
      add({ kind: 'sys', text: slice(start, i), pos: start, str: name });
    } else if (isLower(c)) {
      while (i < n && isWord(chars[i])) i += 1;
      add({ kind: 'ident', text: slice(start, i), pos: start });
    } else if ((c === '!' || c === '<' || c === '>') && chars[i + 1] === '=') {
      i += 2;
      add({ kind: 'op', text: slice(start, i), pos: start });
    } else if ('=<>+-*/(),'.includes(c)) {
      i += 1;
      add({ kind: 'op', text: c, pos: start });
    } else {
      throw new FormulaSyntaxError(`unexpected character "${c}"`, i);
    }
  }
  toks.push({ kind: 'eof', text: '', pos: n });
  return toks;
}

// ---------------------------------------------------------------------------
// Parser
// ---------------------------------------------------------------------------

function freeze<T extends Node>(node: T): T {
  return Object.freeze(node);
}

class Parser {
  private i = 0;
  private depth = 0;

  constructor(private readonly toks: readonly Token[]) {}

  get peek(): Token {
    return this.toks[this.i] as Token;
  }

  private advance(): Token {
    const tok = this.peek;
    if (tok.kind !== 'eof') this.i += 1;
    return tok;
  }

  private at(kind: Token['kind'], text: string): boolean {
    const tok = this.peek;
    return tok.kind === kind && tok.text === text;
  }

  describe(tok: Token): string {
    return tok.kind === 'eof' ? 'the end of the formula' : `"${tok.text}"`;
  }

  private openParen(): void {
    const tok = this.advance(); // "("
    this.depth += 1;
    if (this.depth > MAX_DEPTH) {
      throw new FormulaSyntaxError(
        `a formula nests at most ${MAX_DEPTH} parentheses deep`,
        tok.pos,
      );
    }
  }

  private closeParen(): void {
    if (!this.at('op', ')')) {
      throw new FormulaSyntaxError(
        `expected ")", found ${this.describe(this.peek)}`,
        this.peek.pos,
      );
    }
    this.advance();
    this.depth -= 1;
  }

  expr(): Node {
    return this.or();
  }

  private or(): Node {
    let left = this.and();
    while (this.at('ident', 'or')) {
      this.advance();
      left = freeze({ kind: 'binary', op: 'or', left, right: this.and() });
    }
    return left;
  }

  private and(): Node {
    let left = this.not();
    while (this.at('ident', 'and')) {
      this.advance();
      left = freeze({ kind: 'binary', op: 'and', left, right: this.not() });
    }
    return left;
  }

  private not(): Node {
    if (this.at('ident', 'not')) {
      this.advance();
      return freeze({ kind: 'unary', op: 'not', operand: this.not() });
    }
    return this.cmp();
  }

  private isOp(set: ReadonlySet<string>): boolean {
    return this.peek.kind === 'op' && set.has(this.peek.text);
  }

  private cmp(): Node {
    let left = this.add();
    if (this.isOp(COMPARISONS)) {
      const op = this.advance().text as BinaryOp;
      left = freeze({ kind: 'binary', op, left, right: this.add() });
      if (this.isOp(COMPARISONS)) {
        throw new FormulaSyntaxError('comparisons do not chain; join them with and', this.peek.pos);
      }
    }
    return left;
  }

  private add(): Node {
    let left = this.mul();
    while (this.isOp(ADDITIVE)) {
      const op = this.advance().text as BinaryOp;
      left = freeze({ kind: 'binary', op, left, right: this.mul() });
    }
    return left;
  }

  private mul(): Node {
    let left = this.unary();
    while (this.isOp(MULTIPLICATIVE)) {
      const op = this.advance().text as BinaryOp;
      left = freeze({ kind: 'binary', op, left, right: this.unary() });
    }
    return left;
  }

  private unary(): Node {
    if (this.at('op', '-')) {
      this.advance();
      return freeze({ kind: 'unary', op: '-', operand: this.unary() });
    }
    return this.atom();
  }

  private atom(): Node {
    const tok = this.peek;
    if (tok.kind === 'num') {
      this.advance();
      return freeze({ kind: 'num', value: tok.num as number });
    }
    if (tok.kind === 'str') {
      this.advance();
      return freeze({ kind: 'str', value: tok.str as string });
    }
    if (tok.kind === 'sys') {
      this.advance();
      return freeze({ kind: 'sys', name: tok.str as 'name' | 'domain' });
    }
    if (tok.kind === 'ident') return this.word();
    if (tok.kind === 'op' && tok.text === '(') {
      this.openParen();
      const node = this.expr();
      this.closeParen();
      return node;
    }
    throw new FormulaSyntaxError(`expected a value, found ${this.describe(tok)}`, tok.pos);
  }

  private word(): Node {
    const tok = this.advance();
    const text = tok.text;
    if (text === 'true' || text === 'false')
      return freeze({ kind: 'bool', value: text === 'true' });
    if (text === 'null') return freeze({ kind: 'null' });
    if (KEYWORDS.has(text)) {
      throw new FormulaSyntaxError(`"${text}" is a keyword, not a value`, tok.pos);
    }
    if (this.at('op', '(')) return this.call(tok);
    // An ident is ASCII, so its UTF-16 length is its code point count.
    if (text.length < 2 || text.length > 40) {
      throw new FormulaSyntaxError(
        `"${text}" is not a column key: keys have 2-40 characters`,
        tok.pos,
      );
    }
    return freeze({ kind: 'ref', key: text });
  }

  private call(nameTok: Token): Node {
    const name = nameTok.text;
    const arity = FUNCTIONS.get(name);
    if (arity === undefined)
      throw new FormulaSyntaxError(`unknown function "${name}"`, nameTok.pos);
    this.openParen();
    const args: Node[] = [];
    if (!this.at('op', ')')) {
      args.push(this.expr());
      while (this.at('op', ',')) {
        this.advance();
        args.push(this.expr());
      }
    }
    this.closeParen();
    const [fewest, most] = arity;
    if (args.length < fewest || (most !== null && args.length > most)) {
      const s = fewest > 1 ? 's' : '';
      let takes: string;
      if (most === null) takes = `at least ${fewest} argument${s}`;
      else if (fewest === most) takes = `${fewest} argument${s}`;
      else takes = `${fewest} to ${most} arguments`;
      throw new FormulaSyntaxError(`${name} takes ${takes}, not ${args.length}`, nameTok.pos);
    }
    return freeze({ kind: 'call', name, args: Object.freeze(args) });
  }
}

/** The formula's tree, or FormulaSyntaxError (with the 0-based position it fails at). */
export function parse(expr: string): Node {
  const chars = Array.from(expr); // code points, as Python indexes a str
  if (chars.length > MAX_LENGTH) {
    throw new FormulaSyntaxError(`a formula has at most ${MAX_LENGTH} characters`, MAX_LENGTH);
  }
  const parser = new Parser(tokenize(chars));
  const node = parser.expr();
  if (parser.peek.kind !== 'eof') {
    throw new FormulaSyntaxError(`unexpected ${parser.describe(parser.peek)}`, parser.peek.pos);
  }
  return node;
}

/** The column keys the formula reads, sorted and distinct (system values excluded). */
export function references(node: Node): string[] {
  const keys = new Set<string>();
  const stack: Node[] = [node];
  while (stack.length > 0) {
    const n = stack.pop() as Node;
    if (n.kind === 'ref') keys.add(n.key);
    else if (n.kind === 'unary') stack.push(n.operand);
    else if (n.kind === 'binary') stack.push(n.left, n.right);
    else if (n.kind === 'call') stack.push(...n.args);
  }
  // Keys are ASCII, so the default sort (UTF-16 order) is Python's sorted() order.
  return [...keys].sort();
}

// ---------------------------------------------------------------------------
// Evaluator
// ---------------------------------------------------------------------------

function typeName(v: unknown): string {
  if (v === null) return 'null';
  if (typeof v === 'boolean') return 'boolean';
  if (typeof v === 'number') return 'number';
  if (typeof v === 'string') return 'text';
  return 'a value of another kind';
}

/** A finite number, or null. */
function finite(x: number): number | null {
  return Number.isFinite(x) ? x : null;
}

/** Compares two strings by Unicode code point, as Python does (JS `<` compares UTF-16 units). */
function compareText(a: string, b: string): number {
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    const ca = a.codePointAt(i) as number;
    const cb = b.codePointAt(j) as number;
    if (ca !== cb) return ca < cb ? -1 : 1;
    i += ca > 0xffff ? 2 : 1;
    j += cb > 0xffff ? 2 : 1;
  }
  return (a.length - i > 0 ? 1 : 0) - (b.length - j > 0 ? 1 : 0);
}

/** Days since 0001-01-01 for a proleptic Gregorian date (no Date object: years 1-99 included). */
function dayNumber(y: number, m: number, d: number): number {
  const yy = m <= 2 ? y - 1 : y;
  const era = Math.floor(yy / 400);
  const yoe = yy - era * 400;
  const mp = (m + 9) % 12;
  const doy = Math.floor((153 * mp + 2) / 5) + d - 1;
  const doe = yoe * 365 + Math.floor(yoe / 4) - Math.floor(yoe / 100) + doy;
  return era * 146097 + doe;
}

function daysInMonth(y: number, m: number): number {
  if (m === 2) return (y % 4 === 0 && y % 100 !== 0) || y % 400 === 0 ? 29 : 28;
  return [4, 6, 9, 11].includes(m) ? 30 : 31;
}

/** The day number of a real calendar date written exactly as YYYY-MM-DD; null for anything else. */
export function parseIsoDate(text: string): number | null {
  if (!/^[0-9]{4}-[0-9]{2}-[0-9]{2}$/.test(text)) return null;
  const y = Number(text.slice(0, 4));
  const m = Number(text.slice(5, 7));
  const d = Number(text.slice(8, 10));
  if (y < 1 || m < 1 || m > 12 || d < 1 || d > daysInMonth(y, m)) return null;
  return dayNumber(y, m, d);
}

function text(v: Scalar): string {
  if (v === null) return '';
  if (typeof v === 'boolean') return v ? 'true' : 'false';
  if (typeof v === 'number') return String(v);
  return v;
}

/**
 * Rounds the shortest decimal form of `x` to `places`, half away from zero (Python's
 * Decimal(repr(x)).quantize(..., ROUND_HALF_UP)).
 */
function roundNumber(x: number, places: number): number {
  if (Number.isInteger(x)) return x; // whole already (every double of 2**52 or more is)
  const m = /^(-?)([0-9]+)(?:\.([0-9]+))?(?:e([+-][0-9]+))?$/.exec(String(x));
  if (m === null) throw new Error(`cannot read the digits of ${x}`); // finite numbers always match
  const sign = m[1] ?? '';
  const frac = m[3] ?? '';
  const digits = (m[2] ?? '') + frac;
  // |x| = digits x 10^exp, exactly as printed.
  const exp = Number(m[4] ?? '0') - frac.length;
  const drop = -(exp + places); // digits to the right of the kept place
  let kept: bigint;
  if (drop <= 0) {
    kept = BigInt(digits) * 10n ** BigInt(-drop);
  } else {
    const head = digits.length > drop ? digits.slice(0, digits.length - drop) : '';
    const first = digits.length >= drop ? (digits[digits.length - drop] as string) : '0';
    kept = BigInt(head === '' ? '0' : head) + (first >= '5' ? 1n : 0n);
  }
  const value = Number(`${sign}${kept}e-${places}`);
  // Python: int(Decimal('-0')) is 0 for places 0, float(Decimal('-0.00')) is -0.0 otherwise.
  return places === 0 && value === 0 ? 0 : value;
}

class Evaluator {
  constructor(
    private readonly values: Readonly<Record<string, unknown>>,
    private readonly system: SystemValues,
    private readonly today: number,
  ) {}

  private truth(v: Scalar, where: string): boolean {
    if (v === null || v === false) return false;
    if (v === true) return true;
    throw new FormulaTypeError(`${where} needs true or false, got ${typeName(v)}`);
  }

  run(node: Node): Scalar {
    switch (node.kind) {
      case 'num':
        return finite(node.value);
      case 'str':
        return node.value;
      case 'bool':
        return node.value;
      case 'null':
        return null;
      case 'sys': {
        const v = Object.hasOwn(this.system, node.name) ? this.system[node.name] : null;
        return v ?? null;
      }
      case 'ref': {
        const v = Object.hasOwn(this.values, node.key) ? this.values[node.key] : null;
        if (v === null || v === undefined) return null;
        if (typeof v === 'boolean' || typeof v === 'string') return v;
        if (typeof v === 'number') return finite(v);
        // A JSONB claim value may be a list or an object: not a formula value.
        throw new FormulaTypeError(`column "${node.key}" holds a list or object, not a value`);
      }
      case 'unary':
        return this.unary(node.op, this.run(node.operand));
      case 'binary':
        return this.binary(node.op, this.run(node.left), this.run(node.right));
      case 'call':
        return this.call(node.name, node.args);
    }
  }

  private unary(op: '-' | 'not', v: Scalar): Scalar {
    if (op === 'not') return !this.truth(v, 'not');
    if (v === null) return null;
    if (typeof v !== 'number') throw new FormulaTypeError(`"-" needs a number, got ${typeName(v)}`);
    return finite(-v);
  }

  private binary(op: BinaryOp, a: Scalar, b: Scalar): Scalar {
    // Both sides are always evaluated: and/or never short-circuit, so a type error on either
    // side always shows.
    if (op === 'and') {
      const left = this.truth(a, 'and');
      return this.truth(b, 'and') && left;
    }
    if (op === 'or') {
      const left = this.truth(a, 'or');
      return this.truth(b, 'or') || left;
    }
    if (a === null || b === null) return null;
    if (op === '+' || op === '-' || op === '*' || op === '/') {
      if (typeof a !== 'number' || typeof b !== 'number') {
        throw new FormulaTypeError(
          `"${op}" needs two numbers, got ${typeName(a)} and ${typeName(b)}`,
        );
      }
      if (op === '+') return finite(a + b);
      if (op === '-') return finite(a - b);
      if (op === '*') return finite(a * b);
      return b === 0 ? null : finite(a / b);
    }
    const same = typeof a === typeof b;
    if (op === '=' || op === '!=') {
      if (!same) {
        throw new FormulaTypeError(
          `"${op}" compares values of one type, got ${typeName(a)} and ${typeName(b)}`,
        );
      }
      return (a === b) === (op === '=');
    }
    if (typeof a === 'boolean' || !same) {
      throw new FormulaTypeError(
        `"${op}" compares two numbers or two texts, got ${typeName(a)} and ${typeName(b)}`,
      );
    }
    const order =
      typeof a === 'string'
        ? compareText(a, b as string)
        : a < (b as number)
          ? -1
          : a > (b as number)
            ? 1
            : 0;
    if (op === '<') return order < 0;
    if (op === '<=') return order <= 0;
    if (op === '>') return order > 0;
    return order >= 0;
  }

  private textArg(name: string, v: Scalar): string {
    if (typeof v !== 'string') throw new FormulaTypeError(`${name} needs text, got ${typeName(v)}`);
    return v;
  }

  private call(name: string, argNodes: readonly Node[]): Scalar {
    if (name === 'if') {
      // Only the branch taken is evaluated.
      const cond = this.truth(this.run(argNodes[0] as Node), 'the condition of if');
      return this.run((cond ? argNodes[1] : argNodes[2]) as Node);
    }
    const args = argNodes.map((a) => this.run(a));
    if (name === 'coalesce') return args.find((a) => a !== null && a !== '') ?? null;
    if (name === 'concat') return args.map(text).join('');
    if (name === 'is_empty') return args[0] === null || args[0] === '';
    if (args.some((a) => a === null)) return null;
    const first = args[0] as Scalar;
    if (name === 'contains') {
      const haystack = this.textArg('contains', first);
      const needle = this.textArg('contains', args[1] as Scalar);
      return haystack.toLowerCase().includes(needle.toLowerCase());
    }
    if (name === 'lower') return this.textArg('lower', first).toLowerCase();
    if (name === 'upper') return this.textArg('upper', first).toUpperCase();
    if (name === 'len') return Array.from(this.textArg('len', first)).length;
    if (name === 'days_since') {
      const day = typeof first === 'string' ? parseIsoDate(first) : null;
      if (day === null) {
        throw new FormulaTypeError(`days_since needs a YYYY-MM-DD date, got ${typeName(first)}`);
      }
      return this.today - day;
    }
    if (name === 'round') {
      if (typeof first !== 'number')
        throw new FormulaTypeError(`round needs a number, got ${typeName(first)}`);
      const places = args.length > 1 ? args[1] : 0;
      if (
        typeof places !== 'number' ||
        !Number.isInteger(places) ||
        places < 0 ||
        places > MAX_ROUND_PLACES
      ) {
        throw new FormulaTypeError(
          `round takes a whole number of places from 0 to ${MAX_ROUND_PLACES}`,
        );
      }
      return roundNumber(first, places);
    }
    throw new Error(`unknown function ${name}`);
  }
}

/**
 * The formula's value for one entity.
 *
 * `values` maps column keys to their current value (a missing key is null); `system` holds
 * name and domain; `today` is the evaluation date (YYYY-MM-DD) for days_since.
 * Throws FormulaTypeError.
 */
export function evaluate(
  node: Node,
  values: Readonly<Record<string, unknown>>,
  system: SystemValues,
  today: string,
): Scalar {
  const day = parseIsoDate(today);
  if (day === null)
    throw new RangeError(`today must be a YYYY-MM-DD date, got ${JSON.stringify(today)}`);
  return new Evaluator(values, system, day).run(node);
}

/**
 * Why `value` cannot fill a column of `valueType`, or null when it can (or is null).
 *
 * A null result is the caller's no_data, not an error. The message is always
 * `formula returned <type>, column is <value_type>`.
 */
export function checkResult(
  value: Scalar,
  valueType: string,
  allowedValues: readonly string[] | null | undefined,
): string | null {
  if (value === null) return null;
  let ok: boolean;
  if (valueType === 'boolean') ok = typeof value === 'boolean';
  else if (valueType === 'number') ok = typeof value === 'number' && Number.isFinite(value);
  else if (valueType === 'text') ok = typeof value === 'string';
  else if (valueType === 'enum')
    ok = typeof value === 'string' && (allowedValues ?? []).includes(value);
  else if (valueType === 'date') ok = typeof value === 'string' && parseIsoDate(value) !== null;
  else throw new Error(`unknown value_type ${JSON.stringify(valueType)}`);
  return ok ? null : `formula returned ${typeName(value)}, column is ${valueType}`;
}
