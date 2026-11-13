-- Discovery breadth (M2): Brave Search radar types, lexicon pre-filter, per-query yield,
-- targeted search for Unverified entities, and radars provisioned from a Brief's sources.

-- ── 1. Radar types and lexicon ────────────────────────────────────────────────
alter table public.radar drop constraint radar_connector_type_check;
alter table public.radar add constraint radar_connector_type_check check (connector_type in
  ('csv_upload', 'website', 'news', 'job_post', 'registry', 'tender', 'event', 'directory', 'social_post'));

alter table public.radar
  add column lexicon           text[]  not null default '{}',
  add column lexicon_edited    boolean not null default false,
  add column prefiltered_count int     not null default 0;

alter table public.radar_query
  add column record_count      int not null default 0,
  add column prefiltered_count int not null default 0,
  add column yield_count       int not null default 0;

-- ── 2. Source record attribution ─────────────────────────────────────────────
-- The first query that finds a record owns it; a duplicate hit is not re-attributed.
alter table public.source_record
  add column radar_id       uuid references public.radar (id) on delete set null,
  add column radar_query_id uuid references public.radar_query (id) on delete set null,
  add column content_kind   text not null default 'full' check (content_kind in ('full', 'snippet'));
create index source_record_radar_idx       on public.source_record (radar_id);
create index source_record_radar_query_idx on public.source_record (radar_query_id);

-- The FKs clear these columns when a query is deleted, so the immutability trigger lets them change.
drop trigger source_record_immutable on public.source_record;
create trigger source_record_immutable before update on public.source_record
  for each row execute function app.forbid_update_except(
    'extraction_status', 'status_detail', 'updated_at', 'radar_id', 'radar_query_id');

drop function app.create_source_record(uuid, uuid, text, text, text, timestamptz, text, text, text, text);
create function app.create_source_record(
  p_org          uuid,
  p_workspace    uuid,
  p_connector_id text,
  p_source_type  text,
  p_url          text,
  p_fetched_at   timestamptz,
  p_content_hash text,
  p_payload_ref  text,
  p_language     text,
  p_content      text default null,
  p_radar        uuid default null,
  p_radar_query  uuid default null,
  p_content_kind text default 'full'
) returns uuid
language plpgsql security definer set search_path = '' as $$
declare
  v_id    uuid;
  v_radar uuid := p_radar;
begin
  -- Every radar connector names itself 'radar:<id>': attribute the record even when the caller
  -- did not pass the radar (news, website, job post and CSV connectors).
  if v_radar is null and p_connector_id ~ '^radar:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' then
    select id into v_radar from public.radar
     where id = substr(p_connector_id, 7)::uuid and org_id = p_org;
  end if;

  insert into public.source_record
    (org_id, workspace_id, connector_id, source_type, url, fetched_at, content_hash, payload_ref,
     language, radar_id, radar_query_id, content_kind)
  values
    (p_org, p_workspace, p_connector_id, p_source_type, p_url, p_fetched_at, p_content_hash, p_payload_ref,
     p_language, v_radar, p_radar_query, coalesce(p_content_kind, 'full'))
  on conflict (org_id, content_hash) do nothing
  returning id into v_id;

  if v_id is null then
    select id into v_id from public.source_record where org_id = p_org and content_hash = p_content_hash;
    return v_id;
  end if;

  if length(btrim(coalesce(p_content, ''))) > 0 then
    insert into public.source_payload (source_record_id, org_id, workspace_id, content)
    values (v_id, p_org, p_workspace, left(p_content, 200000));
  end if;

  perform app.emit(
    'source_record.created'::public.event_type,
    p_org, p_workspace, 'source_record', v_id,
    jsonb_build_object('source_record_id', v_id, 'source_type', p_source_type,
                       'connector_id', p_connector_id)
  );
  return v_id;
end $$;

-- ── 3. Query upsert keeps history ────────────────────────────────────────────
-- Unchanged queries keep their row (and counts); only generated queries that are no longer
-- produced are removed. A user-edited lexicon is never overwritten.
drop function app.upsert_radar_queries(uuid, jsonb);
create function app.upsert_radar_queries(p_radar uuid, p_queries jsonb, p_lexicon text[] default null)
returns void
language plpgsql security definer set search_path = '' as $$
declare
  v_org   uuid;
  v_texts text[];
  q       jsonb;
begin
  select org_id into v_org from public.radar where id = p_radar;
  select coalesce(array_agg(btrim(e ->> 'query_text')), '{}') into v_texts
    from jsonb_array_elements(coalesce(p_queries, '[]')) e;

  delete from public.radar_query
   where radar_id = p_radar and source <> 'user' and not (query_text = any (v_texts));

  for q in select * from jsonb_array_elements(coalesce(p_queries, '[]')) loop
    if not exists (select 1 from public.radar_query
                    where radar_id = p_radar and query_text = btrim(q ->> 'query_text')) then
      insert into public.radar_query (org_id, radar_id, query_text, language, source)
      values (v_org, p_radar, btrim(q ->> 'query_text'), coalesce(q ->> 'language', 'en'),
              coalesce(q ->> 'source', 'hypothesis'));
    end if;
  end loop;

  if p_lexicon is not null then
    update public.radar set lexicon = app.clean_terms(p_lexicon), updated_at = now()
     where id = p_radar and not lexicon_edited;
  end if;
end $$;

-- Trimmed, de-duplicated (first wins), non-empty, at most 50 terms.
create function app.clean_terms(p_terms text[]) returns text[]
language sql immutable set search_path = '' as $$
  select coalesce(array_agg(t order by first_at), '{}')
    from (select btrim(x) as t, min(ord) as first_at
            from unnest(coalesce(p_terms, '{}')) with ordinality as u(x, ord)
           where length(btrim(x)) > 0
           group by btrim(x)
           order by min(ord)
           limit 50) s
$$;

-- ── 4. Yield (per radar and per query) ───────────────────────────────────────
-- update_radar_cursor no longer adds to yield_count: yield is computed, not "new records".
-- It adds the fetch's new records (p_yield, historically named) to record_count, so the count
-- between yield runs matches what compute_radar_yield will write.
create or replace function app.update_radar_cursor(
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
    record_count    = record_count + greatest(p_yield, 0),
    status          = case when status = 'paused' then 'paused' else 'active' end,
    last_error      = null,
    updated_at      = now()
  where id = p_radar;
end $$;

-- A record yields when one of its active claims is on an entity with a Confirmed or Likely
-- verdict on the radar's Brief's current version.
create function app.compute_radar_yield() returns int
language plpgsql security definer set search_path = '' as $$
declare v_n int;
begin
  create temp table _ry on commit drop as
  select sr.id, sr.radar_id, sr.radar_query_id,
         (sr.extraction_status = 'irrelevant' and sr.status_detail = 'prefilter') as prefiltered,
         exists (
           select 1
             from public.claim c
             join public.radar r on r.id = sr.radar_id
             join public.brief b on b.id = r.brief_id
             join public.verdict v on v.brief_version_id = b.current_version_id
                                  and v.entity_id = app.canonical_entity(c.entity_id)
            where c.source_record_id = sr.id and c.status = 'active'
              and v.value in ('Confirmed', 'Likely')
         ) as yielded
    from public.source_record sr
   where sr.radar_id is not null;

  update public.radar r set
    record_count      = s.records,
    prefiltered_count = s.prefiltered,
    yield_count       = s.yielded,
    updated_at        = now()
  from (select radar_id, count(*)::int as records,
               count(*) filter (where prefiltered)::int as prefiltered,
               count(*) filter (where yielded)::int as yielded
          from _ry group by radar_id) s
  where r.id = s.radar_id;
  get diagnostics v_n = row_count;

  update public.radar_query q set
    record_count      = s.records,
    prefiltered_count = s.prefiltered,
    yield_count       = s.yielded
  from (select radar_query_id, count(*)::int as records,
               count(*) filter (where prefiltered)::int as prefiltered,
               count(*) filter (where yielded)::int as yielded
          from _ry where radar_query_id is not null group by radar_query_id) s
  where q.id = s.radar_query_id;

  drop table _ry;
  return v_n;
end $$;

-- ── 5. Brief sources → radars ────────────────────────────────────────────────
create function app.brief_source_connector(p_source text) returns text
language sql immutable set search_path = '' as $$
  select case p_source
    when 'news_publications'   then 'news'
    when 'job_postings'        then 'job_post'
    when 'public_registries'   then 'registry'
    when 'tenders_procurement' then 'tender'
    when 'events'              then 'event'
    when 'directories'         then 'directory'
    when 'public_social'       then 'social_post'
    when 'company_websites'    then 'website'
  end
$$;

-- Creates one radar per selected source type that runs from queries alone. Any existing radar of
-- that type, including one the user removed (paused), blocks re-creation.
create function app.provision_radars(p_brief uuid) returns int
language plpgsql security definer set search_path = '' as $$
declare
  b      record;
  v_type text;
  v_n    int := 0;
begin
  select br.id, br.org_id, br.workspace_id, bv.config
    into b
    from public.brief br join public.brief_version bv on bv.id = br.current_version_id
   where br.id = p_brief;
  if not found then return 0; end if;

  for v_type in
    select distinct app.brief_source_connector(s)
      from jsonb_array_elements_text(coalesce(b.config -> 'sources' -> 'radar_types', '[]')) s
  loop
    continue when v_type is null
      or v_type not in ('news', 'registry', 'tender', 'event', 'directory', 'social_post');
    continue when exists (select 1 from public.radar where brief_id = p_brief and connector_type = v_type);
    insert into public.radar (org_id, workspace_id, brief_id, connector_type, name)
    values (b.org_id, b.workspace_id, p_brief, v_type, case v_type
      when 'news'        then 'News & publications'
      when 'registry'    then 'Public registries & permits'
      when 'tender'      then 'Tenders & procurement'
      when 'event'       then 'Events'
      when 'directory'   then 'Directories & partner lists'
      when 'social_post' then 'Public social posts' end);
    v_n := v_n + 1;
  end loop;
  return v_n;
end $$;

-- ── 6. Targeted search throttle ──────────────────────────────────────────────
create table public.targeted_search (
  id           uuid primary key default gen_random_uuid(),
  org_id       uuid not null references public.organisation (id) on delete restrict,
  workspace_id uuid not null references public.workspace (id) on delete restrict,
  brief_id     uuid not null references public.brief (id) on delete restrict,
  entity_id    uuid not null references public.entity (id),
  searched_at  timestamptz not null default now(),
  unique (brief_id, entity_id)
);
alter table public.targeted_search enable row level security;
create policy targeted_search_read on public.targeted_search for select to authenticated
  using (app.has_workspace_role(workspace_id, 'Viewer'));
grant select on public.targeted_search to authenticated;

-- True when this (brief, entity) may be searched now: once per 30 days, at most 100 searches per
-- Brief per day, and never for an entity known only from targeted searches (that would let one
-- search's results trigger more searches without end).
create function app.claim_targeted_search(p_brief uuid, p_entity uuid) returns boolean
language plpgsql security definer set search_path = '' as $$
declare v_id uuid;
begin
  if exists (select 1 from public.entity_alias where entity_id = p_entity)
     and not exists (select 1 from public.entity_alias a
                       join public.source_record sr on sr.id = a.source_record_id
                      where a.entity_id = p_entity and sr.connector_id not like 'targeted:%') then
    return false;
  end if;
  if (select count(*) from public.targeted_search
       where brief_id = p_brief and searched_at > now() - interval '1 day') >= 100 then
    return false;
  end if;
  insert into public.targeted_search (org_id, workspace_id, brief_id, entity_id)
  select org_id, workspace_id, id, p_entity from public.brief where id = p_brief
  on conflict (brief_id, entity_id) do update set searched_at = now()
    where public.targeted_search.searched_at < now() - interval '30 days'
  returning id into v_id;
  return v_id is not null;
end $$;

-- A search that failed gives its slot back, so a retry (or a later verdict) can search again.
create function app.release_targeted_search(p_brief uuid, p_entity uuid) returns void
language sql security definer set search_path = '' as $$
  delete from public.targeted_search where brief_id = p_brief and entity_id = p_entity
$$;

-- ── 7. Editor RPCs: queries and lexicon ──────────────────────────────────────
create function public.add_radar_query(p_radar uuid, p_text text, p_language text default 'en')
returns uuid
language plpgsql security definer set search_path = '' as $$
declare r record; v_id uuid;
begin
  select id, org_id, workspace_id into r from public.radar where id = p_radar;
  if r.id is null or not app.has_workspace_role(r.workspace_id, 'Editor') then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  if length(btrim(coalesce(p_text, ''))) not between 1 and 500 then
    raise exception 'a query needs 1 to 500 characters' using errcode = '22023';
  end if;
  insert into public.radar_query (org_id, radar_id, query_text, language, source, edited_by_member)
  values (r.org_id, p_radar, btrim(p_text), coalesce(nullif(btrim(p_language), ''), 'en'), 'user',
          app.current_member_id(r.org_id))
  returning id into v_id;
  perform app.audit(r.org_id, r.workspace_id, 'radar.query_added', 'radar', p_radar, null,
                    jsonb_build_object('query_id', v_id, 'query_text', btrim(p_text)));
  return v_id;
end $$;

create function public.remove_radar_query(p_query uuid) returns void
language plpgsql security definer set search_path = '' as $$
declare q record;
begin
  select rq.id, rq.radar_id, rq.query_text, r.org_id, r.workspace_id into q
    from public.radar_query rq join public.radar r on r.id = rq.radar_id where rq.id = p_query;
  if q.id is null or not app.has_workspace_role(q.workspace_id, 'Editor') then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  delete from public.radar_query where id = p_query;
  perform app.audit(q.org_id, q.workspace_id, 'radar.query_removed', 'radar', q.radar_id,
                    jsonb_build_object('query_id', p_query, 'query_text', q.query_text), null);
end $$;

create function public.update_radar_lexicon(p_radar uuid, p_terms text[]) returns void
language plpgsql security definer set search_path = '' as $$
declare r record;
begin
  select id, org_id, workspace_id, lexicon into r from public.radar where id = p_radar;
  if r.id is null or not app.has_workspace_role(r.workspace_id, 'Editor') then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  update public.radar set lexicon = app.clean_terms(p_terms), lexicon_edited = true, updated_at = now()
   where id = p_radar;
  perform app.audit(r.org_id, r.workspace_id, 'radar.lexicon_updated', 'radar', p_radar,
                    jsonb_build_object('lexicon', r.lexicon),
                    jsonb_build_object('lexicon', app.clean_terms(p_terms)));
end $$;

-- ── 8. create_radar validates config.sites ───────────────────────────────────
create or replace function app.create_radar(
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
  v_sites  jsonb := coalesce(p_config, '{}') -> 'sites';
begin
  if not app.has_workspace_role(p_ws, 'Editor') then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  if not exists (select 1 from public.brief where id = p_brief and workspace_id = p_ws) then
    raise exception 'brief not found' using errcode = 'P0002';
  end if;
  if v_sites is not null and (
       jsonb_typeof(v_sites) <> 'array'
       or jsonb_array_length(v_sites) > 20
       or exists (select 1 from jsonb_array_elements(v_sites) s
                   where jsonb_typeof(s) <> 'string'
                      or (s #>> '{}') !~ '^[a-z0-9.-]+\.[a-z]{2,}(/.*)?$')) then
    raise exception 'sites must be a list of up to 20 domains, like example.com' using errcode = '22023';
  end if;
  select org_id into v_org from public.workspace where id = p_ws;
  insert into public.radar (org_id, workspace_id, brief_id, connector_type, name, config)
  values (v_org, p_ws, p_brief, p_connector_type, p_name, coalesce(p_config, '{}'))
  returning id into v_radar;
  insert into public.outbox (event, org_id, workspace_id, aggregate_type, aggregate_id, payload)
  values ('radar.fetch_queued', v_org, p_ws, 'radar', v_radar,
          jsonb_build_object('radar_id', v_radar, 'connector_type', p_connector_type,
                             'org_id', v_org, 'workspace_id', p_ws, 'brief_id', p_brief));
  return v_radar;
end $$;

-- ── 9. Price, kill switches ──────────────────────────────────────────────────
-- Brave Search: $5 per 1,000 requests (2026-11), i.e. $0.005 per call.
insert into public.model_price (model, provider, unit, usd_per_million) values
  ('brave-search', 'brave', 'fetch', 5000.0000)
on conflict (model, unit) do nothing;

insert into public.job_type_config (job_type, enabled) values
  ('source.fetch_registry',    true),
  ('source.fetch_tender',      true),
  ('source.fetch_event',       true),
  ('source.fetch_directory',   true),
  ('source.fetch_social_post', true),
  ('source.targeted_search',   true),
  ('radar.yield_compute',      true)
on conflict (job_type) do nothing;

-- ── 10. Grants ───────────────────────────────────────────────────────────────
revoke execute on function
  app.create_source_record(uuid, uuid, text, text, text, timestamptz, text, text, text, text, uuid, uuid, text),
  app.upsert_radar_queries(uuid, jsonb, text[]),
  app.compute_radar_yield(),
  app.provision_radars(uuid),
  app.claim_targeted_search(uuid, uuid),
  app.release_targeted_search(uuid, uuid),
  app.clean_terms(text[]),
  app.brief_source_connector(text)
from public, anon, authenticated;
grant execute on function
  app.create_source_record(uuid, uuid, text, text, text, timestamptz, text, text, text, text, uuid, uuid, text),
  app.upsert_radar_queries(uuid, jsonb, text[]),
  app.compute_radar_yield(),
  app.provision_radars(uuid),
  app.claim_targeted_search(uuid, uuid),
  app.release_targeted_search(uuid, uuid),
  app.clean_terms(text[]),
  app.brief_source_connector(text)
to postgres, service_role;

revoke execute on function
  public.add_radar_query(uuid, text, text),
  public.remove_radar_query(uuid),
  public.update_radar_lexicon(uuid, text[])
from public, anon;
grant execute on function
  public.add_radar_query(uuid, text, text),
  public.remove_radar_query(uuid),
  public.update_radar_lexicon(uuid, text[])
to authenticated;

-- ── 11. Backfill: attribute existing records to their radars and replace the old yield
-- ("new records") with the computed one.
update public.source_record sr
   set radar_id = r.id
  from public.radar r
 where sr.radar_id is null
   and sr.connector_id ~ '^radar:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
   and r.id = substr(sr.connector_id, 7)::uuid;
update public.radar set yield_count = 0;
select app.compute_radar_yield();
