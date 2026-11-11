import { Button } from '@signal/ui';
import { revokeApiToken } from '@/app/actions/activation';
import { atLeast, getOrgContext } from '@/lib/org';
import { createClient } from '@/lib/supabase/server';
import { TokenForm } from './token-form';

type TokenRow = { id: string; name: string; scope: string; last_used_at: string | null; expires_at: string | null; revoked_at: string | null };
type IntegrationRow = { id: string; name: string; category: string; access_mode: string; enabled: boolean };
type SubscriptionRow = { id: string; url: string; event_types: string[]; enabled: boolean; fail_count: number };

const when = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }) : '—');

function Section({ title, hint, children }: { title: string; hint: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-3 rounded-md border border-line bg-surface p-4">
      <div className="flex flex-col gap-0.5">
        <h2 className="m-0 text-[15px] leading-[22px] font-semibold">{title}</h2>
        <p className="m-0 max-w-[70ch] text-[13px] text-ink-muted">{hint}</p>
      </div>
      {children}
    </section>
  );
}

/** API and MCP tokens, connected integrations and webhook subscriptions for the current workspace (M13). */
export async function ApiAccess() {
  const [ctx, supabase] = await Promise.all([getOrgContext(), createClient()]);
  if (!ctx?.workspace || !supabase) return null;
  const ws = ctx.workspace.id;
  const isAdmin = atLeast(ctx.orgRole, 'Admin');
  const canCreate = isAdmin || atLeast(ctx.workspaceRole, 'Editor');

  const [tokens, integrations, subscriptions] = await Promise.all([
    supabase.from('api_token').select('id, name, scope, last_used_at, expires_at, revoked_at').eq('workspace_id', ws).order('created_at', { ascending: false }),
    supabase.from('integration').select('id, name, category, access_mode, enabled').eq('workspace_id', ws).order('created_at'),
    isAdmin
      ? supabase.from('webhook_subscription').select('id, url, event_types, enabled, fail_count').eq('workspace_id', ws).order('created_at')
      : Promise.resolve({ data: [] }),
  ]);
  const tokenRows = (tokens.data ?? []) as TokenRow[];
  const integrationRows = (integrations.data ?? []) as IntegrationRow[];
  const subscriptionRows = (subscriptions.data ?? []) as SubscriptionRow[];

  return (
    <>
      <Section
        title="API and MCP tokens"
        hint="A token is scoped to this workspace and works for the REST API (/v1) and the MCP server. Admins can create any scope; Editors can create Read tokens."
      >
        {canCreate ? <TokenForm workspaceId={ws} scopes={isAdmin ? ['Read', 'Write', 'Admin'] : ['Read']} /> : null}
        {tokenRows.length === 0 ? (
          <p className="m-0 text-[13px] text-ink-muted">No tokens yet.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Scope</th>
                <th>Last used</th>
                <th>Expires</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {tokenRows.map((t) => (
                <tr key={t.id}>
                  <td>{t.name}</td>
                  <td>{t.scope}</td>
                  <td>{when(t.last_used_at)}</td>
                  <td>{t.expires_at ? when(t.expires_at) : 'Never'}</td>
                  <td>
                    {t.revoked_at ? (
                      'Revoked'
                    ) : (
                      <form action={revokeApiToken.bind(null, t.id)} style={{ display: 'inline' }}>
                        <Button type="submit" variant="quiet" size="sm">
                          Revoke
                        </Button>
                      </form>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>

      <Section
        title="Integrations"
        hint="Connected systems and their access mode. A new connection starts in Read. Connect one with an Admin token: POST /v1/integrations returns the signing secret for its inbound events."
      >
        {integrationRows.length === 0 ? (
          <p className="m-0 text-[13px] text-ink-muted">Not set up yet.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Category</th>
                <th>Access mode</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {integrationRows.map((i) => (
                <tr key={i.id}>
                  <td>{i.name}</td>
                  <td>{i.category}</td>
                  <td>{i.access_mode}</td>
                  <td>{i.enabled ? 'Enabled' : 'Disabled'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>

      {isAdmin ? (
        <Section
          title="Webhook subscriptions"
          hint="Events are sent signed to each URL and retried with backoff; a subscription that keeps failing is disabled. Create one with an Admin token: POST /v1/webhook-subscriptions."
        >
          {subscriptionRows.length === 0 ? (
            <p className="m-0 text-[13px] text-ink-muted">Not set up yet.</p>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>URL</th>
                  <th>Events</th>
                  <th>Status</th>
                  <th>Recent failures</th>
                </tr>
              </thead>
              <tbody>
                {subscriptionRows.map((s) => (
                  <tr key={s.id}>
                    <td>{s.url}</td>
                    <td>{s.event_types.join(', ')}</td>
                    <td>{s.enabled ? 'Enabled' : 'Disabled'}</td>
                    <td>{s.fail_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Section>
      ) : null}
    </>
  );
}
