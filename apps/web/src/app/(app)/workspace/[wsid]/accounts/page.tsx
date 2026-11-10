import { createClient } from "@/lib/supabase/server";
import { AccountsFilter } from "./accounts-filter";

export default async function AccountsPage({ params }: { params: Promise<{ wsid: string }> }) {
  const { wsid } = await params;
  const supabase = await createClient();
  if (!supabase) return null;

  const { data: accounts } = await supabase
    .from("account")
    .select(`
      id,
      stage,
      entity:entity_id(id, display_name),
      score:score(band, fit, evidence, scale, timing, priority, computed_at),
      verdict:verdict(value, computed_at),
      contact_point:contact_point(id)
    `)
    .eq("workspace_id", wsid)
    .order("id");

  return (
    <div>
      <h1>Accounts</h1>
      {!accounts || accounts.length === 0 ? (
        <p>No accounts yet</p>
      ) : (
        // @ts-expect-error Supabase inferred join types use arrays for FK relations; runtime values are correct
        <AccountsFilter accounts={accounts} />
      )}
    </div>
  );
}
