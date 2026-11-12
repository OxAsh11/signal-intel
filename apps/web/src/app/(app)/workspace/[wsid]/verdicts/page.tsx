import { createClient } from "@/lib/supabase/server";

export default async function VerdictsPage({ params }: { params: Promise<{ wsid: string }> }) {
  const { wsid } = await params;
  const supabase = await createClient();
  if (!supabase) return null;

  const { data: verdicts } = await supabase
    .from("verdict")
    .select("id, entity_id, value, computed_at, brief_version_id, entity:entity_id(display_name)")
    .eq("workspace_id", wsid)
    .order("computed_at", { ascending: false });

  if (!verdicts || verdicts.length === 0) {
    return (
      <div>
        <h1>Verdicts</h1>
        <p>No verdicts yet — extraction and identity resolution must complete first.</p>
      </div>
    );
  }

  return (
    <div>
      <h1>Verdicts</h1>
      <table>
        <thead>
          <tr>
            <th>Entity</th>
            <th>Verdict</th>
            <th>Computed at</th>
          </tr>
        </thead>
        <tbody>
          {verdicts.map((v: { id: string; entity_id: string; value: string; computed_at: string | null; brief_version_id: string; entity: { display_name: string } | { display_name: string }[] | null }) => (
            <tr key={v.id}>
              <td>{(Array.isArray(v.entity) ? v.entity[0]?.display_name : v.entity?.display_name) ?? v.entity_id}</td>
              <td>
                <span data-verdict={v.value}>{v.value}</span>
              </td>
              <td>{v.computed_at ? new Date(v.computed_at).toLocaleString() : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
