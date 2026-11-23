import { spawn } from 'node:child_process';
import { writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

export const FAKE_PORT = 3101;
export const FAKE_TOKEN = 'e2e-reveal-token';
export const PID_FILE = path.join(tmpdir(), 'signal-intel-e2e-fake-worker.pid');

/** Start the fake reveal worker before the browser tests run. */
export default async function globalSetup(): Promise<void> {
  const script = path.resolve(__dirname, 'fake-worker.mjs');
  const child = spawn(process.execPath, [script], {
    env: { ...process.env, REVEAL_PORT: String(FAKE_PORT), REVEAL_TOKEN: FAKE_TOKEN },
    detached: process.platform !== 'win32',
    stdio: 'ignore',
  });
  child.unref();
  writeFileSync(PID_FILE, String(child.pid));
  for (let n = 0; n < 50; n += 1) {
    try {
      const res = await fetch(`http://127.0.0.1:${FAKE_PORT}/healthz`);
      if (res.ok) return;
    } catch {
      // The child has not bound its port yet.
    }
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error('the fake reveal worker did not start');
}
