import { createClient } from "@/lib/supabase/server";

export default async function InboxPage({ params }: { params: Promise<{ wsid: string }> }) {
  const { wsid } = await params;
  const supabase = await createClient();
  if (!supabase) return null;

  const { data: rawReplies } = await supabase
    .from("activity")
    .select("id, kind, occurred_at, payload, person:person_entity_id(id, display_name), account:account_id(id)")
    .eq("workspace_id", wsid)
    .eq("kind", "reply_received")
    .order("occurred_at", { ascending: false })
    .limit(100);

  type PersonRow = { id: string; display_name: string | null };
  type AccountRow = { id: string };
  type RawReply = {
    id: string;
    kind: string;
    occurred_at: string;
    payload: Record<string, unknown> | null;
    person: PersonRow | PersonRow[] | null;
    account: AccountRow | AccountRow[] | null;
  };

  const replies = (rawReplies as RawReply[] | null ?? []).map((r) => {
    const person: PersonRow | null = Array.isArray(r.person) ? (r.person[0] ?? null) : r.person;
    const account: AccountRow | null = Array.isArray(r.account) ? (r.account[0] ?? null) : r.account;
    const twodays = 2 * 24 * 60 * 60 * 1000;
    const overdue = !r.payload?.reply_class && (Date.now() - new Date(r.occurred_at).getTime()) > twodays;
    return { ...r, person, account, overdue };
  });

  if (replies.length === 0) {
    return (
      <div>
        <h1>Inbox</h1>
        <p>No replies received yet.</p>
      </div>
    );
  }

  return (
    <div>
      <h1>Inbox</h1>
      <table>
        <thead>
          <tr>
            <th>Received</th>
            <th>Account</th>
            <th>Person</th>
            <th>Reply class</th>
          </tr>
        </thead>
        <tbody>
          {replies.map((r) => (
            <tr key={r.id}>
              <td>
                {new Date(r.occurred_at).toLocaleString()}
                {r.overdue && <span> (overdue)</span>}
              </td>
              <td>{r.account?.id ?? "—"}</td>
              <td>{r.person?.display_name ?? "—"}</td>
              <td>{(r.payload?.reply_class as string | undefined) ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
