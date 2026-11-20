// The formula language (D5): every parity case in ../cases.json, plus the TypeScript API.
// Mirrors packages/signal_core/tests/test_formula.py.
import { describe, expect, it } from 'vitest';
import caseFile from '../cases.json';
import {
  FormulaSyntaxError,
  FormulaTypeError,
  checkResult,
  evaluate,
  parse,
  references,
  type Scalar,
} from './index';

type Case = {
  name: string;
  expr: string;
  values: Record<string, unknown>;
  system: { name?: string | null; domain?: string | null };
  today: string;
  expect: Scalar | { error: 'syntax' | 'type' };
};

const CASES = (caseFile as unknown as { cases: Case[] }).cases;
const TODAY = '2026-11-19';

/** Numbers compare by value (2 equals 2.0); a boolean never equals a number. */
function same(actual: unknown, expected: unknown): boolean {
  return typeof actual === typeof expected && actual === expected;
}

function run(c: Case): Scalar {
  return evaluate(parse(c.expr), c.values, c.system, c.today);
}

describe('parity cases', () => {
  it('the case file is rich', () => {
    expect(CASES.length).toBeGreaterThanOrEqual(60);
    expect(new Set(CASES.map((c) => c.name)).size).toBe(CASES.length);
  });

  it.each(CASES.map((c) => [c.name, c] as const))('%s', (_name, c) => {
    const want = c.expect;
    if (want !== null && typeof want === 'object') {
      const error = want.error === 'syntax' ? FormulaSyntaxError : FormulaTypeError;
      expect(() => run(c)).toThrow(error);
      return;
    }
    const actual = run(c);
    expect(
      same(actual, want),
      `${JSON.stringify(c.expr)}: got ${String(actual)}, want ${String(want)}`,
    ).toBe(true);
  });
});

describe('API', () => {
  it('integers stay integers', () => {
    const v = evaluate(parse('1 + 2 * 3'), {}, {}, TODAY);
    expect(v).toBe(7);
    expect(Number.isInteger(v)).toBe(true);
  });

  it('parse returns the tree', () => {
    expect(parse('site_count + 1')).toEqual({
      kind: 'binary',
      op: '+',
      left: { kind: 'ref', key: 'site_count' },
      right: { kind: 'num', value: 1 },
    });
  });

  it('parse returns an immutable tree', () => {
    const node = parse('site_count + 1');
    expect(Object.isFrozen(node)).toBe(true);
    expect(() => {
      (node as { op?: string }).op = '-';
    }).toThrow(TypeError);
  });

  it('references are sorted and distinct', () => {
    const node = parse('concat(zeta, alpha, if(alpha > 1, beta, zeta), @name, "gamma")');
    expect(references(node)).toEqual(['alpha', 'beta', 'zeta']);
    expect(references(parse('1 + 2'))).toEqual([]);
  });

  it.each([
    ['1 + @x', 4],
    ['foo(1)', 0],
    ['"abc', 0],
    ['1 2', 2],
    ['', 0],
    ['(1 + 2', 6],
    ['a'.repeat(501), 500],
    ['('.repeat(33) + '1' + ')'.repeat(33), 32],
    ['1' + ' + 1'.repeat(100), 400],
    ['"a\\nb"', 2],
    // Positions count code points, as Python does: the emoji is one character, not two.
    ['"😀" #', 4],
  ] as const)('syntax error position: %j', (expr, position) => {
    let caught: unknown;
    try {
      parse(expr);
    } catch (e) {
      caught = e;
    }
    expect(caught).toBeInstanceOf(FormulaSyntaxError);
    expect((caught as FormulaSyntaxError).position).toBe(position);
    expect((caught as FormulaSyntaxError).message).not.toBe('');
  });

  it('the length limit counts code points', () => {
    // 249 emoji inside quotes plus len(...) is under 500 code points but over 500 UTF-16 units.
    expect(evaluate(parse(`len("${'😀'.repeat(249)}")`), {}, {}, TODAY)).toBe(249);
  });

  it.each([[[1, 2]], [{ a: 1 }], [[]]])('a non-scalar input is a type error: %j', (bad) => {
    for (const expr of ['xs', 'xs + 1', 'concat(xs)', 'is_empty(xs)', 'coalesce(xs)', 'xs = 1']) {
      expect(() => evaluate(parse(expr), { xs: bad }, {}, TODAY)).toThrow(FormulaTypeError);
      expect(() => evaluate(parse(expr), { xs: bad }, {}, TODAY)).toThrow(/column "xs"/);
    }
  });

  it('a key inherited from Object.prototype is not a value', () => {
    expect(evaluate(parse('constructor'), {}, {}, TODAY)).toBeNull();
    expect(evaluate(parse('constructor = 1'), { constructor: undefined }, {}, TODAY)).toBeNull();
  });

  it('errors are Errors', () => {
    expect(new FormulaSyntaxError('x', 1)).toBeInstanceOf(Error);
    expect(new FormulaTypeError('x')).toBeInstanceOf(Error);
    expect(new FormulaSyntaxError('x', 1).name).toBe('FormulaSyntaxError');
    expect(new FormulaTypeError('x').name).toBe('FormulaTypeError');
  });

  it.each([
    [true, 'boolean', null, null],
    [1, 'boolean', null, 'formula returned number, column is boolean'],
    [3, 'number', null, null],
    [2.5, 'number', null, null],
    [Infinity, 'number', null, 'formula returned number, column is number'],
    [NaN, 'number', null, 'formula returned number, column is number'],
    [true, 'number', null, 'formula returned boolean, column is number'],
    ['x', 'text', null, null],
    [4, 'text', null, 'formula returned number, column is text'],
    ['energy', 'enum', ['energy', 'mining'], null],
    ['farming', 'enum', ['energy', 'mining'], 'formula returned text, column is enum'],
    [false, 'enum', ['energy', 'mining'], 'formula returned boolean, column is enum'],
    ['2026-11-19', 'date', null, null],
    ['2026-02-30', 'date', null, 'formula returned text, column is date'],
    ['19/11/2026', 'date', null, 'formula returned text, column is date'],
    [20261119, 'date', null, 'formula returned number, column is date'],
    [null, 'number', null, null],
  ] as const)('checkResult(%j, %s, %j)', (value, valueType, allowed, problem) => {
    expect(checkResult(value, valueType, allowed)).toBe(problem);
  });

  it('checkResult refuses an unknown value type', () => {
    expect(() => checkResult(1, 'money', null)).toThrow(/unknown value_type/);
  });
});
