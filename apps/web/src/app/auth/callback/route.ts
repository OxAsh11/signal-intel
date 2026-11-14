import { NextResponse, type NextRequest } from 'next/server';
import { createClient } from '@/lib/supabase/server';
import { publicOrigin } from '@/lib/site-url';

/** Return URL for OAuth (Google / Microsoft) and email sign-in links. Exchanges the code for a session cookie. */
export async function GET(request: NextRequest) {
  const { searchParams } = request.nextUrl;
  const origin = publicOrigin(request);
  const code = searchParams.get('code');
  const nextParam = searchParams.get('next') ?? '/';
  // Only same-site relative paths: never redirect to another origin.
  const next = nextParam.startsWith('/') && !nextParam.startsWith('//') ? nextParam : '/';

  if (code) {
    const supabase = await createClient();
    const { error } = supabase ? await supabase.auth.exchangeCodeForSession(code) : { error: new Error('not configured') };
    if (!error) return NextResponse.redirect(`${origin}${next}`);
  }
  return NextResponse.redirect(`${origin}/login?error=sign_in_failed`);
}
