// The editor helpers: what a formula reads, what is wrong with it, and renaming a column it reads.
import { describe, expect, it } from 'vitest';
import { formulaProblem, formulaReads, MAX_INPUTS, renameFormulaRef } from './index';

describe('formulaReads', () => {
  it('lists the columns read, sorted and distinct, without system values', () => {
    expect(formulaReads('site_count > 0 and drone_programme and site_count < 9 and @name != ""')).toEqual([
      'drone_programme',
      'site_count',
    ]);
  });

  it('is null when the formula does not parse', () => {
    expect(formulaReads('site_count >')).toBeNull();
    expect(formulaReads(null)).toBeNull();
  });
});

describe('formulaProblem', () => {
  const columns = ['drone_programme', 'site_count', 'flag'];

  it('accepts a formula reading other columns of the Brief', () => {
    expect(formulaProblem('drone_programme and site_count > 0', 'flag', columns, [])).toBeNull();
  });

  it('asks for a formula when there is none', () => {
    expect(formulaProblem('  ', 'flag', columns, [])).toBe('Write the formula this column computes.');
    expect(formulaProblem(undefined, 'flag', columns, [])).toBe('Write the formula this column computes.');
  });

  it('names a syntax error and its 1-based character', () => {
    expect(formulaProblem('site_count >', 'flag', columns, [])).toMatch(/^Formula: .+ \(at character 13\)$/);
  });

  it('refuses a formula reading its own column', () => {
    expect(formulaProblem('flag', 'flag', columns, [])).toBe('A formula cannot read its own column.');
  });

  it('names an unknown column and a legacy key', () => {
    expect(formulaProblem('work_emial', 'flag', columns, [])).toBe(
      'Formula reads "work_emial", which is not a column of this Brief',
    );
    expect(formulaProblem('vertical', 'flag', columns, ['vertical'])).toBe(
      'Formula reads "vertical", which has no column spec yet; give it one first.',
    );
  });

  it('needs at least one and at most MAX_INPUTS columns', () => {
    expect(formulaProblem('@name', 'flag', columns, [])).toBe('A formula reads at least one column of this Brief.');
    const many = Array.from({ length: MAX_INPUTS + 1 }, (_, i) => `c${i}x`);
    expect(formulaProblem(`concat(${many.join(', ')})`, 'flag', [...many, 'flag'], [])).toBe(
      `A formula reads at most ${MAX_INPUTS} columns.`,
    );
  });
});

describe('renameFormulaRef', () => {
  it('renames a column reference', () => {
    expect(renameFormulaRef('site_count > 0', 'site_count', 'sites')).toBe('sites > 0');
  });

  it('returns the formula unchanged when it does not read the column', () => {
    expect(renameFormulaRef('drone_programme', 'site_count', 'sites')).toBe('drone_programme');
  });

  it('replaces whole identifiers only', () => {
    expect(renameFormulaRef('site_count_2 + site_count', 'site_count', 'sites')).toBe('site_count_2 + sites');
    expect(renameFormulaRef('x_site_count + site_count', 'site_count', 'sites')).toBe('x_site_count + sites');
  });

  it('never touches text, escapes included', () => {
    expect(renameFormulaRef('"site_count" = site_count', 'site_count', 'sites')).toBe('"site_count" = sites');
    expect(renameFormulaRef('concat("a \\"site_count\\" b", site_count)', 'site_count', 'sites')).toBe(
      'concat("a \\"site_count\\" b", sites)',
    );
    expect(renameFormulaRef('concat("a\\\\", site_count)', 'site_count', 'sites')).toBe('concat("a\\\\", sites)');
  });

  it('never touches @name or @domain', () => {
    expect(renameFormulaRef('@name = name', 'name', 'title')).toBe('@name = title');
    expect(renameFormulaRef('coalesce(domain, @domain)', 'domain', 'site')).toBe('coalesce(site, @domain)');
  });

  it('never touches a function name, even with space before its bracket', () => {
    expect(renameFormulaRef('lower(lower)', 'lower', 'low')).toBe('lower(low)');
    expect(renameFormulaRef('lower (lower)', 'lower', 'low')).toBe('lower (low)');
  });

  it('never touches a keyword, and refuses to rename a column to one', () => {
    expect(renameFormulaRef('flag and other', 'and', 'x_and')).toBe('flag and other');
    expect(renameFormulaRef('site_count > 0', 'site_count', 'and')).toBeNull();
    expect(renameFormulaRef('site_count > 0', 'site_count', 'null')).toBeNull();
  });

  it('refuses a rename it cannot confirm', () => {
    // Unparsable before: the change cannot be checked.
    expect(renameFormulaRef('site_count >', 'site_count', 'sites')).toBeNull();
    // The new key is already read: two columns would merge into one.
    expect(renameFormulaRef('site_count + sites', 'site_count', 'sites')).toBeNull();
  });
});
