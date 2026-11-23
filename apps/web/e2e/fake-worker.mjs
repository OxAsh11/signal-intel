/**
 * Test double for the worker's token-protected contact reveal endpoint.
 *
 * The web app in e2e talks to a real HTTP endpoint with the same contract as the worker
 * (`POST /contacts/reveal`, bearer token, `{ workspace_id, contact_point_id }`). No key, vendor or
 * cryptography is involved: the answer is derived from the contact id so the test can predict it.
 */
import http from 'node:http';

const PORT = Number(process.env.REVEAL_PORT ?? 3101);
const TOKEN = process.env.REVEAL_TOKEN ?? 'e2e-reveal-token';
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

const send = (res, status, body) => {
  const raw = JSON.stringify(body);
  res.writeHead(status, { 'content-type': 'application/json', 'content-length': Buffer.byteLength(raw) });
  res.end(raw);
};

const server = http.createServer((req, res) => {
  if (req.method === 'GET' && req.url === '/healthz') return send(res, 200, { ok: true });
  if (req.url !== '/contacts/reveal') return send(res, 404, { error: 'not found' });
  if (req.method !== 'POST') return send(res, 405, { error: 'POST required' });
  if (req.headers.authorization !== `Bearer ${TOKEN}`) return send(res, 401, { error: 'unauthorised' });
  let body = '';
  req.on('data', (chunk) => {
    body += chunk;
    if (body.length > 4096) req.destroy();
  });
  req.on('end', () => {
    let payload;
    try {
      payload = JSON.parse(body || '{}');
    } catch {
      return send(res, 400, { error: 'invalid request' });
    }
    const ws = payload && payload.workspace_id;
    const cp = payload && payload.contact_point_id;
    if (!UUID.test(String(ws)) || !UUID.test(String(cp))) return send(res, 400, { error: 'invalid request' });
    return send(res, 200, { email: `revealed-${cp.replace(/-/g, '').slice(0, 8)}@people.test` });
  });
});

server.listen(PORT, '127.0.0.1');
