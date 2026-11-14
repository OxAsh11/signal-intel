import type { NextRequest } from 'next/server';

/**
 * The public origin to send people back to. Behind Railway's proxy the server sees its own bind
 * address (http://0.0.0.0:8080), so `request.nextUrl.origin` is wrong in production.
 * Order: the configured site URL (trusted), then the proxy's forwarded host, then the request.
 */
export function publicOrigin(request: NextRequest): string {
  const configured = process.env.NEXT_PUBLIC_SITE_URL?.trim().replace(/\/+$/, '');
  if (configured) return configured;
  const host = request.headers.get('x-forwarded-host');
  if (host) {
    const proto = request.headers.get('x-forwarded-proto') ?? 'https';
    return `${proto.split(',')[0].trim()}://${host.split(',')[0].trim()}`;
  }
  return request.nextUrl.origin;
}
