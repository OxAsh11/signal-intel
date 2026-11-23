-- ── P8: Reports and Settings (scaffold) ─────────────────────────────────────
-- Hand labels: a separate table so derived labels stay pure.
create table if not exists public.account_tag (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organisation (id) on delete restrict,
  workspace_id uuid not null references public.workspace (id) on delete cascade,
  account_id uuid not null references public.account (id) on delete cascade,
  label text not null,
  note text,
  created_by uuid references public.member (id),
  created_at timestamptz not null default now()
);
alter table public.account_tag enable row level security;
create policy account_tag_read on public.account_tag for select to authenticated
  using (app.has_workspace_role(workspace_id, 'Viewer'));
create policy account_tag_write on public.account_tag for all to authenticated
  using (app.has_workspace_role(workspace_id, 'Editor'))
  with check (app.has_workspace_role(workspace_id, 'Editor'));
grant select on public.account_tag to authenticated;

create or replace function public.add_account_tag(p_account uuid, p_label text, p_note text default null) returns uuid
language plpgsql security definer set search_path = ''
as $$
declare
  a public.account%rowtype;
  v_id uuid;
begin
  select * into a from public.account where id = p_account;
  if not found or not app.has_workspace_role(a.workspace_id, 'Editor') then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  if nullif(btrim(coalesce(p_label, '')), '') is null then
    raise exception 'a label is required' using errcode = '22023';
  end if;
  insert into public.account_tag (org_id, workspace_id, account_id, label, note, created_by)
  values (a.org_id, a.workspace_id, p_account, btrim(p_label), nullif(btrim(coalesce(p_note, '')), ''),
          app.current_member_id(a.org_id))
  returning id into v_id;
  return v_id;
end $$;

create or replace function public.remove_account_tag(p_tag uuid) returns void
language plpgsql security definer set search_path = ''
as $$
declare
  t public.account_tag%rowtype;
begin
  select * into t from public.account_tag where id = p_tag;
  if not found or not app.has_workspace_role(t.workspace_id, 'Editor') then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  delete from public.account_tag where id = p_tag;
end $$;

revoke all on function public.add_account_tag(uuid, text, text) from public, anon;
revoke all on function public.remove_account_tag(uuid) from public, anon;
grant execute on function public.add_account_tag(uuid, text, text) to authenticated;
grant execute on function public.remove_account_tag(uuid) to authenticated;

-- segment filter AST editor: reuse the existing segment table (P2). No schema change needed.

-- A simple funnel count RPC for Reports.
create or replace function public.report_funnel(p_ws uuid, p_brief uuid default null) returns jsonb
language plpgsql stable security definer set search_path = ''
as $$
declare
  v_found bigint;
  v_matched bigint;
  v_confirmed bigint;
begin
  if not app.has_workspace_role(p_ws, 'Viewer') then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  select count(*) into v_found from public.account a
    where a.workspace_id = p_ws and (p_brief is null or a.brief_id = p_brief);
  select count(*) into v_matched from public.score s
    join public.account a on a.id = s.account_id
    where a.workspace_id = p_ws and (p_brief is null or a.brief_id = p_brief);
  select count(*) into v_confirmed from public.verdict v
    join public.account a on a.entity_id = v.entity_id
    where a.workspace_id = p_ws and v.value in ('Confirmed','Likely')
      and (p_brief is null or a.brief_id = p_brief);
  return jsonb_build_object('found', v_found, 'matched', v_matched, 'confirmed', v_confirmed);
end $$;

revoke all on function public.report_funnel(uuid, uuid) from public, anon;
grant execute on function public.report_funnel(uuid, uuid) to authenticated;