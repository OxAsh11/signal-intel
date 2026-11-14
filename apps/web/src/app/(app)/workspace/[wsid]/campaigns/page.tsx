import { createClient } from "@/lib/supabase/server";
import { createCampaign, setCampaignPushMode, setCampaignStatus } from "@/app/actions/activation";

const MOTIONS = ["Geographic", "Vertical", "Event", "Trigger", "Re-engagement"];

type Campaign = {
  id: string;
  name: string | null;
  motion: string | null;
  push_mode: string | null;
  status: string | null;
  bounce_messages_sent: number | null;
  bounce_count: number | null;
};

export default async function CampaignsPage({ params }: { params: Promise<{ wsid: string }> }) {
  const { wsid } = await params;
  const supabase = await createClient();
  if (!supabase) return null;

  const [{ data: campaigns }, { data: briefs }] = await Promise.all([
    supabase
      .from("campaign")
      .select("id, name, motion, push_mode, status, bounce_messages_sent, bounce_count")
      .eq("workspace_id", wsid)
      .order("created_at", { ascending: false }),
    supabase.from("brief").select("id, name").eq("workspace_id", wsid).order("name"),
  ]);

  const rows = (campaigns as Campaign[] | null) ?? [];
  const briefRows = (briefs as { id: string; name: string }[] | null) ?? [];

  return (
    <div>
      <h1 className="si-h1">Campaigns</h1>

      <section>
        <h2 className="si-h2">New campaign</h2>
        {briefRows.length === 0 ? (
          <p>Create a Brief first: a campaign is linked to the Brief it activates.</p>
        ) : (
          <form action={createCampaign.bind(null, wsid)} style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
            <input type="text" name="name" placeholder="Campaign name" aria-label="Campaign name" required />
            <select name="brief" aria-label="Brief" required>
              {briefRows.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.name}
                </option>
              ))}
            </select>
            <select name="motion" aria-label="Motion" required>
              {MOTIONS.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
            <button type="submit">Create</button>
          </form>
        )}
        <p>A new campaign starts with pushes off. Run it in dry run first; it can go live only from dry run.</p>
      </section>

      {rows.length === 0 ? (
        <p>No campaigns yet.</p>
      ) : (
        <table className="si-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Motion</th>
              <th>Push mode</th>
              <th>Status</th>
              <th>Bounce rate</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((c) => {
              const bounceRate =
                c.bounce_messages_sent && c.bounce_messages_sent > 0
                  ? (((c.bounce_count ?? 0) / c.bounce_messages_sent) * 100).toFixed(1) + "%"
                  : "—";
              const archived = c.status === "archived";
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
                  <td>
                    {!archived && c.push_mode !== "dry run" && (
                      <form action={setCampaignPushMode.bind(null, c.id, wsid, "dry run")} style={{ display: "inline" }}>
                        <button type="submit">Dry run</button>
                      </form>
                    )}{" "}
                    {!archived && c.push_mode === "dry run" && (
                      <form action={setCampaignPushMode.bind(null, c.id, wsid, "live")} style={{ display: "inline" }}>
                        <button type="submit">Go live</button>
                      </form>
                    )}{" "}
                    {!archived && c.push_mode !== "off" && (
                      <form action={setCampaignPushMode.bind(null, c.id, wsid, "off")} style={{ display: "inline" }}>
                        <button type="submit">Pushes off</button>
                      </form>
                    )}{" "}
                    {c.status === "active" && (
                      <form action={setCampaignStatus.bind(null, c.id, wsid, "paused")} style={{ display: "inline" }}>
                        <button type="submit">Pause</button>
                      </form>
                    )}
                    {c.status === "paused" && (
                      <form action={setCampaignStatus.bind(null, c.id, wsid, "active")} style={{ display: "inline" }}>
                        <button type="submit">Resume</button>
                      </form>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}
