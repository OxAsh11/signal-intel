import { createClient } from "@/lib/supabase/server";

export default async function CampaignsPage({ params }: { params: Promise<{ wsid: string }> }) {
  const { wsid } = await params;
  const supabase = await createClient();
  if (!supabase) return null;

  const { data: campaigns } = await supabase
    .from("campaign")
    .select("id, name, motion, push_mode, status, bounce_messages_sent, bounce_count")
    .eq("workspace_id", wsid)
    .order("created_at", { ascending: false });

  type Campaign = {
    id: string;
    name: string | null;
    motion: string | null;
    push_mode: string | null;
    status: string | null;
    bounce_messages_sent: number | null;
    bounce_count: number | null;
  };

  const rows = (campaigns as Campaign[] | null) ?? [];

  if (rows.length === 0) {
    return (
      <div>
        <h1>Campaigns</h1>
        <p>No campaigns yet.</p>
      </div>
    );
  }

  return (
    <div>
      <h1>Campaigns</h1>
      <table>
        <thead>
          <tr>
            <th>Name</th>
            <th>Motion</th>
            <th>Push mode</th>
            <th>Status</th>
            <th>Bounce rate</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((c) => {
            const bounceRate =
              c.bounce_messages_sent && c.bounce_messages_sent > 0
                ? (((c.bounce_count ?? 0) / c.bounce_messages_sent) * 100).toFixed(1) + "%"
                : "—";
            return (
              <tr key={c.id}>
                <td>{c.name ?? "—"}</td>
                <td>
                  <span data-motion={c.motion ?? undefined}>{c.motion ?? "—"}</span>
                </td>
                <td>
                  <span data-push-mode={c.push_mode ?? undefined}>{c.push_mode ?? "—"}</span>
                </td>
                <td>{c.status ?? "—"}</td>
                <td>{bounceRate}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
