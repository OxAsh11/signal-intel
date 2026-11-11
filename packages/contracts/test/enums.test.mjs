import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const { enums } = JSON.parse(readFileSync(new URL('../schema/enums.json', import.meta.url), 'utf8'));

test('enums match PRD Appendix A', () => {
  assert.deepEqual(enums.Verdict.values, ['Confirmed', 'Likely', 'Unverified', 'Contradicted']);
  assert.deepEqual(enums.Band.values, ['High', 'Medium', 'Low', 'Excluded']);
  assert.equal(enums.Stage.values.length, 9);
  assert.equal(enums.ActivityKind.values.length, 15);
  // Appendix A.4's twenty, plus two reviewed additions that queue work from the interface:
  // brief.intake_requested (0015) and radar.fetch_queued (0017).
  assert.equal(enums.EventType.values.length, 22);
  assert.deepEqual(enums.GateOutcome.values, ['applied', 'review queue', 'digest']);
});

test('every enum has a unique Postgres name and unique values', () => {
  const pg = Object.values(enums).map((d) => d.pg);
  assert.equal(new Set(pg).size, pg.length);
  for (const [name, d] of Object.entries(enums)) {
    assert.equal(new Set(d.values).size, d.values.length, `${name} has duplicate values`);
  }
});
