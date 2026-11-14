import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { Verdict } from '@signal/contracts';
import { BandChip, Button, Card, DerivedValue, FamilyTag, HealthIndicator, MetricTile, VerdictBadge } from '../src';
// @ts-expect-error plain ESM script without types
import { buildTokens } from '../scripts/build-tokens.mjs';

describe('tokens', () => {
  const tokens = JSON.parse(readFileSync(resolve(__dirname, '../../../design/signal-intel-ds/project/tokens.json'), 'utf8'));
  const { css, theme } = buildTokens(tokens);

  it('emits one light theme that never switches to dark', () => {
    expect(css).toContain('--signal: #39729a;');
    expect(css).toContain('--paper: #ffffff;');
    expect(css).not.toContain('prefers-color-scheme');
    expect(css).not.toContain('data-theme');
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

describe('light system', () => {
  afterEach(cleanup);

  it('a primary button carries the right-pointing arrow; others do not', () => {
    const { container } = render(<><Button variant="primary">Go</Button><Button>Back</Button></>);
    const [primary, secondary] = container.querySelectorAll('button');
    expect(primary!.querySelector('svg.si-btn-arrow')?.getAttribute('aria-hidden')).toBe('true');
    expect(secondary!.querySelector('svg')).toBeNull();
  });

  it('cards take a colour family and a kind', () => {
    const { container } = render(
      <>
        <Card family="mint">Content</Card>
        <Card family="grass" kind="feature">Feature</Card>
        <Card>Plain</Card>
      </>,
    );
    const [content, feature, plain] = container.querySelectorAll('section');
    expect(content!.className).toContain('si-card si-gcard si-fam-mint');
    expect(feature!.className).toContain('si-fcard si-fam-grass');
    expect(plain!.className).toBe('si-card');
  });

  it('a family tag shows its words in the family colours', () => {
    render(<FamilyTag family="mustard">Needs you</FamilyTag>);
    expect(screen.getByText('Needs you').className).toBe('si-ftag si-fam-mustard');
  });

  it('a metric tile can carry a family', () => {
    const { container } = render(<MetricTile label="Spend" value="$7" href="/x" version="def v1" family="lava" />);
    expect(container.querySelector('a')!.className).toBe('si-metric si-fam-lava');
  });
});
