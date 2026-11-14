import { createClient } from "@/lib/supabase/server";
import { AccountsFilter } from "./accounts-filter";

export default async function AccountsPage({ params }: { params: Promise<{ wsid: string }> }) {
  const { wsid } = await params;
  const supabase = await createClient();
  if (!supabase) return null;

  const { data: rawAccounts } = await supabase
    .from("account")
    .select(`
      id,
      stage,
      entity:entity_id(id, display_name, verdict(value, computed_at)),
      score:score(band, fit, evidence, scale, timing, priority, computed_at),
      contact_point:contact_point(id)
    `)
    .eq("workspace_id", wsid)
    .order("id");

  // Normalize PostgREST array joins → single objects before passing to client component
  const accounts = (rawAccounts ?? []).map((a) => {
    const entity = Array.isArray(a.entity) ? (a.entity[0] ?? null) : a.entity;
    const scoreArr = Array.isArray(a.score) ? a.score : (a.score ? [a.score] : []);
    const verdictArr = entity && "verdict" in entity
      ? (Array.isArray((entity as { verdict?: unknown }).verdict)
          ? ((entity as { verdict: { value: string; computed_at: string | null }[] }).verdict)
          : [])
      : [];
    return {
      id: a.id,
      stage: a.stage,
      entity: entity ? { id: (entity as { id: string }).id, display_name: (entity as { display_name: string }).display_name } : null,
      score: scoreArr[0] ?? null,
      verdict: verdictArr[0] ?? null,
      contact_point: Array.isArray(a.contact_point) ? a.contact_point : [],
    };
  });

  return (
    <div>
      <h1 className="si-h1">Accounts</h1>
      {accounts.length === 0 ? (
        <p>No accounts yet</p>
      ) : (
        <AccountsFilter accounts={accounts} />
      )}
    </div>
  );
}
