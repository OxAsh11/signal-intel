#!/usr/bin/env node
// Builds packages/ui/src/tokens.css and theme.css from the design system's tokens.json.
// tokens.json (design/signal-intel-ds/project/tokens.json) is the single source of truth.
// Usage: node scripts/build-tokens.mjs [--check]
import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const repo = resolve(here, '..', '..', '..');
const SOURCE = resolve(repo, 'design/signal-intel-ds/project/tokens.json');
const HEADER = '/* GENERATED from design/signal-intel-ds/project/tokens.json by packages/ui/scripts/build-tokens.mjs. Do not edit. */';

export function buildTokens(tokens) {
  const [first, ...others] = tokens.color.themes.map((t) => t.id);
  const value = (tok, theme) => (typeof tok.value === 'string' ? tok.value : (tok.value[theme] ?? tok.value[first]));
  const resolveAlias = (v) => v.replace(/^\{(.+)\}$/, 'var(--$1)');
  const themed = [...tokens.color.tokens, ...(tokens.shadow?.tokens ?? [])];

  const decl = (theme) => themed.map((t) => `  --${t.name}: ${resolveAlias(value(t, theme))};`).join('\n');
  const statics = [
    ...(tokens.spacing?.tokens ?? []),
    ...(tokens.radius?.tokens ?? []),
  ].map((t) => `  --${t.name}: ${t.value};`);
  const fonts = Object.entries(tokens.type.families).map(([k, stack]) => `  --font-${k}: ${stack};`);

  let css = `${HEADER}\n:root {\n${decl(first)}\n${statics.join('\n')}\n${fonts.join('\n')}\n  color-scheme: light;\n}\n`;
  for (const theme of others) {
    css += `\n@media (prefers-color-scheme: ${theme}) {\n  :root:not([data-theme="${first}"]) {\n${decl(theme).replace(/^/gm, '  ')}\n    color-scheme: ${theme};\n  }\n}\n`;
    css += `\n:root[data-theme="${theme}"] {\n${decl(theme)}\n  color-scheme: ${theme};\n}\n`;
  }

  // Tailwind v4 theme: utilities like bg-surface, text-ink-muted, border-line, rounded-md, font-mono.
  // Colors are theme-aware, so they reference the runtime variables (`inline` avoids self-reference).
  // Radii and font stacks don't change per theme, so they are literal values under the same names.
  // Shadows change per theme and share Tailwind's --shadow-* names, so they are used via var() only.
  const colorMap = tokens.color.tokens.map((t) => `  --color-${t.name}: var(--${t.name});`);
  const radiusMap = (tokens.radius?.tokens ?? []).map((t) => `  --${t.name}: ${t.value};`);
  const fontMap = Object.entries(tokens.type.families).map(([k, stack]) => `  --font-${k}: ${stack};`);
  const theme = `${HEADER}\n@theme inline {\n${colorMap.join('\n')}\n}\n\n@theme {\n${[...radiusMap, ...fontMap].join('\n')}\n}\n`;
  return { css, theme };
}

const isMain = process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isMain) {
  const check = process.argv.includes('--check');
  const { css, theme } = buildTokens(JSON.parse(readFileSync(SOURCE, 'utf8')));
  let stale = 0;
  for (const [file, content] of [[resolve(here, '../src/tokens.css'), css], [resolve(here, '../src/theme.css'), theme]]) {
    const current = existsSync(file) ? readFileSync(file, 'utf8') : null;
    if (current === content) continue;
    if (check) { console.error(`stale: ${file}`); stale++; } else { writeFileSync(file, content); console.log(`wrote ${file}`); }
  }
  if (check && stale) { console.error('Design tokens changed. Run `pnpm codegen`.'); process.exit(1); }
  if (check) console.log('tokens up to date');
}
