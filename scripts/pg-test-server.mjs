#!/usr/bin/env node
// Serves the migrated in-memory Postgres over TCP so the Python services can be tested against
// the real schema without Docker. Usage: node scripts/pg-test-server.mjs <port>
// Prints "READY <port>" once it accepts connections and exits when stdin closes.
import { PGLiteSocketServer } from '@electric-sql/pglite-socket';
import { openMigratedDb } from './lib/pglite-schema.mjs';

const port = Number(process.argv[2] ?? 54329);
const db = await openMigratedDb();
const server = new PGLiteSocketServer({ db, port, host: '127.0.0.1' });
await server.start();
console.log(`READY ${port}`);

async function shutdown() {
  await server.stop().catch(() => {});
  await db.close().catch(() => {});
  process.exit(0);
}
process.stdin.on('end', shutdown);
process.stdin.on('close', shutdown);
process.on('SIGTERM', shutdown);
process.on('SIGINT', shutdown);
process.stdin.resume();
