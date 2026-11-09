-- Phase 1c: Connector framework (S2) + Source/Radar Hub (M2).

-- 1. Event catalogue: add radar.fetch_queued (reviewed addition, see plan).
alter type public.event_type add value 'radar.fetch_queued';

-- 2. radar table (M2: a configured source connector bound to a Brief).
create table public.radar (
  id              uuid primary key default gen_random_uuid(),
  org_id          uuid not null references public.organisation (id) on delete restrict,
  workspace_id    uuid not null references public.workspace (id) on delete restrict,
  brief_id        uuid not null references public.brief (id) on delete restrict,
  connector_type  text not null check (connector_type in ('csv_upload', 'website', 'news', 'job_post')),
  name            text not null check (length(btrim(name)) between 1 and 200),
  config          jsonb not null default '{}',
  cursor_state    jsonb not null default '{}',
  status          text not null default 'active' check (status in ('active', 'paused', 'error', 'silent')),
  last_fetched_at timestamptz,
  last_error      text,
  record_count    int not null default 0,
  yield_count     int not null default 0,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);
create index radar_brief_idx    on public.radar (brief_id);
create index radar_ws_type_idx  on public.radar (workspace_id, connector_type);

-- 3. radar_query table.
create table public.radar_query (
  id                uuid primary key default gen_random_uuid(),
  org_id            uuid not null references public.organisation (id) on delete restrict,
  radar_id          uuid not null references public.radar (id) on delete cascade,
  query_text        text not null check (length(btrim(query_text)) between 1 and 500),
  language          text not null default 'en',
  source            text not null default 'hypothesis' check (source in ('hypothesis', 'icp', 'user')),
  edited_by_member  uuid references public.member (id),
  created_at        timestamptz not null default now()
);
create index radar_query_radar_idx on public.radar_query (radar_id);

-- 4. RLS.
alter table public.radar       enable row level security;
alter table public.radar_query enable row level security;

create policy radar_read       on public.radar       for select to authenticated using (app.has_workspace_role(workspace_id, 'Viewer'));
create policy radar_query_read on public.radar_query for select to authenticated using (app.is_org_member(org_id));

grant select on public.radar, public.radar_query to authenticated;

-- 5. Kill switches for new job types.
insert into public.job_type_config (job_type, enabled) values
  ('source.fetch_csv',       true),
  ('source.fetch_website',   true),
  ('source.fetch_news',      true),
  ('source.fetch_job_post',  true),
  ('radar.generate_queries', true),
  ('radar.health_check',     true);

-- 6. Write-path RPCs.
-- app.create_radar: Editor+ creates a radar and fires fetch via outbox.
create function app.create_radar(
  p_ws             uuid,
  p_brief          uuid,
  p_connector_type text,
  p_name           text,
  p_config         jsonb default '{}'
) returns uuid
language plpgsql security definer set search_path = '' as $$
declare
  v_org    uuid;
  v_radar  uuid;
begin
  if not app.has_workspace_role(p_ws, 'Editor') then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  if not exists (select 1 from public.brief where id = p_brief and workspace_id = p_ws) then
    raise exception 'brief not found' using errcode = 'P0002';
  end if;
  select org_id into v_org from public.workspace where id = p_ws;
  insert into public.radar (org_id, workspace_id, brief_id, connector_type, name, config)
  values (v_org, p_ws, p_brief, p_connector_type, p_name, p_config)
  returning id into v_radar;
  -- Outbox: triggers the fetch worker.
  insert into public.outbox (event, org_id, workspace_id, aggregate_type, aggregate_id, payload)
  values ('radar.fetch_queued', v_org, p_ws, 'radar', v_radar,
          jsonb_build_object('radar_id', v_radar, 'connector_type', p_connector_type,
                             'org_id', v_org, 'workspace_id', p_ws, 'brief_id', p_brief));
  return v_radar;
end $$;

-- app.remove_radar: Editor+ soft-deletes by setting status=paused (keeps records).
create function app.remove_radar(p_radar uuid) returns void
language plpgsql security definer set search_path = '' as $$
declare v_ws uuid;
begin
  select workspace_id into v_ws from public.radar where id = p_radar;
  if not app.has_workspace_role(v_ws, 'Editor') then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  update public.radar set status = 'paused', updated_at = now() where id = p_radar;
end $$;

-- app.update_radar_cursor: service_role only — called after a successful fetch.
create function app.update_radar_cursor(
  p_radar        uuid,
  p_cursor_state jsonb,
  p_fetched      int,
  p_yield        int
) returns void
language plpgsql security definer set search_path = '' as $$
begin
  update public.radar set
    cursor_state    = p_cursor_state,
    last_fetched_at = now(),
    record_count    = record_count + p_fetched,
    yield_count     = yield_count  + p_yield,
    status          = 'active',
    last_error      = null,
    updated_at      = now()
  where id = p_radar;
end $$;

-- app.upsert_radar_queries: service_role only — called by generate_queries job.
create function app.upsert_radar_queries(p_radar uuid, p_queries jsonb) returns void
language plpgsql security definer set search_path = '' as $$
declare
  v_org  uuid;
  q      jsonb;
begin
  select org_id into v_org from public.radar where id = p_radar;
  delete from public.radar_query where radar_id = p_radar and source <> 'user';
  for q in select * from jsonb_array_elements(p_queries) loop
    insert into public.radar_query (org_id, radar_id, query_text, language, source)
    values (v_org, p_radar,
            q->>'query_text', coalesce(q->>'language', 'en'), coalesce(q->>'source', 'hypothesis'));
  end loop;
end $$;

-- app.create_source_record: service_role; deduplicates on (org_id, content_hash).
create function app.create_source_record(
  p_org          uuid,
  p_workspace    uuid,
  p_connector_id text,
  p_source_type  text,
  p_url          text,
  p_fetched_at   timestamptz,
  p_content_hash text,
  p_payload_ref  text,
  p_language     text
) returns uuid
language plpgsql security definer set search_path = '' as $$
declare v_id uuid;
begin
  insert into public.source_record
    (org_id, workspace_id, connector_id, source_type, url, fetched_at, content_hash, payload_ref, language)
  values (p_org, p_workspace, p_connector_id, p_source_type, p_url, p_fetched_at, p_content_hash, p_payload_ref, p_language)
  on conflict (org_id, content_hash) do nothing
  returning id into v_id;
  if v_id is null then
    -- Duplicate: return existing id.
    select id into v_id from public.source_record where org_id = p_org and content_hash = p_content_hash;
  end if;
  return v_id;
end $$;

-- app.mark_radar_error: service_role.
create function app.mark_radar_error(p_radar uuid, p_error text) returns void
language plpgsql security definer set search_path = '' as $$
begin
  update public.radar set status = 'error', last_error = p_error, updated_at = now() where id = p_radar;
end $$;

-- app.mark_radar_silent: service_role — records health incident.
create function app.mark_radar_silent(p_radar uuid) returns void
language plpgsql security definer set search_path = '' as $$
declare v_ws uuid; v_name text;
begin
  select workspace_id, name into v_ws, v_name from public.radar where id = p_radar;
  update public.radar set status = 'silent', updated_at = now() where id = p_radar;
  perform app.record_health('radar:' || p_radar::text, 'incident',
    jsonb_build_object('radar_id', p_radar, 'name', v_name), 'M17');
end $$;

-- Revoke the built-in PUBLIC execute grant before re-granting to specific roles.
revoke execute on function
  app.create_radar(uuid, uuid, text, text, jsonb),
  app.remove_radar(uuid),
  app.update_radar_cursor(uuid, jsonb, int, int),
  app.upsert_radar_queries(uuid, jsonb),
  app.create_source_record(uuid, uuid, text, text, text, timestamptz, text, text, text),
  app.mark_radar_error(uuid, text),
  app.mark_radar_silent(uuid)
from public, anon, authenticated;

-- Grant to service_role (the Python worker), not to authenticated.
grant execute on function
  app.create_radar(uuid, uuid, text, text, jsonb),
  app.remove_radar(uuid),
  app.update_radar_cursor(uuid, jsonb, int, int),
  app.upsert_radar_queries(uuid, jsonb),
  app.create_source_record(uuid, uuid, text, text, text, timestamptz, text, text, text),
  app.mark_radar_error(uuid, text),
  app.mark_radar_silent(uuid)
to postgres, service_role;

-- Public wrappers for client-callable RPCs (create_radar, remove_radar).
create function public.create_radar(p_ws uuid, p_brief uuid, p_connector_type text, p_name text, p_config jsonb default '{}')
  returns uuid language sql security definer set search_path = '' as $$
  select app.create_radar(p_ws, p_brief, p_connector_type, p_name, p_config)
$$;
create function public.remove_radar(p_radar uuid)
  returns void language sql security definer set search_path = '' as $$
  select app.remove_radar(p_radar)
$$;
revoke execute on function
  public.create_radar(uuid, uuid, text, text, jsonb),
  public.remove_radar(uuid)
from public, anon;

grant execute on function public.create_radar(uuid, uuid, text, text, jsonb),
                          public.remove_radar(uuid) to authenticated;
