import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { Verdict } from '@signal/contracts';
import { BandChip, Button, DerivedValue, HealthIndicator, VerdictBadge } from '../src';
// @ts-expect-error plain ESM script without types
import { buildTokens } from '../scripts/build-tokens.mjs';

describe('tokens', () => {
  const tokens = JSON.parse(readFileSync(resolve(__dirname, '../../../design/signal-intel-ds/project/tokens.json'), 'utf8'));
  const { css, theme } = buildTokens(tokens);

  it('emits light values on :root and dark values for both dark selectors', () => {
    expect(css).toContain('--signal: #1c4d86;');
    expect(css).toMatch(/@media \(prefers-color-scheme: dark\) \{\n {2}:root:not\(\[data-theme="light"\]\) \{[\s\S]*--signal: #86b4ee;/);
    expect(css).toMatch(/:root\[data-theme="dark"\] \{[\s\S]*--signal: #86b4ee;/);
  });

  it('maps colors to Tailwind without self-referencing variables', () => {
    expect(theme).toContain('--color-signal: var(--signal);');
    expect(theme).not.toMatch(/--(radius|font|shadow)-[\w-]+: var\(--\1/);
  });
});

describe('components', () => {
  afterEach(cleanup);

  it('VerdictBadge renders a label and a glyph for every verdict', () => {
    for (const v of Verdict.options) {
      const { unmount } = render(<VerdictBadge verdict={v} />);
      const el = screen.getByText(v);
      expect(el.className).toContain(`si-verdict-${v.toLowerCase()}`);
      expect(el.querySelector('.si-glyph')?.textContent).toBeTruthy();
      unmount();
    }
  });

  it('BandChip encodes order with pips and shows the exclusion reason', () => {
    const { container, rerender } = render(<BandChip band="Medium" />);
    expect(container.querySelectorAll('.si-pip.on')).toHaveLength(2);
    rerender(<BandChip band="Excluded" reason="Competitor" />);
    expect(container.querySelectorAll('.si-pip')).toHaveLength(0);
    expect(container.textContent).toContain('Competitor');
  });

  it('DerivedValue has no input and opens its explanation', () => {
    const { container } = render(
      <DerivedValue label="Verdict" explanation={<p>Rests on 2 claims</p>}>
        <VerdictBadge verdict="Confirmed" />
      </DerivedValue>,
    );
    expect(container.querySelector('input, textarea, select')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /explain/i }));
    expect(screen.getByRole('dialog').textContent).toContain('Rests on 2 claims');
  });

  it('Button shows the cost of a spending action', () => {
    render(<Button cost="≈ $42">Expand to Germany</Button>);
    expect(screen.getByRole('button').textContent).toBe('Expand to Germany≈ $42');
  });

  it('a silent pipeline reads as Silent, not healthy', () => {
    render(<HealthIndicator name="Webhooks" status="silent" />);
    expect(screen.getByText('Silent')).toBeTruthy();
  });
});
