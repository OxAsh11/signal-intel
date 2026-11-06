import { connection } from 'next/server';

/** Railway health check. `connection()` keeps it a request-time response, never prerendered. */
export async function GET() {
  await connection();
  return Response.json({ status: 'ok', service: 'web', time: new Date().toISOString() });
}
