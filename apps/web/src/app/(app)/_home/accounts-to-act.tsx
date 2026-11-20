import Link from 'next/link';
import { BandChip, VerdictBadge } from '@signal/ui';
import type { Band, Verdict } from '@signal/contracts';
import { CardError } from './card-error';
import { type HomeCardProps, fmt, loadCard, plural } from './shared';

type Row = {
  account_id: string;
  name: string;
  verdict: Verdict | null;
  band: Band | null;
  why_now: null | { claim_id: string; quote: string; observed_at: string };
  valid_contacts: number;
  next_action: null | { text: string; mode: string };
  href: string;
};
type AccountsToAct = { accounts: Row[]; total: number; href: string };

/** The top five accounts of the "Ready to contact" view, by priority, with the quote behind each. */
export async function AccountsToActCard(props: HomeCardProps) {
  const a = await loadCard<AccountsToAct>('home_accounts_to_act', props);
  if (!a) return <CardError id="home-act" title="Accounts to act on" retry={props.retry} />;
  return (
    <section aria-labelledby="home-act" className="flex min-w-0 flex-col gap-2">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="home-act" className="si-h2">Accounts to act on</h2>
        <Link href={a.href} className="text-[13px]">
          Ready to contact · {fmt(a.total)} →
        </Link>
      </div>
      {a.accounts.length === 0 ? (
        <p className="m-0 text-[13px] text-ink-muted">
          No account is ready to contact yet. Accounts land here once they are Confirmed or Likely with a High or Medium band.
        </p>
      ) : (
        <div className="si-table-box rounded-[8px] border border-line bg-surface">
          <table className="si-table min-w-[720px] text-[13px]">
            <thead>
              <tr>
                <th scope="col">Account</th>
                <th scope="col">Verdict</th>
                <th scope="col">Band</th>
                <th scope="col">Why now</th>
                <th scope="col">Contact</th>
                <th scope="col">Next</th>
              </tr>
            </thead>
            <tbody>
              {a.accounts.map((r) => (
                <tr key={r.account_id} data-account={r.name}>
                  <td>
                    <Link href={r.href} className="font-medium text-ink no-underline hover:underline">{r.name}</Link>
                  </td>
                  <td>{r.verdict ? <VerdictBadge verdict={r.verdict} /> : <span className="si-meta">not judged</span>}</td>
                  <td>{r.band ? <BandChip band={r.band} /> : <span className="si-meta">not scored</span>}</td>
                  <td className="max-w-[40ch]">
                    {r.why_now ? (
                      <q className="font-serif text-[14px] leading-[21px] text-ink">{r.why_now.quote}</q>
                    ) : (
                      <span className="si-meta">no quoted claim yet</span>
                    )}
                  </td>
                  <td>
                    {r.valid_contacts > 0 ? (
                      <Link href={r.href} className="text-positive no-underline hover:underline">
                        ✓ {fmt(r.valid_contacts)} valid {plural(r.valid_contacts, 'contact', 'contacts')}
                      </Link>
                    ) : (
                      <span className="text-ink-muted">No valid contact yet</span>
                    )}
                  </td>
                  <td>{r.next_action ? r.next_action.text : <span className="text-ink-muted">—</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
