import { readFileSync, rmSync } from 'node:fs';
import { PID_FILE } from './global-setup';

/** Stop the fake worker started in global setup. */
export default async function globalTeardown(): Promise<void> {
  let pid = 0;
  try {
    pid = Number(readFileSync(PID_FILE, 'utf8').trim());
  } catch {
    return;
  }
  if (!pid) return;
  try {
    process.kill(process.platform === 'win32' ? pid : -pid, 'SIGTERM');
  } catch {
    // Already stopped.
  }
  rmSync(PID_FILE, { force: true });
}
