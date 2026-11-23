'use server';

import { revalidatePath, refresh } from 'next/cache';
import { createClient } from '@/lib/supabase/server';
import { atLeast, friendlyError, getOrgContext } from '@/lib/org';

/**
 * People actions. Every database write goes through an audited SQL function that re-checks the
 * member's role. The one value SQL cannot return is a decrypted email: the Fernet key lives only
 * in the worker, so the panel asks the worker's token-protected reveal endpoint after the
 * `person_email` RPC has checked Editor+ and named the contact point.
 */
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export type PeopleResult<T = undefined> = { ok: true; data: T } | { ok: false; error: string; code?: string; firstN?: number };

export type FindEstimate = {
  count: number;
  per_person_usd: number;
  cost_usd: number;
  remaining_usd: number | null;
  within_budget: number;
  max_per_request: number;
};

export type PersonEmail = {
  contact_point_id: string;
  masked: string;
  status: string | null;
  verified_at: string | null;
  provider: string | null;
  reveal: string;
};

const fail = (message: string | undefined, code?: string): { ok: false; error: string; code?: string } => ({
  ok: false,
  error: friendlyError(message),
  code,
});
const NOT_CONFIGURED = { ok: false as const, error: 'Supabase is not configured.' };

function changed(ws?: string) {
  refresh();
  revalidatePath('/people');
  revalidatePath('/review');
  if (ws) revalidatePath(`/workspace/${ws}/accounts`);
}

/** What finding up to `limit` people costs. Reads only (Viewer+). */
export async function estimateFindPeople(
  ws: string,
  account: string,
  limit = 5,
): Promise<PeopleResult<FindEstimate>> {
  if (!UUID.test(ws) || !UUID.test(account) || !Number.isInteger(limit) || limit < 1 || limit > 10) {
    return { ok: false, error: 'That request was not understood.' };
  }
  const supabase = await createClient();
  if (!supabase) return NOT_CONFIGURED;
  const { data, error } = await supabase.rpc('estimate_find_people', {
    p_ws: ws,
    p_account: account,
    p_limit: limit,
  });
  if (error || !data) return fail(error?.message, error?.code);
  const estimate = data as Record<string, unknown>;
  const num = (v: unknown) => Number(v ?? 0);
  return {
    ok: true,
    data: {
      count: num(estimate.count),
      per_person_usd: num(estimate.per_person_usd),
      cost_usd: num(estimate.cost_usd),
      remaining_usd: estimate.remaining_usd == null ? null : num(estimate.remaining_usd),
      within_budget: num(estimate.within_budget),
      max_per_request: num(estimate.max_per_request),
    },
  };
}

/**
 * Queues one locked `people.find` job, after the cost was shown. A 55000 refusal names the
 * largest N that fits so the button can offer "Find the first N".
 */
export async function requestFindPeople(
  ws: string,
  account: string,
  limit = 5,
  brief: string | null = null,
): Promise<PeopleResult<{ queued: number; cost_usd: number }>> {
  if (!UUID.test(ws) || !UUID.test(account) || (brief && !UUID.test(brief))) {
    return { ok: false, error: 'That request was not understood.' };
  }
  if (!Number.isInteger(limit) || limit < 1 || limit > 10) {
    return { ok: false, error: 'Find between 1 and 10 people.' };
  }
  const supabase = await createClient();
  if (!supabase) return NOT_CONFIGURED;
  const { data, error } = await supabase.rpc('request_find_people', {
    p_ws: ws,
    p_account: account,
    p_limit: limit,
    p_brief: brief,
  });
  if (error) {
    const first = /find the first (\d+) within budget/.exec(error.message);
    return {
      ...fail(error.message, error.code),
      firstN: error.code === '55000' && first ? Number(first[1]) : undefined,
    };
  }
  changed(ws);
  const result = (data ?? {}) as { queued?: number; cost_usd?: number };
  return { ok: true, data: { queued: Number(result.queued ?? 0), cost_usd: Number(result.cost_usd ?? 0) } };
}

/** Dismiss the Review card: the person was correctly left unlinked. */
export async function dismissUnlinkedPerson(itemId: string, ws: string): Promise<PeopleResult> {
  if (!UUID.test(itemId) || !UUID.test(ws)) return { ok: false, error: 'That item was not found.' };
  const supabase = await createClient();
  if (!supabase) return NOT_CONFIGURED;
  const { error } = await supabase.rpc('resolve_review_item', { p_id: itemId, p_action: 'skip' });
  if (error) return fail(error.message, error.code);
  changed(ws);
  return { ok: true, data: undefined };
}

/** Link the Review card's person to the account the reviewer chose. */
export async function linkPersonFromReview(
  itemId: string,
  accountId: string,
  ws: string,
): Promise<PeopleResult> {
  if (!UUID.test(itemId) || !UUID.test(accountId) || !UUID.test(ws)) {
    return { ok: false, error: 'Choose an account.' };
  }
  const supabase = await createClient();
  if (!supabase) return NOT_CONFIGURED;
  // The four-argument form has no defaults, so every parameter must be named (the SQL overload
  // with defaults has no p_account and PostgREST cannot mix the two).
  const { error } = await supabase.rpc('resolve_review_item', {
    p_id: itemId,
    p_action: 'link',
    p_new_entity_name: null,
    p_account: accountId,
  });
  if (error) return fail(error.message, error.code);
  changed(ws);
  return { ok: true, data: undefined };
}

/** Viewer-safe mask plus the contact id; the full value never comes through Supabase. */
export async function loadPersonEmail(
  ws: string,
  person: string,
): Promise<PeopleResult<PersonEmail>> {
  if (!UUID.test(ws) || !UUID.test(person)) return { ok: false, error: 'That person was not found.' };
  const supabase = await createClient();
  if (!supabase) return NOT_CONFIGURED;
  const ctx = await getOrgContext();
  if (!atLeast(ctx?.workspaceRole, 'Editor')) return { ok: false, error: 'Only Editors can reveal an email.' };
  const { data, error } = await supabase.rpc('person_email', { p_ws: ws, p_person: person });
  if (error || !data) return fail(error?.message, error?.code);
  const email = data as Partial<PersonEmail>;
  if (!email.contact_point_id) return { ok: true, data: { ...(email as PersonEmail), masked: '—' } };
  return {
    ok: true,
    data: {
      contact_point_id: email.contact_point_id,
      masked: email.masked ?? '—',
      status: email.status ?? null,
      verified_at: email.verified_at ?? null,
      provider: email.provider ?? null,
      reveal: 'worker',
    },
  };
}

/**
 * Ask the worker for the full email. The database RPC above has already checked Editor+ and the
 * workspace; the worker checks the shared token and the contact id. A missing URL is an honest
 * "not set up yet", never a crash and never a plaintext fallback in the web app.
 */
export async function revealPersonEmail(
  ws: string,
  person: string,
  contactId: string,
): Promise<PeopleResult<{ email: string; masked: string }>> {
  if (![ws, person, contactId].every((v) => UUID.test(v))) {
    return { ok: false, error: 'That email was not found.' };
  }
  const ctx = await getOrgContext();
  if (!atLeast(ctx?.workspaceRole, 'Editor')) return { ok: false, error: 'Only Editors can reveal an email.' };
  const supabase = await createClient();
  if (!supabase) return NOT_CONFIGURED;
  const { data, error } = await supabase.rpc('person_email', { p_ws: ws, p_person: person });
  if (error || !data) return fail(error?.message, error?.code);
  const allowed = data as { contact_point_id?: string; masked?: string };
  if (allowed.contact_point_id !== contactId) return { ok: false, error: 'That email was not found.' };

  const url = process.env.WORKER_REVEAL_URL?.trim().replace(/\/+$/, '');
  const token = process.env.WORKER_STATUS_TOKEN?.trim();
  if (!url || !token) return { ok: false, error: 'Email reveal is not set up yet.' };
  try {
    const res = await fetch(`${url}/contacts/reveal`, {
      method: 'POST',
      headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` },
      body: JSON.stringify({ workspace_id: ws, contact_point_id: contactId }),
      cache: 'no-store',
      signal: AbortSignal.timeout(5000),
    });
    if (!res.ok) return { ok: false, error: 'The email could not be revealed right now.' };
    const value = (await res.json()) as { email?: unknown };
    if (typeof value.email !== 'string') return { ok: false, error: 'The email could not be revealed right now.' };
    return { ok: true, data: { email: value.email, masked: allowed.masked ?? '—' } };
  } catch {
    return { ok: false, error: 'The email could not be revealed right now.' };
  }
}

// ── Form-action wrappers (used by the Review cards) ──────────────────────────

/** `<form action>` shape for the Review Link button: the chosen account comes from the form. */
export async function linkPersonFromReviewForm(itemId: string, ws: string, form: FormData): Promise<void> {
  const accountId = String(form.get('account') ?? '').trim();
  const res = await linkPersonFromReview(itemId, accountId, ws);
  if (!res.ok) throw new Error(res.error);
}

/** `<form action>` shape for the Review Dismiss button. */
export async function dismissUnlinkedPersonForm(itemId: string, ws: string): Promise<void> {
  const res = await dismissUnlinkedPerson(itemId, ws);
  if (!res.ok) throw new Error(res.error);
}
