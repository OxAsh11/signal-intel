/**
 * Test double for the worker's two small, token-protected endpoints.
 *
 * The web app talks to real HTTP endpoints with the same contracts as the worker
 * (`POST /contacts/reveal`, `GET /providers/status`, bearer token). No key, vendor or cryptography
 * is involved: the reveal answer is derived from the contact id, and provider status is a fixture
 * a test can replace through /_fake/provider-status.
 */
import http from 'node:http';

const PORT = Number(process.env.REVEAL_PORT ?? 3101);
const TOKEN = process.env.REVEAL_TOKEN ?? 'e2e-reveal-token';
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

const KINDS = {
  leadmagic: 'email_finder',
  findymail: 'email_finder',
  findymail_verify: 'email_verifier',
  prospeo: 'email_finder',
  pdl_company: 'company',
  pdl_person: 'person_search',
};
const PRICES = {
  leadmagic: '0.012', findymail: '0.015', findymail_verify: '0.004',
  prospeo: '0.010', pdl_company: '0.100', pdl_person: '0.280',
};
const defaults = () =>
  Object.keys(KINDS).map((key) => ({
    key,
    kind: KINDS[key],
    configured: false,
    last_error: null,
    last_error_at: null,
    default_price_usd: PRICES[key],
  }));

let providers = defaults();

const send = (res, status, body) => {
  const raw = JSON.stringify(body);
  res.writeHead(status, { 'content-type': 'application/json', 'content-length': Buffer.byteLength(raw) });
  res.end(raw);
};

const server = http.createServer((req, res) => {
  const url = req.url || '';
  const auth = req.headers.authorization === `Bearer ${TOKEN}`;

  // Test-only control: replace the status fixture before loading Settings. Never used by the app.
  if (url === '/_fake/provider-status' && req.method === 'POST') {
    let body = '';
    req.on('data', (chunk) => { body += chunk; });
    req.on('end', () => {
      try {
        const rows = JSON.parse(body || '[]');
        if (!Array.isArray(rows)) return send(res, 400, { error: 'invalid request' });
        providers = rows;
        res.writeHead(204);
        return res.end();
      } catch {
        return send(res, 400, { error: 'invalid request' });
      }
    });
    return;
  }

  if (url === '/healthz' && req.method === 'GET') return send(res, 200, { ok: true });
  if (url === '/providers/status') {
    if (req.method !== 'GET') return send(res, 405, { error: 'GET required' });
    if (!auth) return send(res, 401, { error: 'unauthorised' });
    return send(res, 200, { providers });
  }
  if (url !== '/contacts/reveal') return send(res, 404, { error: 'not found' });
  if (req.method !== 'POST') return send(res, 405, { error: 'POST required' });
  if (!auth) return send(res, 401, { error: 'unauthorised' });
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
