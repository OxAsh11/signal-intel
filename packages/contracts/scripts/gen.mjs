#!/usr/bin/env node
// Generates zod (TS) and StrEnum (Python) modules from schema/enums.json.
// Usage: node scripts/gen.mjs [--check]   (--check exits 1 if outputs are stale)
import { readFileSync, writeFileSync, mkdirSync, existsSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, '..');
const repo = resolve(root, '..', '..');
const check = process.argv.includes('--check');
const { enums } = JSON.parse(readFileSync(resolve(root, 'schema/enums.json'), 'utf8'));
const HEADER = 'GENERATED from packages/contracts/schema/enums.json by scripts/gen.mjs. Do not edit.';

export function memberName(value) {
  return value
    .replace(/[^A-Za-z0-9]+/g, '_')
    .replace(/^_|_$/g, '')
    .toUpperCase();
}

function ts() {
  let out = `// ${HEADER}\nimport { z } from 'zod';\n`;
  for (const [name, def] of Object.entries(enums)) {
    out += `\nexport const ${name} = z.enum(${JSON.stringify(def.values)});\n`;
    out += `export type ${name} = z.infer<typeof ${name}>;\n`;
  }
  out += `\nexport const PG_ENUM_NAMES = ${JSON.stringify(
    Object.fromEntries(Object.entries(enums).map(([n, d]) => [n, d.pg])),
    null,
    2,
  )} as const;\n`;
  return out;
}

function py() {
  let out = `# ${HEADER}\n"""Canonical value lists (PRD Appendix A)."""\n\nfrom enum import StrEnum\n`;
  for (const [name, def] of Object.entries(enums)) {
    out += `\n\nclass ${name}(StrEnum):\n`;
    if (def.note) out += `    """${def.note}"""\n\n`;
    for (const v of def.values) out += `    ${memberName(v)} = ${JSON.stringify(v)}\n`;
  }
  out += `\n\nPG_ENUM_NAMES: dict[str, str] = {\n`;
  for (const [name, def] of Object.entries(enums)) out += `    ${JSON.stringify(name)}: ${JSON.stringify(def.pg)},\n`;
  out += `}\n`;
  return out;
}

const targets = [
  [resolve(root, 'gen/ts/enums.ts'), ts()],
  [resolve(repo, 'packages/signal_core/src/signal_core/contracts/enums.py'), py()],
];

let stale = 0;
for (const [file, content] of targets) {
  const current = existsSync(file) ? readFileSync(file, 'utf8') : null;
  if (current === content) continue;
  if (check) {
    console.error(`stale: ${file}`);
    stale++;
  } else {
    mkdirSync(dirname(file), { recursive: true });
    writeFileSync(file, content);
    console.log(`wrote ${file}`);
  }
}
if (check && stale) {
  console.error('Generated contracts are out of date. Run `pnpm codegen`.');
  process.exit(1);
}
if (check) console.log('contracts up to date');
