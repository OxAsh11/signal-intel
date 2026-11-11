import { createClient } from "@/lib/supabase/server";

export default async function MeetingsPage({ params }: { params: Promise<{ wsid: string }> }) {
  const { wsid } = await params;
  const supabase = await createClient();
  if (!supabase) return null;

  const { data: rawMeetings } = await supabase
    .from("meeting")
    .select("id, scheduled_at, held_at, proof_status, account:account_id(id, entity:entity_id(display_name))")
    .eq("workspace_id", wsid)
    .order("scheduled_at", { ascending: false })
    .limit(50);

  const { data: rawTasks } = await supabase
    .from("task")
    .select("id, title, due_date, origin, status, account:account_id(id, entity:entity_id(display_name))")
    .eq("workspace_id", wsid)
    .eq("status", "open")
    .order("due_date");

  type EntityRow = { display_name: string | null };
  type AccountWithEntity = { id: string; entity: EntityRow | EntityRow[] | null };
  type RawMeeting = {
    id: string;
    scheduled_at: string | null;
    held_at: string | null;
    proof_status: string | null;
    account: AccountWithEntity | AccountWithEntity[] | null;
  };
  type RawTask = {
    id: string;
    title: string | null;
    due_date: string | null;
    origin: string | null;
    status: string | null;
    account: AccountWithEntity | AccountWithEntity[] | null;
  };

  function normalizeAccount(raw: AccountWithEntity | AccountWithEntity[] | null): { id: string; displayName: string | null } | null {
    const acct: AccountWithEntity | null = Array.isArray(raw) ? (raw[0] ?? null) : raw;
    if (!acct) return null;
    const entity: EntityRow | null = Array.isArray(acct.entity) ? (acct.entity[0] ?? null) : (acct.entity ?? null);
    return { id: acct.id, displayName: entity?.display_name ?? null };
  }

  const meetings = ((rawMeetings as RawMeeting[] | null) ?? []).map((m) => ({
    ...m,
    accountNorm: normalizeAccount(m.account),
  }));

  const tasks = ((rawTasks as RawTask[] | null) ?? []).map((t) => ({
    ...t,
    accountNorm: normalizeAccount(t.account),
  }));

  return (
    <div>
      <h1>Meetings &amp; Tasks</h1>

      <h2>Meetings</h2>
      {meetings.length === 0 ? (
        <p>No meetings yet.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Scheduled at</th>
              <th>Account</th>
              <th>Held at</th>
              <th>Proof status</th>
            </tr>
          </thead>
          <tbody>
            {meetings.map((m) => (
              <tr key={m.id}>
                <td>{m.scheduled_at ? new Date(m.scheduled_at).toLocaleString() : "—"}</td>
                <td>{m.accountNorm?.displayName ?? m.accountNorm?.id ?? "—"}</td>
                <td>{m.held_at ? new Date(m.held_at).toLocaleString() : "—"}</td>
                <td>
                  <span data-proof-status={m.proof_status ?? undefined}>{m.proof_status ?? "—"}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h2>Tasks</h2>
      {tasks.length === 0 ? (
        <p>No open tasks.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Title</th>
              <th>Account</th>
              <th>Due date</th>
              <th>Origin</th>
            </tr>
          </thead>
          <tbody>
            {tasks.map((t) => (
              <tr key={t.id}>
                <td>{t.title ?? "—"}</td>
                <td>{t.accountNorm?.displayName ?? t.accountNorm?.id ?? "—"}</td>
                <td>{t.due_date ? new Date(t.due_date).toLocaleDateString() : "—"}</td>
                <td>
                  <span data-origin={t.origin ?? undefined}>{t.origin ?? "—"}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
