import { createClient } from "@/lib/supabase/server";

export default async function ReportsPage({ params }: { params: Promise<{ wsid: string }> }) {
  const { wsid } = await params;
  const supabase = await createClient();
  if (!supabase) return null;

  const { data: briefRow } = await supabase
    .from("brief")
    .select("id")
    .eq("workspace_id", wsid)
    .order("created_at", { ascending: false })
    .limit(1)
    .maybeSingle();
  const briefId = briefRow?.id ?? null;

  const metricKeys = [
    "accounts_discovered",
    "accounts_verified",
    "accounts_contacted",
    "genuine_replies_people",
    "companies_met",
  ] as const;
  const metrics: Record<string, number | null> = {};

  if (briefId) {
    for (const key of metricKeys) {
      const { data } = await supabase.rpc("compute_metric", {
        p_workspace_id: wsid,
        p_brief_id: briefId,
        p_metric_key: key,
        p_filters: {},
      });
      metrics[key] = (data as { value?: number } | null)?.value ?? null;
    }
  }

  const { data: stageRows } = await supabase
    .from("account")
    .select("stage")
    .eq("workspace_id", wsid);
  const stageCounts: Record<string, number> = {};
  for (const row of stageRows ?? []) {
    const stage = (row as { stage: string | null }).stage;
    if (stage) {
      stageCounts[stage] = (stageCounts[stage] ?? 0) + 1;
    }
  }

  return (
    <div>
      <h1>Reports</h1>

      {!briefId ? (
        <p>No brief published yet — metrics unavailable.</p>
      ) : (
        <section>
          <h2>Key metrics</h2>
          <div style={{ display: "flex", gap: "1rem", flexWrap: "wrap" }}>
            {metricKeys.map((key) => (
              <div className="metric-tile" key={key}>
                <span className="metric-label">{key}</span>
                <span className="metric-value">{metrics[key] ?? "—"}</span>
              </div>
            ))}
          </div>
        </section>
      )}

      <section>
        <h2>Stage funnel</h2>
        {Object.keys(stageCounts).length === 0 ? (
          <p>No accounts yet.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Stage</th>
                <th>Count</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(stageCounts).map(([stage, count]) => (
                <tr key={stage}>
                  <td>{stage}</td>
                  <td>{count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
