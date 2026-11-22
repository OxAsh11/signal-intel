-- Migration 0042: Home and sidebar RPCs (P4).
--   * Home is made of cards; each card reads one RPC, so one failing card cannot break the page.
--     Every public.home_* RPC is (p_ws uuid, p_brief uuid default null) returns jsonb, SECURITY
--     DEFINER, Viewer+ (42501 otherwise, also for an unknown workspace).
--   * The lens: p_brief null means all Briefs. A p_brief that is not a Brief of p_ws, or is
--     Archived, is treated as null (never an error).
--   * Where a card counts accounts, it goes through P3's public.view_entities, with the P3 preset
--     filters read from public.list_views; there "all Briefs" means the current versions of the
--     Pilot and Active Briefs, as P3 defines it. For radars (the Sources card, the header's
--     sources running, new accounts by radar) "all Briefs" means the Briefs with a published
--     version that are Pilot, Active or Paused (app.home_lens_radar_briefs), so a Draft's radars
--     never show. Elsewhere (Brief versions, verdict changes) "all Briefs" means every Brief of the
--     workspace that is not Archived.
--   * Counts are never null (0). Hrefs are relative paths on the P4 routes; links to screens
--     that only exist per workspace today (inbox, meetings and tasks, the account page) use
--     /workspace/<ws>/....
--   * public.sidebar_counts and public.budget_meter feed the shell (Review count, budget meter).

-- ── 1. Indexes for the Home reads ────────────────────────────────────────────
-- What changed reads verdict history from verdict.changed events: the events of the last 7 days,
-- then, for each, the one event before it of the same entity under the same Brief.
create index outbox_verdict_changed_ws_idx on public.outbox (workspace_id, created_at)
  where event = 'verdict.changed';
create index outbox_verdict_changed_agg_idx on public.outbox (aggregate_id, created_at)
  where event = 'verdict.changed';
comment on table public.outbox is
  'Transactional outbox (S4). public.home_changes reads verdict history (from → to) from the '
  'verdict.changed rows: any future pruning must keep them, or move that history to its own table first.';
-- A Brief version's column changes, read from the publish audit (predicate.fill_changed /
-- predicate.values_added carry after.brief_version_id).
create index audit_log_predicate_version_idx on public.audit_log ((after ->> 'brief_version_id'))
  where target_type = 'predicate';
-- New accounts per radar: account → mention → source_record.radar_id.
create index mention_entity_idx on public.mention (entity_id) where entity_id is not null;

-- ── 2. Helpers ───────────────────────────────────────────────────────────────
-- The access check and the lens: raises 42501 unless the caller is a Viewer+ of p_ws; returns
-- p_brief when it is a non-Archived Brief of p_ws, else null.
create function app.home_lens(p_ws uuid, p_brief uuid) returns uuid
  language plpgsql stable security definer set search_path = ''
as $$
begin
  if not exists (select 1 from public.workspace w where w.id = p_ws) or not app.has_workspace_role(p_ws, 'Viewer') then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  return (select b.id from public.brief b
           where b.id = p_brief and b.workspace_id = p_ws and b.state <> 'Archived');
end $$;

-- The Briefs a lens covers: the given one, else every non-Archived Brief of the workspace.
create function app.home_lens_briefs(p_ws uuid, p_brief uuid) returns uuid[]
  language sql stable security definer set search_path = ''
as $$
  select case when p_brief is not null then array[p_brief]
              else coalesce((select array_agg(b.id) from public.brief b
                              where b.workspace_id = p_ws and b.state <> 'Archived'), '{}') end
$$;

-- The Briefs whose radars a lens covers: the given one, else the Briefs with a published version
-- that are Pilot, Active or Paused (p_live: only Pilot and Active, for "sources running" beside
-- the header's count of Pilot and Active Briefs).
create function app.home_lens_radar_briefs(p_ws uuid, p_brief uuid, p_live boolean default false) returns uuid[]
  language sql stable security definer set search_path = ''
as $$
  select case when p_brief is not null then array[p_brief]
              else coalesce((select array_agg(b.id) from public.brief b
                              where b.workspace_id = p_ws and b.current_version_id is not null
                                and b.state = any(case when p_live then array['Pilot', 'Active']
                                                       else array['Pilot', 'Active', 'Paused'] end::public.brief_state[])), '{}') end
$$;

-- The Brief versions verdicts and scores are read under (as P3's view_brief_ctx): the given
-- Brief's current version, else the current versions of the Pilot and Active Briefs.
create function app.home_lens_versions(p_ws uuid, p_brief uuid) returns uuid[]
  language sql stable security definer set search_path = ''
as $$
  select coalesce(array_agg(b.current_version_id) filter (where b.current_version_id is not null), '{}')
    from public.brief b
   where b.workspace_id = p_ws
     and (case when p_brief is not null then b.id = p_brief else b.state in ('Pilot', 'Active') end)
$$;

-- How many accounts a filter matches (P3 view_entities), 0 when none.
create function app.home_count(p_ws uuid, p_brief uuid, p_filter jsonb) returns int
  language sql stable security definer set search_path = ''
as $$
  select coalesce(max(v.total), 0)::int from public.view_entities(p_ws, p_brief, p_filter, null, 1, 0) v
$$;

-- A P3 preset's filter, as public.list_views defines it.
create function app.home_preset(p_ws uuid, p_brief uuid, p_preset text) returns jsonb
  language sql stable security definer set search_path = ''
as $$
  select v.filter from public.list_views(p_ws, p_brief) v where v.id is null and v.preset = p_preset limit 1
$$;

-- {claim_id, quote, observed_at} of the newest quoted claim among p_cited (not retracted), else
-- (when p_fallback) the newest quoted active claim of the entity; null when there is none.
-- Derived (formula) claims are never evidence: their source record's connector is 'derived:%'.
create function app.home_quote(p_entity uuid, p_cited uuid[], p_fallback boolean) returns jsonb
  language sql stable security definer set search_path = ''
as $$
  select jsonb_build_object('claim_id', c.id, 'quote', c.quote, 'observed_at', c.observed_at)
    from public.claim c
    join public.source_record sr on sr.id = c.source_record_id
   where c.entity_id = p_entity
     and c.quote is not null and btrim(c.quote) <> ''
     and sr.connector_id not like 'derived:%'
     and ((c.id = any(coalesce(p_cited, '{}')) and c.status <> 'retracted') or (p_fallback and c.status = 'active'))
   order by (c.id = any(coalesce(p_cited, '{}'))) desc, c.observed_at desc, c.id desc
   limit 1
$$;

-- {spent_usd, cap_usd, pct}: this month's workspace spend (app.period_spend, the sum the budget
-- guard checks) against the workspace-wide monthly cap. cap_usd and pct are null without a cap;
-- pct has one decimal.
create function app.home_spend(p_ws uuid) returns jsonb
  language sql stable security definer set search_path = ''
as $$
  select jsonb_build_object('spent_usd', s.spent, 'cap_usd', c.cap,
                            'pct', case when c.cap is null then null else round(s.spent * 100 / c.cap, 1) end)
    from (select app.period_spend(p_ws, null, 'month') as spent) s
    left join (select b.cap_usd as cap from public.budget b
                where b.workspace_id = p_ws and b.brief_id is null and b.period = 'month') c on true
$$;

-- How many cells of the given columns are stale now, over the workspace's company accounts (the
-- accounts P3's views list). Set-based: one count per column through P3's app.filter_run_sql.
create function app.home_stale_cells(p_ws uuid, p_cols uuid[]) returns int
  language plpgsql stable security definer set search_path = ''
as $$
declare
  v_col uuid;
  v_n   int;
  v_sum int := 0;
begin
  for v_col in select distinct c from unnest(coalesce(p_cols, '{}')) c loop
    execute format(
      'select count(*)::int from public.account a join public.entity e on e.id = a.entity_id'
      ' where a.workspace_id = $1 and e.entity_type = ''company'' and e.status = ''active'' and %s',
      app.filter_run_sql(format('select p.id as pid, p.fill_method as fill from public.predicate p where p.id = %L::uuid', v_col),
                         array['stale']))
      into v_n using p_ws;
    v_sum := v_sum + v_n;
  end loop;
  return v_sum;
end $$;

-- Pending decisions on /review: pending review_item plus pending verdict_review.
create function app.home_review_count(p_ws uuid) returns int
  language sql stable security definer set search_path = ''
as $$
  select ((select count(*) from public.review_item r where r.workspace_id = p_ws and r.status = 'pending')
        + (select count(*) from public.verdict_review v where v.workspace_id = p_ws and v.status = 'pending'))::int
$$;

-- ── 3. Header ────────────────────────────────────────────────────────────────
-- {lens: 'brief' | 'all',
--  workspace_name: text,
--  brief: null (lens all) | {id, name, state, version: int | null (no published version),
--                            live_since: timestamptz | null (first publish), href: '/briefs/<id>'},
--  brief_count: int (Pilot and Active Briefs of the workspace),
--  sources_running: int (active radars of the lens Brief; for all, of the published Pilot and
--                   Active Briefs the count beside it names),
--  href: '/briefs/<id>' | '/briefs'}
create function public.home_header(p_ws uuid, p_brief uuid default null) returns jsonb
  language plpgsql stable security definer set search_path = ''
as $$
declare
  v_brief uuid := app.home_lens(p_ws, p_brief);
begin
  return jsonb_build_object(
    'lens', case when v_brief is null then 'all' else 'brief' end,
    'workspace_name', (select w.name from public.workspace w where w.id = p_ws),
    'brief', (select jsonb_build_object('id', b.id, 'name', b.name, 'state', b.state, 'version', bv.version,
                                        'live_since', (select min(x.published_at) from public.brief_version x where x.brief_id = b.id),
                                        'href', '/briefs/' || b.id)
                from public.brief b left join public.brief_version bv on bv.id = b.current_version_id
               where b.id = v_brief),
    'brief_count', (select count(*)::int from public.brief b where b.workspace_id = p_ws and b.state in ('Pilot', 'Active')),
    'sources_running', (select count(*)::int from public.radar r
                         where r.workspace_id = p_ws and r.status = 'active'
                           and r.brief_id = any(app.home_lens_radar_briefs(p_ws, v_brief, true))),
    'href', case when v_brief is null then '/briefs' else '/briefs/' || v_brief end);
end $$;

-- ── 4. Journey ───────────────────────────────────────────────────────────────
-- {steps: [{key, done: boolean, href}] in this order:
--    brief (a non-Archived Brief)              '/briefs/new'
--    sources (an active radar)                 '/sources'
--    first_accounts (an account)               '/accounts'
--    first_confirmed (a Confirmed account under the lens)  '/accounts?verdict=Confirmed'
--    contacts (a valid contact_point)          '/accounts?view=ready_to_contact'
--    first_email (never done: comes with the Email engine)  '/email'
--  complete: boolean (every step but first_email is done; the page hides the strip then)}
create function public.home_journey(p_ws uuid, p_brief uuid default null) returns jsonb
  language plpgsql stable security definer set search_path = ''
as $$
declare
  v_brief uuid := app.home_lens(p_ws, p_brief);
  v_done  boolean[];
begin
  v_done := array[
    exists (select 1 from public.brief b where b.workspace_id = p_ws and b.state <> 'Archived'),
    exists (select 1 from public.radar r where r.workspace_id = p_ws and r.status = 'active'),
    exists (select 1 from public.account a where a.workspace_id = p_ws),
    app.home_count(p_ws, v_brief, '{"field": {"system": "verdict"}, "op": "one_of", "value": ["Confirmed"]}') > 0,
    exists (select 1 from public.contact_point c where c.workspace_id = p_ws and c.status = 'valid'),
    false];
  return jsonb_build_object(
    'steps', (select jsonb_agg(jsonb_build_object('key', s.key, 'done', v_done[s.n], 'href', s.href) order by s.n)
                from (values (1, 'brief', '/briefs/new'), (2, 'sources', '/sources'), (3, 'first_accounts', '/accounts'),
                             (4, 'first_confirmed', '/accounts?verdict=Confirmed'),
                             (5, 'contacts', '/accounts?view=ready_to_contact'), (6, 'first_email', '/email')) s(n, key, href)),
    'complete', v_done[1] and v_done[2] and v_done[3] and v_done[4] and v_done[5]);
end $$;

-- ── 5. Number tiles ──────────────────────────────────────────────────────────
-- {accounts_found: {count, href: '/accounts'}            (all: every account; a Brief: the accounts
--                                                         with a verdict under its current version)
--  confirmed: {count, href: '/accounts?verdict=Confirmed'} (under the lens)
--  replies_month: {count, href: '/workspace/<ws>/inbox'}   (replies received this UTC month)
--  meetings_held_month: {count, href: '/workspace/<ws>/meetings'} (meetings held this UTC month)
--  spend_month: {spent_usd, cap_usd | null, pct | null, href: '/settings'}}
-- Replies, meetings and spend are workspace-wide whatever the lens.
create function public.home_metrics(p_ws uuid, p_brief uuid default null) returns jsonb
  language plpgsql stable security definer set search_path = ''
as $$
declare
  v_brief uuid := app.home_lens(p_ws, p_brief);
  v_month timestamptz := app.period_start('month');
begin
  return jsonb_build_object(
    'accounts_found', jsonb_build_object('count',
        case when v_brief is null then app.home_count(p_ws, null, null)
             else app.home_count(p_ws, v_brief, jsonb_build_object(
                    'field', '{"system": "verdict"}'::jsonb, 'op', 'one_of',
                    'value', to_jsonb(enum_range(null::public.verdict_value)::text[]))) end,
        'href', '/accounts'),
    'confirmed', jsonb_build_object('count',
        app.home_count(p_ws, v_brief, '{"field": {"system": "verdict"}, "op": "one_of", "value": ["Confirmed"]}'),
        'href', '/accounts?verdict=Confirmed'),
    'replies_month', jsonb_build_object('count',
        (select count(*)::int from public.activity a
          where a.workspace_id = p_ws and a.kind = 'reply_received' and a.occurred_at >= v_month),
        'href', '/workspace/' || p_ws || '/inbox'),
    'meetings_held_month', jsonb_build_object('count',
        (select count(*)::int from public.meeting m where m.workspace_id = p_ws and m.held_at >= v_month),
        'href', '/workspace/' || p_ws || '/meetings'),
    'spend_month', app.home_spend(p_ws) || jsonb_build_object('href', '/settings'));
end $$;

-- ── 6. Needs you ─────────────────────────────────────────────────────────────
-- {review: {count, href: '/review'}                        (pending review items + verdict reviews)
--  replies_to_classify: {count, href: '/workspace/<ws>/inbox'} (replies with no reply_class yet)
--  stale: {count, cells: int | null, estimate_usd: number | null, href: '/accounts?view=stale'}
--  errored: {count, href: '/accounts?view=errored'}}
-- stale / errored count ACCOUNTS: the workspace's active company accounts with at least one cell
-- in that state among the lens's columns (the P3 presets through view_entities: the Brief's
-- columns, else every workspace column and the global ones it does not shadow, as the
-- {"run": "*"} leaf scopes them). The run state of a cell does not depend on a Brief version, so
-- the lens only picks the columns.
-- stale.cells and stale.estimate_usd describe what re-running those same accounts would do, over
-- the same columns and the same accounts: the sum over the columns of
-- estimate_column_run(mode 'stale', the lens) .count (cells) and .cost_usd. That counts CELLS,
-- not accounts (one account can be stale on several columns), and leaves out the stale cells a
-- re-run cannot reach: cells with a run already queued or running, and columns that cannot be
-- run (fill methods other than extract and formula, and columns that never finished a run with
-- an inputs hash, which cannot be stale). When a column cannot be priced (e.g. the extract model
-- has no price: P0002), cells and estimate_usd are both null and the counts still return. The
-- card reads "N accounts stale · re-run M cells ≈ $X".
create function public.home_needs_you(p_ws uuid, p_brief uuid default null) returns jsonb
  language plpgsql stable security definer set search_path = ''
as $$
declare
  v_brief  uuid := app.home_lens(p_ws, p_brief);
  v_cols   uuid[];
  v_est    numeric := 0;
  v_cells  int := 0;
  v_run    jsonb;
  v_pid    uuid;
  v_failed boolean := false;
begin
  v_cols := case when v_brief is not null then app.brief_column_ids(p_ws, v_brief)
                 else array(select p.id from public.predicate p
                             where p.workspace_id = p_ws
                                or (p.workspace_id is null and not exists (select 1 from public.predicate w
                                                                            where w.workspace_id = p_ws and w.key = p.key))) end;
  for v_pid in select p.id from public.predicate p
                where p.id = any(v_cols) and p.fill_method in ('extract', 'formula')
                  and exists (select 1 from public.predicate_run pr
                               where pr.workspace_id = p_ws and pr.predicate_id = p.id and pr.finished_at is not null
                                 and pr.inputs_hash is not null and pr.status <> 'cancelled') loop
    begin
      v_run := public.estimate_column_run(p_ws, v_pid, 'stale', v_brief);
      v_est := v_est + (v_run ->> 'cost_usd')::numeric;
      v_cells := v_cells + (v_run ->> 'count')::int;
    exception when sqlstate 'P0002' or sqlstate '22023' then
      v_failed := true;
    end;
  end loop;
  if v_failed then
    v_est := null;
    v_cells := null;
  end if;
  return jsonb_build_object(
    'review', jsonb_build_object('count', app.home_review_count(p_ws), 'href', '/review'),
    'replies_to_classify', jsonb_build_object('count',
        (select count(*)::int from public.activity a
          where a.workspace_id = p_ws and a.kind = 'reply_received' and (a.payload ->> 'reply_class') is null),
        'href', '/workspace/' || p_ws || '/inbox'),
    'stale', jsonb_build_object('count', app.home_count(p_ws, v_brief, app.home_preset(p_ws, v_brief, 'stale')),
                                'cells', v_cells, 'estimate_usd', v_est, 'href', '/accounts?view=stale'),
    'errored', jsonb_build_object('count', app.home_count(p_ws, v_brief, app.home_preset(p_ws, v_brief, 'errored')),
                                  'href', '/accounts?view=errored'));
end $$;

-- ── 7. Accounts to act on ────────────────────────────────────────────────────
-- {accounts: [{account_id, entity_id, name,
--              verdict: text | null (the best verdict under the lens),
--              band: text | null, priority: number | null (the highest-priority score under the lens),
--              why_now: null | {claim_id, quote, observed_at} (the newest quoted claim the verdict
--                       cites, else the newest quoted claim; never a derived one),
--              valid_contacts: int, next_action: null | {text, mode} (the account's next_step),
--              href: '/workspace/<ws>/accounts/<account_id>'}]   at most 5, by priority (desc)
--  total: int (every account the ready_to_contact preset matches),
--  href: '/accounts?view=ready_to_contact'}
create function public.home_accounts_to_act(p_ws uuid, p_brief uuid default null) returns jsonb
  language plpgsql stable security definer set search_path = ''
as $$
declare
  v_brief uuid := app.home_lens(p_ws, p_brief);
  v_vers  uuid[] := app.home_lens_versions(p_ws, v_brief);
  v_rows  jsonb;
  v_total int;
begin
  select coalesce(jsonb_agg(jsonb_build_object(
           'account_id', a.id, 'entity_id', e.id, 'name', e.display_name,
           'verdict', vd.value, 'band', sc.band, 'priority', sc.priority,
           'why_now', app.home_quote(e.id, vd.claim_ids, true),
           'valid_contacts', (select count(*)::int from public.contact_point cp where cp.account_id = a.id and cp.status = 'valid'),
           'next_action', (select jsonb_build_object('text', ns.suggested_text, 'mode', ns.mode)
                             from public.next_step ns where ns.account_id = a.id),
           'href', '/workspace/' || p_ws || '/accounts/' || a.id) order by ve.n), '[]'::jsonb),
         coalesce(max(ve.total), 0)::int
    into v_rows, v_total
    from public.view_entities(p_ws, v_brief, app.home_preset(p_ws, v_brief, 'ready_to_contact'),
                              '[{"system": "priority", "dir": "desc"}]', 5, 0) with ordinality ve(entity_id, total, n)
    join public.entity e on e.id = ve.entity_id
    join public.account a on a.workspace_id = p_ws and a.entity_id = e.id
    left join lateral (select v.value, v.claim_ids from public.verdict v
                        where v.entity_id = e.id and v.brief_version_id = any(v_vers)
                        order by array_position(enum_range(null::public.verdict_value), v.value), v.computed_at desc
                        limit 1) vd on true
    left join lateral (select s.band, s.priority from public.score s
                        where s.account_id = a.id and s.brief_version_id = any(v_vers)
                        order by s.priority desc nulls last, s.computed_at desc
                        limit 1) sc on true;
  return jsonb_build_object('accounts', v_rows, 'total', v_total, 'href', '/accounts?view=ready_to_contact');
end $$;

-- ── 8. What changed (last 7 days) ────────────────────────────────────────────
-- {items: [...]} at most 10, newest first (by at), each one of:
--  {kind: 'verdict', at, entity_id, account_id | null, name, from: text, to: text,
--   quote: text | null (the newest quoted claim the verdict cites), href: '/workspace/<ws>/accounts/<id>' | null}
--     A verdict.changed event of the last 7 days compared with the one event before it of the same
--     entity under the same Brief (any version of it), so a re-judgement under a republished
--     version that keeps the value is not a change. No item when the value is unchanged.
--  {kind: 'first_verdicts', at (the newest of them), brief_id, brief_name, day: date (UTC),
--   count: int (accounts), href: '/accounts?brief=<brief_id>'}
--     The events with no event before them (first verdicts), one item per Brief per UTC day, so a
--     pilot's first run cannot fill the card. A first Unverified is not news and is left out.
--  {kind: 'brief_version', at (published_at), brief_id, brief_name, version, stale_cells: int,
--   href: '/briefs/<id>'}
--     stale_cells: the cells that are stale now of the columns whose config_hash this version
--     changed when it was published (the predicate.fill_changed / predicate.values_added audit of
--     the publish, which bumps the column's version), among the Brief's current columns. A version
--     that changed no column counts 0, whatever else is stale.
--  {kind: 'new_accounts', at (newest account), radar_id, radar_name, count: int,
--   href: '/accounts?radar=<radar_id>'}
--     accounts created in the last 7 days that a record of the radar mentions (count > 0 only),
--     over the lens's radars (app.home_lens_radar_briefs).
create function public.home_changes(p_ws uuid, p_brief uuid default null) returns jsonb
  language plpgsql stable security definer set search_path = ''
as $$
declare
  v_brief  uuid := app.home_lens(p_ws, p_brief);
  v_briefs uuid[] := app.home_lens_briefs(p_ws, v_brief);
  v_since  timestamptz := now() - interval '7 days';
begin
  return jsonb_build_object('items', coalesce((
    select jsonb_agg(x.item order by x.at desc, x.kind, x.tie)
      from (with vc as (
        -- Verdict events since v_since, each with the value of the single event before it of the
        -- same entity under the same Brief (prev null: a first verdict).
        select ev.oid, ev.at, ev.val, ev.vid, ev.entity_id, ev.brief_id, pv.val as prev
          from (select o.id as oid, o.created_at as at, o.payload ->> 'value' as val, o.aggregate_id as vid,
                       (o.payload ->> 'entity_id')::uuid as entity_id, bv.brief_id
                  from public.outbox o
                  join public.brief_version bv on bv.id = (o.payload ->> 'brief_version_id')::uuid
                 where o.event = 'verdict.changed' and o.workspace_id = p_ws and o.aggregate_type = 'verdict'
                   and o.created_at >= v_since and bv.brief_id = any(v_briefs)) ev
          left join lateral (
                select o2.payload ->> 'value' as val
                  from public.outbox o2
                 where o2.event = 'verdict.changed'
                   and o2.aggregate_id in (select v2.id from public.verdict v2
                                             join public.brief_version b2 on b2.id = v2.brief_version_id
                                            where v2.entity_id = ev.entity_id and b2.brief_id = ev.brief_id)
                   and (o2.created_at < ev.at or (o2.created_at = ev.at and o2.id < ev.oid))
                 order by o2.created_at desc, o2.id desc
                 limit 1) pv on true
      )
      select * from (
        -- Verdict changes: a value that differs from the one before it.
        select vc.at, 'verdict' as kind, vc.oid::text as tie,
               jsonb_build_object('kind', 'verdict', 'at', vc.at, 'entity_id', vc.entity_id, 'account_id', a.id,
                                  'name', e.display_name, 'from', vc.prev, 'to', vc.val,
                                  'quote', app.home_quote(vc.entity_id, vd.claim_ids, false) ->> 'quote',
                                  'href', case when a.id is not null then '/workspace/' || p_ws || '/accounts/' || a.id end) as item
          from vc
          join public.entity e on e.id = vc.entity_id
          left join public.verdict vd on vd.id = vc.vid
          left join public.account a on a.workspace_id = p_ws and a.entity_id = vc.entity_id
         where vc.prev is not null and vc.prev <> vc.val
        union all
        -- First verdicts, one item per Brief per UTC day (a pilot's first run judges many accounts
        -- at once and must not push everything else out). A first Unverified is not news.
        select max(vc.at), 'first_verdicts', vc.brief_id::text || ':' || (vc.at at time zone 'utc')::date,
               jsonb_build_object('kind', 'first_verdicts', 'at', max(vc.at), 'brief_id', vc.brief_id, 'brief_name', b.name,
                                  'day', (vc.at at time zone 'utc')::date, 'count', count(distinct vc.entity_id)::int,
                                  'href', '/accounts?brief=' || vc.brief_id)
          from vc
          join public.brief b on b.id = vc.brief_id
         where vc.prev is null and vc.val <> 'Unverified'
         group by vc.brief_id, b.name, (vc.at at time zone 'utc')::date
        union all
        -- Brief versions published, with the stale cells of the columns each one changed.
        select bv.published_at, 'brief_version', bv.id::text,
               jsonb_build_object('kind', 'brief_version', 'at', bv.published_at, 'brief_id', b.id, 'brief_name', b.name,
                                  'version', bv.version, 'href', '/briefs/' || b.id,
                                  'stale_cells', app.home_stale_cells(p_ws, array(
                                     select distinct al.target_id from public.audit_log al
                                      where al.target_type = 'predicate'
                                        and al.after ->> 'brief_version_id' = bv.id::text
                                        and al.workspace_id = p_ws
                                        and al.action in ('predicate.fill_changed', 'predicate.values_added')
                                        and al.target_id = any(cols.ids))))
          from (select b0.id as brief_id, app.brief_column_ids(p_ws, b0.id) as ids
                  from public.brief b0 where b0.workspace_id = p_ws and b0.id = any(v_briefs)) cols
          join public.brief b on b.id = cols.brief_id
          join public.brief_version bv on bv.brief_id = b.id
         where bv.published_at >= v_since
        union all
        -- New accounts by radar.
        select n.at, 'new_accounts', n.rid::text,
               jsonb_build_object('kind', 'new_accounts', 'at', n.at, 'radar_id', n.rid, 'radar_name', n.rname,
                                  'count', n.cnt, 'href', '/accounts?radar=' || n.rid)
          from (select r.id as rid, r.name as rname, count(distinct a.id)::int as cnt, max(a.created_at) as at
                  from public.account a
                  join public.mention m on m.entity_id = a.entity_id
                  join public.source_record sr on sr.id = m.source_record_id
                  join public.radar r on r.id = sr.radar_id
                 where a.workspace_id = p_ws and a.created_at >= v_since
                   and r.workspace_id = p_ws and r.brief_id = any(app.home_lens_radar_briefs(p_ws, v_brief))
                 group by r.id, r.name) n
      ) u order by u.at desc, u.kind, u.tie limit 10) x), '[]'::jsonb));
end $$;

-- ── 9. CRM card ──────────────────────────────────────────────────────────────
-- {stages: [{stage, count, href: '/crm/pipeline?stage=<stage, spaces as %20>'}] every stage, in
--          the stage order, including those at 0 (workspace-wide)
--  tasks_due_today: {count (open tasks due today, UTC), href: '/workspace/<ws>/meetings' (today's tasks)}}
create function public.home_crm(p_ws uuid, p_brief uuid default null) returns jsonb
  language plpgsql stable security definer set search_path = ''
as $$
begin
  perform app.home_lens(p_ws, p_brief);
  return jsonb_build_object(
    'stages', (select jsonb_agg(jsonb_build_object('stage', u.s, 'count', coalesce(c.n, 0),
                                                   'href', '/crm/pipeline?stage=' || replace(u.s::text, ' ', '%20'))
                                order by u.o)
                 from unnest(enum_range(null::public.stage)) with ordinality u(s, o)
                 left join (select a.stage, count(*)::int as n from public.account a
                             where a.workspace_id = p_ws group by a.stage) c on c.stage = u.s),
    'tasks_due_today', jsonb_build_object(
        'count', (select count(*)::int from public.task t
                   where t.workspace_id = p_ws and t.status = 'open' and t.due_date = (now() at time zone 'utc')::date),
        'href', '/workspace/' || p_ws || '/meetings'));
end $$;

-- ── 10. Sources card ─────────────────────────────────────────────────────────
-- {radars: [{id, name, type (connector_type), health: 'incident' | 'silent' | 'ok' | 'paused' (status
--            error / silent / active / paused), records_7d: int (source records of the radar in the
--            last 7 days), href: '/sources'}]   the lens's radars (for all: those of the published
--                                               Pilot, Active and Paused Briefs): incident, then
--                                               silent, then ok, then paused, then by name
--  href: '/sources'}
create function public.home_sources(p_ws uuid, p_brief uuid default null) returns jsonb
  language plpgsql stable security definer set search_path = ''
as $$
declare
  v_brief uuid := app.home_lens(p_ws, p_brief);
begin
  return jsonb_build_object(
    'radars', coalesce((
      select jsonb_agg(jsonb_build_object(
               'id', r.id, 'name', r.name, 'type', r.connector_type,
               'health', case r.status when 'error' then 'incident' when 'silent' then 'silent'
                                       when 'paused' then 'paused' else 'ok' end,
               'records_7d', (select count(*)::int from public.source_record sr
                               where sr.radar_id = r.id and sr.created_at >= now() - interval '7 days'),
               'href', '/sources')
             order by case r.status when 'error' then 0 when 'silent' then 1 when 'active' then 2 else 3 end,
                      lower(r.name), r.id)
        from public.radar r
       where r.workspace_id = p_ws and r.brief_id = any(app.home_lens_radar_briefs(p_ws, v_brief))),
      '[]'::jsonb),
    'href', '/sources');
end $$;

-- ── 11. Shell ────────────────────────────────────────────────────────────────
-- {review: int} pending review_item + pending verdict_review (the Review count in the sidebar).
create function public.sidebar_counts(p_ws uuid) returns jsonb
  language plpgsql stable security definer set search_path = ''
as $$
begin
  perform app.home_lens(p_ws, null);
  return jsonb_build_object('review', app.home_review_count(p_ws));
end $$;

-- {spent_usd: number, cap_usd: number | null (no monthly workspace cap), pct: number | null (no
-- cap; one decimal)} this UTC month, from app.period_spend.
create function public.budget_meter(p_ws uuid) returns jsonb
  language plpgsql stable security definer set search_path = ''
as $$
begin
  perform app.home_lens(p_ws, null);
  return app.home_spend(p_ws);
end $$;

-- ── 12. Grants ───────────────────────────────────────────────────────────────
revoke execute on function
  app.home_lens(uuid, uuid), app.home_lens_briefs(uuid, uuid), app.home_lens_radar_briefs(uuid, uuid, boolean),
  app.home_lens_versions(uuid, uuid),
  app.home_count(uuid, uuid, jsonb), app.home_preset(uuid, uuid, text), app.home_quote(uuid, uuid[], boolean),
  app.home_spend(uuid), app.home_review_count(uuid), app.home_stale_cells(uuid, uuid[])
  from public, anon, authenticated;

revoke execute on function
  public.home_header(uuid, uuid), public.home_journey(uuid, uuid), public.home_metrics(uuid, uuid),
  public.home_needs_you(uuid, uuid), public.home_accounts_to_act(uuid, uuid), public.home_changes(uuid, uuid),
  public.home_crm(uuid, uuid), public.home_sources(uuid, uuid), public.sidebar_counts(uuid), public.budget_meter(uuid)
  from public, anon;
grant execute on function
  public.home_header(uuid, uuid), public.home_journey(uuid, uuid), public.home_metrics(uuid, uuid),
  public.home_needs_you(uuid, uuid), public.home_accounts_to_act(uuid, uuid), public.home_changes(uuid, uuid),
  public.home_crm(uuid, uuid), public.home_sources(uuid, uuid), public.sidebar_counts(uuid), public.budget_meter(uuid)
  to authenticated;
