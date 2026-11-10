import { createClient } from "@/lib/supabase/server";
import { VerdictBadge, StageBadge } from "@signal/ui";
import { ScoreBreakdown } from "@signal/ui";
import type { Verdict, Stage } from "@signal/contracts";

export default async function AccountDetailPage({
  params,
}: {
  params: Promise<{ wsid: string; aid: string }>;
}) {
  const { wsid, aid } = await params;
  const supabase = await createClient();
  if (!supabase) return null;

  // First fetch account to get entity_id
  const { data: account } = await supabase
    .from("account")
    .select("id, stage, entity:entity_id(id, display_name, kind)")
    .eq("id", aid)
    .eq("workspace_id", wsid)
    .single();

  if (!account) return null;

  // entity may be an array (Supabase join returns array)
  const entityRow = Array.isArray(account.entity) ? account.entity[0] : account.entity;
  const entityId = (entityRow as { id?: string } | null)?.id ?? null;

  // Fetch everything else in parallel
  const [
    { data: scores },
    { data: verdicts },
    { data: labels },
    { data: card },
    { data: researchDocs },
    { data: contactPoints },
    { data: stageEvents },
    { data: activities },
  ] = await Promise.all([
    supabase
      .from("score")
      .select(
        "band, fit, evidence, scale, timing, reachability, priority, readiness, gates, explanation, computed_at, brief_version_id"
      )
      .eq("account_id", aid)
      .order("computed_at", { ascending: false })
      .limit(1),

    entityId
      ? supabase
          .from("verdict")
          .select("value, quotes, computed_at, brief_version_id")
          .eq("entity_id", entityId)
          .order("computed_at", { ascending: false })
          .limit(1)
      : Promise.resolve({ data: null }),

    supabase
      .from("label")
      .select("id, kind, value, computed_at")
      .eq("account_id", aid)
      .order("kind"),

    supabase
      .from("account_card")
      .select("id, content, computed_at, brief_version_id")
      .eq("account_id", aid)
      .order("computed_at", { ascending: false })
      .limit(1),

    supabase
      .from("research_doc")
      .select("id, version, published_at, content")
      .eq("account_id", aid)
      .order("published_at", { ascending: false }),

    supabase
      .from("contact_point")
      .select("id, type, status, provider, verified_at")
      .eq("account_id", aid),

    supabase
      .from("account_stage_event")
      .select("id, to_stage, occurred_at, activity_id")
      .eq("account_id", aid)
      .order("occurred_at", { ascending: false }),

    supabase
      .from("activity")
      .select("id, kind, occurred_at, entity:entity_id(display_name)")
      .eq("account_id", aid)
      .order("occurred_at", { ascending: false })
      .limit(50),
  ]);

  const latestScore = Array.isArray(scores) && scores.length > 0 ? scores[0] : null;
  const latestVerdict = Array.isArray(verdicts) && verdicts.length > 0 ? verdicts[0] : null;
  const latestCard = Array.isArray(card) && card.length > 0 ? card[0] : null;

  const entityName =
    (entityRow as { display_name?: string } | null)?.display_name ?? aid;

  return (
    <div>
      {/* 1. Header */}
      <h1>{entityName}</h1>

      {/* 2. Verdict */}
      <section>
        <h2>Verdict</h2>
        {latestVerdict ? (
          <VerdictBadge verdict={latestVerdict.value as Verdict} />
        ) : (
          <p>No verdict yet</p>
        )}
        {(latestVerdict?.quotes as string[] | null | undefined)?.map((q: string, i: number) => (
          <blockquote key={i}>{q}</blockquote>
        ))}
      </section>

      {/* 3. Score */}
      <section>
        <h2>Score</h2>
        {latestScore ? (
          <ScoreBreakdown
            priority={latestScore.priority as number}
            band={latestScore.band as import("@signal/contracts").Band}
            dimensions={[
              { name: "Fit", value: latestScore.fit as number },
              { name: "Evidence", value: latestScore.evidence as number },
              { name: "Scale", value: latestScore.scale as number },
              { name: "Timing", value: latestScore.timing as number },
            ]}
            readiness={latestScore.readiness as number}
            gates={
              latestScore.gates
                ? (latestScore.gates as { name: string; hit: boolean }[])
                : undefined
            }
          />
        ) : (
          <p>No score yet</p>
        )}
      </section>

      {/* 4. Account card */}
      <section>
        <h2>Account Card</h2>
        {latestCard ? (
          <ul>
            {((latestCard.content as { bullets?: string[] })?.bullets ?? []).map(
              (b: string, i: number) => (
                <li key={i}>{b}</li>
              )
            )}
          </ul>
        ) : (
          <p>No account card yet</p>
        )}
      </section>

      {/* 5. Labels */}
      <section>
        <h2>Labels</h2>
        {labels?.length ? (
          labels.map((l) => (
            <span key={l.id} style={{ marginRight: 8 }}>
              {l.kind}: {l.value}
            </span>
          ))
        ) : (
          <p>No labels</p>
        )}
      </section>

      {/* 6. Contact points */}
      <section>
        <h2>Contacts</h2>
        {contactPoints?.length ? (
          <table>
            <thead>
              <tr>
                <th>Type</th>
                <th>Status</th>
                <th>Provider</th>
              </tr>
            </thead>
            <tbody>
              {contactPoints.map((cp) => (
                <tr key={cp.id}>
                  <td>{cp.type}</td>
                  <td>{cp.status}</td>
                  <td>{cp.provider ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p>No contact points</p>
        )}
      </section>

      {/* 7. Activity timeline */}
      <section>
        <h2>Activity</h2>
        {activities?.length ? (
          <ul>
            {activities.map((a) => (
              <li key={a.id}>
                {a.occurred_at ? new Date(a.occurred_at).toLocaleString() : "—"} · {a.kind}
                {" · "}
                {(Array.isArray(a.entity)
                  ? a.entity[0]?.display_name
                  : (a.entity as { display_name?: string } | null)?.display_name) ?? ""}
              </li>
            ))}
          </ul>
        ) : (
          <p>No activity yet</p>
        )}
      </section>

      {/* 8. Research documents */}
      <section>
        <h2>Research</h2>
        {researchDocs?.length ? (
          <ul>
            {researchDocs.map((rd) => (
              <li key={rd.id}>
                v{rd.version} ·{" "}
                {rd.published_at ? new Date(rd.published_at).toLocaleString() : "—"}
              </li>
            ))}
          </ul>
        ) : (
          <p>No research documents</p>
        )}
      </section>

      {/* 9. Stage */}
      <section>
        <h2>Stage</h2>
        <StageBadge stage={account.stage as Stage} />
        {stageEvents?.length ? (
          <ul>
            {stageEvents.map((e) => (
              <li key={e.id}>
                {e.to_stage} ·{" "}
                {e.occurred_at ? new Date(e.occurred_at).toLocaleString() : "—"}
              </li>
            ))}
          </ul>
        ) : null}
      </section>

      {/* 10. Tasks */}
      <section>
        <h2>Tasks</h2>
        <p>Tasks are not yet wired (Phase 3).</p>
      </section>
    </div>
  );
}
