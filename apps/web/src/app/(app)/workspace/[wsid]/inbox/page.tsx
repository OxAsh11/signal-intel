import { createClient } from "@/lib/supabase/server";
import { classifyReply } from "@/app/actions/activation";

const REPLY_CLASSES = ["Interested", "Referral", "Not now", "Not interested", "Out of office", "Unsubscribe"];

/** Unclassified replies received before this instant have waited over two working days (M10). */
async function overdueCutoff(): Promise<string> {
  return new Date(Date.now() - 2 * 24 * 60 * 60 * 1000).toISOString();
}

export default async function InboxPage({ params }: { params: Promise<{ wsid: string }> }) {
  const { wsid } = await params;
  const supabase = await createClient();
  if (!supabase) return null;
  const overdueBefore = await overdueCutoff();

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
    const overdue = !r.payload?.reply_class && r.occurred_at < overdueBefore;
    return { ...r, person, account, overdue };
  });

  if (replies.length === 0) {
    return (
      <div>
        <h1 className="si-h1">Inbox</h1>
        <p>No replies received yet.</p>
      </div>
    );
  }

  return (
    <div>
      <h1 className="si-h1">Inbox</h1>
      <table className="si-table">
        <thead>
          <tr>
            <th>Received</th>
            <th>Account</th>
            <th>Person</th>
            <th>Reply class</th>
            <th>Classify</th>
          </tr>
        </thead>
        <tbody>
          {replies.map((r) => {
            const replyClass = r.payload?.reply_class as string | undefined;
            const source = r.payload?.reply_class_source as string | undefined;
            return (
              <tr key={r.id}>
                <td>
                  {new Date(r.occurred_at).toLocaleString()}
                  {r.overdue && <span> (overdue)</span>}
                </td>
                <td>{r.account?.id ?? "—"}</td>
                <td>{r.person?.display_name ?? "—"}</td>
                <td>
                  {replyClass ?? "—"}
                  {replyClass && source ? ` (${source})` : ""}
                </td>
                <td>
                  <form action={classifyReply.bind(null, r.id, wsid)} style={{ display: "inline-flex", gap: "4px" }}>
                    <select name="reply_class" defaultValue={replyClass ?? ""} aria-label="Reply class" required>
                      <option value="" disabled>
                        Choose…
                      </option>
                      {REPLY_CLASSES.map((c) => (
                        <option key={c} value={c}>
                          {c}
                        </option>
                      ))}
                    </select>
                    <button type="submit">Save</button>
                  </form>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
