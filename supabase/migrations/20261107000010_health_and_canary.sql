-- M17 Health skeleton and the end-to-end canary (PRD: "a synthetic source record passes through
-- extraction, resolution, verdict and scoring daily in every environment"). Phase 0 uses stub
-- extraction and resolution in SQL; Phase 1 swaps in the real M3/M4 jobs behind the same check.

create table public.health_check (
  id         bigint generated always as identity primary key,
  org_id     uuid references public.organisation (id) on delete restrict,   -- null = platform-wide
  component  text not null,
  status     text not null check (status in ('ok', 'degraded', 'incident', 'silent')),
  detail     jsonb not null default '{}',
  checked_at timestamptz not null default now()
);
create index health_check_component_time_idx on public.health_check (component, checked_at desc);

create table public.health_incident (
  id              uuid primary key default gen_random_uuid(),
  org_id          uuid references public.organisation (id) on delete restrict,
  component       text not null,
  owner           text not null,              -- the module's named owner, e.g. 'M2', 'S4'
  severity        text not null check (severity in ('info', 'warning', 'critical')),
  status          text not null default 'open' check (status in ('open', 'resolved')),
  summary         text not null,
  opened_at       timestamptz not null default now(),
  resolved_at     timestamptz,
  resolution_note text,
  -- M17: every incident has an owner and a resolution note.
  constraint resolved_has_note check (
    status = 'open' or (resolved_at is not null and coalesce(length(btrim(resolution_note)), 0) > 0))
);
create unique index health_incident_one_open on public.health_incident (component, coalesce(org_id, '00000000-0000-0000-0000-000000000000'::uuid))
  where status = 'open';

alter table public.health_check    enable row level security;
alter table public.health_incident enable row level security;
-- Org Admins see their organisation's health; platform-wide rows are service-only for now.
create policy health_check_read    on public.health_check    for select to authenticated using (org_id is not null and app.has_org_role(org_id, 'Admin'));
create policy health_incident_read on public.health_incident for select to authenticated using (org_id is not null and app.has_org_role(org_id, 'Admin'));
grant select on public.health_check, public.health_incident to authenticated;

-- Opens an incident (idempotent per component) or resolves it.
create function app.record_health(p_component text, p_status text, p_detail jsonb, p_owner text default 'M17')
returns void
language plpgsql set search_path = '' as $$
begin
  insert into public.health_check (component, status, detail) values (p_component, p_status, coalesce(p_detail, '{}'));
  if p_status in ('incident', 'silent') then
    insert into public.health_incident (component, owner, severity, summary)
    values (p_component, p_owner, 'critical', coalesce(p_detail ->> 'error', p_status))
    on conflict do nothing;
  elsif p_status = 'ok' then
    update public.health_incident
       set status = 'resolved', resolved_at = now(), resolution_note = 'Recovered: next check passed'
     where component = p_component and org_id is null and status = 'open';
  end if;
end $$;

-- ── Canary fixtures (fixed ids, platform-owned, invisible to every customer) ──
insert into public.organisation (id, name) values
  ('00000000-0000-4000-a000-0000000000c1', 'Platform canary');
insert into public.workspace (id, org_id, name) values
  ('00000000-0000-4000-a000-0000000000c2', '00000000-0000-4000-a000-0000000000c1', 'Canary');
insert into public.brief (id, org_id, workspace_id, name, state) values
  ('00000000-0000-4000-a000-0000000000c3', '00000000-0000-4000-a000-0000000000c1',
   '00000000-0000-4000-a000-0000000000c2', 'Canary brief', 'Active');
insert into public.brief_version (id, brief_id, org_id, workspace_id, version, config, rubric, weights, change_note) values
  ('00000000-0000-4000-a000-0000000000c4', '00000000-0000-4000-a000-0000000000c3',
   '00000000-0000-4000-a000-0000000000c1', '00000000-0000-4000-a000-0000000000c2', 1,
   '{"hypothesis": "The pipeline is alive"}',
   '{"Confirmed": {"predicate": "canary.pipeline_ok", "value": true, "min_sources": 1}}',
   '{"fit": 0.25, "evidence": 0.25, "scale": 0.25, "timing": 0.25}',
   'Canary fixture');
update public.brief set current_version_id = '00000000-0000-4000-a000-0000000000c4'
 where id = '00000000-0000-4000-a000-0000000000c3';
insert into public.predicate (id, key, label, value_type, extraction_hint) values
  ('00000000-0000-4000-a000-0000000000c5', 'canary.pipeline_ok', 'Canary: pipeline alive', 'boolean',
   'Synthetic predicate used only by the daily canary.');
insert into public.extractor_version (id, capability, prompt_version, model, parser_version) values
  ('00000000-0000-4000-a000-0000000000c6', 'canary', 'v1', 'none', 'v1');
insert into public.job_type_config (job_type, expected_window) values ('system.canary', interval '26 hours');

-- Runs the chain observed → resolved → fact → derived in one transaction, emitting the same
-- outbox events the real modules will. On any failure it records an incident instead of raising.
create function app.run_canary() returns jsonb
language plpgsql set search_path = '' as $$
declare
  c_org  constant uuid := '00000000-0000-4000-a000-0000000000c1';
  c_ws   constant uuid := '00000000-0000-4000-a000-0000000000c2';
  c_bv   constant uuid := '00000000-0000-4000-a000-0000000000c4';
  c_pred constant uuid := '00000000-0000-4000-a000-0000000000c5';
  c_ext  constant uuid := '00000000-0000-4000-a000-0000000000c6';
  c_domain constant text := 'canary.signal-intel.test';
  v_day text := to_char(now() at time zone 'utc', 'YYYY-MM-DD');
  v_sr uuid; v_mention uuid; v_entity uuid; v_claim uuid; v_verdict uuid;
  v_new_entity boolean := false;
  v_result jsonb;
begin
  begin
    perform set_config('app.writer', 'derivation', true);
    perform set_config('app.channel', 'system', true);

    -- observed
    insert into public.source_record (org_id, workspace_id, connector_id, source_type, url, fetched_at,
                                      content_hash, language, extraction_status)
    values (c_org, c_ws, 'canary', 'synthetic', 'https://' || c_domain || '/' || v_day, now(),
            'canary:' || gen_random_uuid(), 'en', 'pending')
    returning id into v_sr;
    perform app.emit('source_record.created', c_org, c_ws, 'source_record', v_sr, jsonb_build_object('canary', true));

    insert into public.mention (org_id, source_record_id, entity_type, raw_name, raw_domain, raw_country)
    values (c_org, v_sr, 'company', 'Canary Industries', c_domain, 'NL')
    returning id into v_mention;

    -- resolved (stub M4: exact domain alias, else create)
    select a.entity_id into v_entity from public.entity_alias a
     where a.org_id = c_org and a.alias_type = 'domain' and a.value_normalised = c_domain
     order by a.created_at limit 1;
    if v_entity is null then
      insert into public.entity (org_id, entity_type, display_name) values (c_org, 'company', 'Canary Industries')
      returning id into v_entity;
      insert into public.entity_alias (org_id, entity_id, alias_type, value_normalised, confidence, source_record_id)
      values (c_org, v_entity, 'domain', c_domain, 100, v_sr);
      perform app.emit('entity.created', c_org, null, 'entity', v_entity, '{}');
      v_new_entity := true;
    end if;
    update public.mention set entity_id = v_entity, match_method = 'alias:domain', match_confidence = 100
     where id = v_mention;

    -- fact (stub M3)
    insert into public.claim (org_id, entity_id, predicate_id, value, quote, source_record_id, observed_at,
                              extractor_version_id, confidence)
    values (c_org, v_entity, c_pred, 'true', 'Canary pipeline check ' || v_day, v_sr, now(), c_ext, 100)
    returning id into v_claim;
    update public.source_record set extraction_status = 'extracted', updated_at = now() where id = v_sr;
    perform app.emit('claim.created', c_org, null, 'claim', v_claim, jsonb_build_object('predicate', 'canary.pipeline_ok'));

    -- derived (stub M5)
    insert into public.verdict (org_id, workspace_id, brief_version_id, entity_id, value, claim_ids, quotes, inputs)
    values (c_org, c_ws, c_bv, v_entity, 'Confirmed', array[v_claim],
            jsonb_build_array(jsonb_build_object('claim_id', v_claim, 'quote', 'Canary pipeline check ' || v_day)),
            jsonb_build_object('brief_version', 1, 'extractor_version_id', c_ext, 'canary', true))
    on conflict (brief_version_id, entity_id) do update
      set value = excluded.value, claim_ids = excluded.claim_ids, quotes = excluded.quotes,
          inputs = excluded.inputs, computed_at = now()
    returning id into v_verdict;
    perform app.emit('verdict.changed', c_org, c_ws, 'verdict', v_verdict, jsonb_build_object('value', 'Confirmed'));

    v_result := jsonb_build_object('status', 'ok', 'source_record_id', v_sr, 'entity_id', v_entity,
                                   'new_entity', v_new_entity, 'claim_id', v_claim, 'verdict_id', v_verdict);
    perform app.record_health('pipeline.canary', 'ok', v_result, 'M17');
    return v_result;
  exception when others then
    v_result := jsonb_build_object('status', 'incident', 'error', sqlerrm, 'sqlstate', sqlstate);
    perform app.record_health('pipeline.canary', 'incident', v_result, 'M17');
    return v_result;
  end;
end $$;

revoke execute on function app.run_canary(), app.record_health(text, text, jsonb, text) from authenticated;
grant execute on function app.run_canary(), app.record_health(text, text, jsonb, text) to service_role;
