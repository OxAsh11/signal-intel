-- Migration 0032: Brief pilot, calibration and edit preview (M1 steps 3–5, edit tiers, S6).
--   * Draft → Pilot (start_pilot) → Active (go_live). A pilot scores a small sample of accounts.
--   * Calibrate: an Editor marks pilot verdicts right or wrong; marks are feedback and can become
--     examples in the Brief's draft.
--   * A new version of a published Brief needs a preview of the exact draft (tier, account
--     changes, cost, duration). An over-budget preview needs an approved budget_approval.

-- ── 1. Event catalogue (reviewed addition) ───────────────────────────────────
alter type public.event_type add value 'brief.preview_requested';

-- ── 2. Settings ──────────────────────────────────────────────────────────────
alter table public.workspace_settings
  add column pilot_accuracy_target_pct int not null default 80 check (pilot_accuracy_target_pct between 1 and 100),
  add column pilot_min_confirmed_pct   int not null default 20 check (pilot_min_confirmed_pct between 0 and 100);

-- ── 3. Pilot sample ──────────────────────────────────────────────────────────
create table public.pilot_sample (
  brief_version_id uuid not null references public.brief_version (id),
  entity_id        uuid not null references public.entity (id),
  slot             int  not null check (slot > 0),
  org_id           uuid not null references public.organisation (id) on delete restrict,
  workspace_id     uuid not null references public.workspace (id) on delete restrict,
  created_at       timestamptz not null default now(),
  primary key (brief_version_id, entity_id),
  unique (brief_version_id, slot)
);
alter table public.pilot_sample enable row level security;
create policy pilot_sample_read on public.pilot_sample for select to authenticated
  using (app.has_workspace_role(workspace_id, 'Viewer'));
grant select on public.pilot_sample to authenticated;

-- ── 4. Edit previews ─────────────────────────────────────────────────────────
create table public.brief_preview (
  id               uuid primary key default gen_random_uuid(),
  org_id           uuid not null references public.organisation (id) on delete restrict,
  workspace_id     uuid not null references public.workspace (id) on delete restrict,
  brief_id         uuid not null references public.brief (id) on delete cascade,
  base_version_id  uuid not null references public.brief_version (id),
  draft_hash       text not null,             -- md5(draft_config::text) at request time
  draft_config     jsonb not null,
  status           text not null default 'pending' check (status in ('pending', 'ready', 'failed')),
  tier             public.edit_tier,
  rejudge          boolean,
  result           jsonb not null default '{}',
  estimate_usd     numeric(12,4),
  duration_minutes int,
  over_budget      boolean,
  requested_by     uuid references public.member (id),
  created_at       timestamptz not null default now(),
  completed_at     timestamptz
);
create index brief_preview_brief_idx on public.brief_preview (brief_id, created_at desc);
alter table public.brief_preview enable row level security;
create policy brief_preview_read on public.brief_preview for select to authenticated
  using (app.has_workspace_role(workspace_id, 'Viewer'));
grant select on public.brief_preview to authenticated;

-- ── 5. Verdict review accepts pilot marks ────────────────────────────────────
alter table public.verdict_review drop constraint verdict_review_reason_check;
alter table public.verdict_review add constraint verdict_review_reason_check
  check (reason in ('conflict', 'sample', 'pilot'));

-- ── 6. Shared checks ─────────────────────────────────────────────────────────
create function app.assert_brief_publishable(p_cfg jsonb) returns void
  language plpgsql immutable set search_path = ''
as $$
begin
  if coalesce(trim(p_cfg ->> 'offer'), '') = '' then
    raise exception 'draft_config.offer is required and cannot be empty' using errcode = '42501';
  end if;
  if coalesce(trim(p_cfg ->> 'hypothesis'), '') = '' then
    raise exception 'draft_config.hypothesis is required and cannot be empty' using errcode = '42501';
  end if;
  if jsonb_array_length(coalesce(p_cfg -> 'examples' -> 'positive', '[]'::jsonb)) < 3 then
    raise exception 'draft_config.examples.positive requires at least 3 items' using errcode = '42501';
  end if;
  if jsonb_array_length(coalesce(p_cfg -> 'examples' -> 'negative', '[]'::jsonb)) < 2 then
    raise exception 'draft_config.examples.negative requires at least 2 items' using errcode = '42501';
  end if;
  if jsonb_array_length(coalesce(p_cfg -> 'sources' -> 'radar_types', '[]'::jsonb)) < 1 then
    raise exception 'draft_config.sources.radar_types requires at least 1 item' using errcode = '42501';
  end if;
end $$;

-- The ready preview of the Brief's exact current draft, against its current version.
create function app.matching_preview(p_brief uuid) returns public.brief_preview
  language sql stable set search_path = ''
as $$
  select p.* from public.brief_preview p
    join public.brief b on b.id = p.brief_id
   where p.brief_id = p_brief
     and p.status = 'ready'
     and b.draft_config is not null
     and p.draft_hash = md5(b.draft_config::text)
     and p.base_version_id = b.current_version_id
   order by p.created_at desc
   limit 1
$$;

create function app.preview_approved(p_preview uuid) returns boolean
  language sql stable set search_path = ''
as $$
  select exists (select 1 from public.budget_approval a
                  where a.job_type = 'brief.publish'
                    and a.job_args ->> 'preview_id' = p_preview::text
                    and a.status = 'approved')
$$;

-- ── 7. Versions from a preview ───────────────────────────────────────────────
-- Writes the next version from the draft with the preview's tier. Verdicts carry over when the
-- edit does not change what they were judged against (hypothesis, examples, evidence rules).
create function app.publish_from_preview(p_brief uuid, p_preview uuid, p_change_note text)
  returns uuid
  language plpgsql security definer set search_path = ''
as $$
declare
  b     public.brief%rowtype;
  p     public.brief_preview%rowtype;
  v_ver int;
  v_vid uuid;
  r     record;
begin
  select * into b from public.brief where id = p_brief for update;
  select * into p from public.brief_preview where id = p_preview;
  -- Re-checked under the row lock: a draft saved after the caller's check is not this preview.
  if b.draft_config is null or md5(b.draft_config::text) <> p.draft_hash
     or b.current_version_id is distinct from p.base_version_id then
    raise exception 'the draft changed after this preview' using errcode = '55000';
  end if;
  perform app.assert_brief_publishable(b.draft_config);

  select coalesce(max(version), 0) + 1 into v_ver from public.brief_version where brief_id = p_brief;
  insert into public.brief_version
    (brief_id, org_id, workspace_id, version, config, rubric, weights, edit_tier, change_note, published_by)
  values
    (p_brief, b.org_id, b.workspace_id, v_ver, b.draft_config, '{}'::jsonb, '{}'::jsonb, p.tier,
     p_change_note, app.current_member_id(b.org_id))
  returning id into v_vid;

  if not coalesce(p.rejudge, true) then
    for r in select entity_id, value, claim_ids, quotes, inputs from public.verdict
              where brief_version_id = b.current_version_id loop
      perform app.upsert_verdict(v_vid, r.entity_id, r.value, r.claim_ids, r.quotes,
                                 r.inputs || jsonb_build_object('copied_from', b.current_version_id));
    end loop;
    -- A pilot keeps its sample: the copied verdicts are the accounts it is calibrating.
    if b.state = 'Pilot' then
      insert into public.pilot_sample (brief_version_id, entity_id, slot, org_id, workspace_id)
      select v_vid, entity_id, slot, org_id, workspace_id
        from public.pilot_sample where brief_version_id = b.current_version_id;
    end if;
  end if;

  update public.brief
     set current_version_id = v_vid, draft_config = null, intake_status = 'none', updated_at = now()
   where id = p_brief;
  perform app.audit(b.org_id, b.workspace_id, 'brief.version_published', 'brief', p_brief,
                    jsonb_build_object('version', v_ver - 1),
                    jsonb_build_object('version', v_ver, 'tier', p.tier, 'preview_id', p_preview));
  perform app.emit('brief.version_published'::public.event_type, b.org_id, b.workspace_id,
                   'brief_version', v_vid,
                   jsonb_build_object('brief_id', p_brief, 'version', v_ver,
                                      'brief_version_id', v_vid, 'copied_verdicts', not coalesce(p.rejudge, true)));
  return v_vid;
end $$;

-- app.publish_brief: a Draft publishes version 1 and goes Active (kept for existing callers;
-- the UI starts a pilot instead). A Brief that already has a version needs a matching preview,
-- and an approval when that preview is over budget.
create or replace function app.publish_brief(p_brief uuid, p_change_note text default null) returns uuid
  language plpgsql security definer set search_path = ''
as $$
declare
  b     public.brief%rowtype;
  p     public.brief_preview%rowtype;
  v_ver int;
  v_vid uuid;
begin
  select * into b from public.brief where id = p_brief;
  if not found then
    raise exception 'brief not found' using errcode = 'P0002';
  end if;
  if not app.has_workspace_role(b.workspace_id, 'Editor') then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  if b.state not in ('Draft', 'Pilot', 'Active', 'Paused') then
    raise exception 'only Draft, Pilot, Active or Paused briefs can be published' using errcode = '42501';
  end if;

  if b.state <> 'Draft' then
    if b.draft_config is null then
      raise exception 'no draft changes to publish' using errcode = '42501';
    end if;
    p := app.matching_preview(p_brief);
    if p.id is null then
      raise exception 'preview the changes before publishing' using errcode = '55000';
    end if;
    if p.over_budget and not app.preview_approved(p.id) then
      raise exception 'this edit is over budget and needs an approval' using errcode = '55000';
    end if;
    return app.publish_from_preview(p_brief, p.id, p_change_note);
  end if;

  perform app.assert_brief_publishable(coalesce(b.draft_config, '{}'));
  select coalesce(max(version), 0) + 1 into v_ver from public.brief_version where brief_id = p_brief;
  insert into public.brief_version
    (brief_id, org_id, workspace_id, version, config, rubric, weights, change_note, published_by)
  values
    (p_brief, b.org_id, b.workspace_id, v_ver, b.draft_config,
     '{}'::jsonb, '{}'::jsonb, p_change_note, app.current_member_id(b.org_id))
  returning id into v_vid;
  update public.brief
     set state = 'Active', current_version_id = v_vid, intake_status = 'none',
         draft_config = null, updated_at = now()
   where id = p_brief;
  perform app.audit(b.org_id, b.workspace_id, 'brief.version_published', 'brief', p_brief,
                    jsonb_build_object('state', 'Draft'),
                    jsonb_build_object('state', 'Active', 'version', v_ver));
  perform app.emit('brief.version_published'::public.event_type, b.org_id, b.workspace_id,
                   'brief_version', v_vid,
                   jsonb_build_object('brief_id', p_brief, 'version', v_ver, 'brief_version_id', v_vid));
  return v_vid;
end $$;

-- app.publish_brief_approved: the worker's brief.publish job, resumed after approval.
create function app.publish_brief_approved(p_brief uuid, p_preview uuid) returns uuid
  language plpgsql security definer set search_path = ''
as $$
declare p public.brief_preview%rowtype;
begin
  p := app.matching_preview(p_brief);
  if p.id is null or p.id <> p_preview then
    raise exception 'the draft changed after this preview' using errcode = '55000';
  end if;
  if not app.preview_approved(p_preview) then
    raise exception 'this edit has no approval' using errcode = '55000';
  end if;
  return app.publish_from_preview(p_brief, p_preview, 'Approved over-budget edit');
end $$;

-- ── 8. Pilot lifecycle ───────────────────────────────────────────────────────
create function app.start_pilot(p_brief uuid) returns uuid
  language plpgsql security definer set search_path = ''
as $$
declare
  b     public.brief%rowtype;
  v_vid uuid;
begin
  select * into b from public.brief where id = p_brief for update;
  if not found then
    raise exception 'brief not found' using errcode = 'P0002';
  end if;
  if not app.has_workspace_role(b.workspace_id, 'Editor') then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  if b.state <> 'Draft' then
    raise exception 'only a Draft brief can start a pilot' using errcode = '55000';
  end if;
  perform app.assert_brief_publishable(coalesce(b.draft_config, '{}'));
  insert into public.brief_version
    (brief_id, org_id, workspace_id, version, config, rubric, weights, change_note, published_by)
  values
    (p_brief, b.org_id, b.workspace_id, 1, b.draft_config, '{}'::jsonb, '{}'::jsonb,
     'Pilot', app.current_member_id(b.org_id))
  returning id into v_vid;
  update public.brief
     set state = 'Pilot', current_version_id = v_vid, intake_status = 'none',
         draft_config = null, updated_at = now()
   where id = p_brief;
  perform app.audit(b.org_id, b.workspace_id, 'brief.pilot_started', 'brief', p_brief,
                    jsonb_build_object('state', 'Draft'),
                    jsonb_build_object('state', 'Pilot', 'version', 1));
  perform app.emit('brief.version_published'::public.event_type, b.org_id, b.workspace_id,
                   'brief_version', v_vid,
                   jsonb_build_object('brief_id', p_brief, 'version', 1, 'brief_version_id', v_vid));
  return v_vid;
end $$;

create function app.go_live(p_brief uuid) returns void
  language plpgsql security definer set search_path = ''
as $$
declare b public.brief%rowtype;
begin
  select * into b from public.brief where id = p_brief for update;
  if not found then
    raise exception 'brief not found' using errcode = 'P0002';
  end if;
  if not app.has_workspace_role(b.workspace_id, 'Editor') then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  if b.state <> 'Pilot' then
    raise exception 'only a Brief in Pilot can go live' using errcode = '55000';
  end if;
  if b.draft_config is not null then
    raise exception 'publish or discard the draft changes before going live' using errcode = '55000';
  end if;
  update public.brief set state = 'Active', updated_at = now() where id = p_brief;
  perform app.audit(b.org_id, b.workspace_id, 'brief.state_changed', 'brief', p_brief,
                    jsonb_build_object('state', 'Pilot'), jsonb_build_object('state', 'Active'));
  -- The live version must now cover the whole workspace, not just a sample or a pause.
  perform app.emit('brief.version_published'::public.event_type, b.org_id, b.workspace_id,
                   'brief_version', b.current_version_id,
                   jsonb_build_object('brief_id', p_brief, 'brief_version_id', b.current_version_id,
                                      'rederive_only', true));
end $$;

-- Takes a slot in the version's pilot sample. True when the entity holds one (now or already).
create function app.claim_pilot_slot(p_version uuid, p_entity uuid) returns boolean
  language plpgsql security definer set search_path = ''
as $$
declare
  v_size  int;
  v_taken int;
  v_org   uuid;
  v_ws    uuid;
begin
  perform pg_advisory_xact_lock(hashtextextended('pilot:' || p_version::text, 0));
  if exists (select 1 from public.pilot_sample where brief_version_id = p_version and entity_id = p_entity) then
    return true;
  end if;
  select bv.org_id, bv.workspace_id, s.pilot_sample_size into v_org, v_ws, v_size
    from public.brief_version bv
    join public.workspace_settings s on s.workspace_id = bv.workspace_id
   where bv.id = p_version;
  select count(*) into v_taken from public.pilot_sample where brief_version_id = p_version;
  if v_taken >= coalesce(v_size, 20) then
    return false;
  end if;
  insert into public.pilot_sample (brief_version_id, entity_id, slot, org_id, workspace_id)
  values (p_version, p_entity, v_taken + 1, v_org, v_ws);
  return true;
end $$;

-- Archiving a pilot is allowed; everything else as before.
create or replace function app.update_brief_state(p_brief uuid, p_new_state public.brief_state) returns void
  language plpgsql security definer set search_path = ''
as $$
declare
  b public.brief%rowtype;
begin
  select * into b from public.brief where id = p_brief;
  if not found then
    raise exception 'brief not found' using errcode = 'P0002';
  end if;
  if not app.has_workspace_role(b.workspace_id, 'Editor') then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  if b.state = p_new_state then return; end if;
  if not (
    (b.state = 'Active' and p_new_state = 'Paused'  ) or
    (b.state = 'Paused' and p_new_state = 'Active'  ) or
    (b.state = 'Active' and p_new_state = 'Archived') or
    (b.state = 'Paused' and p_new_state = 'Archived') or
    (b.state = 'Pilot'  and p_new_state = 'Archived')
  ) then
    raise exception 'invalid state transition: % → %', b.state, p_new_state using errcode = '42501';
  end if;
  update public.brief set state = p_new_state, updated_at = now() where id = p_brief;
  perform app.audit(b.org_id, b.workspace_id, 'brief.state_changed', 'brief', p_brief,
                    jsonb_build_object('state', b.state),
                    jsonb_build_object('state', p_new_state));
  if b.state = 'Paused' and p_new_state = 'Active' then
    -- The live version must now cover the whole workspace, not just a sample or a pause.
    perform app.emit('brief.version_published'::public.event_type, b.org_id, b.workspace_id,
                     'brief_version', b.current_version_id,
                     jsonb_build_object('brief_id', p_brief, 'brief_version_id', b.current_version_id,
                                        'rederive_only', true));
  end if;
end $$;

-- ── 9. Preview requests ──────────────────────────────────────────────────────
create function public.request_brief_preview(p_brief uuid) returns uuid
  language plpgsql security definer set search_path = ''
as $$
declare
  b    public.brief%rowtype;
  v_id uuid;
begin
  select * into b from public.brief where id = p_brief;
  if not found or not app.has_workspace_role(b.workspace_id, 'Editor') then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  if b.state not in ('Pilot', 'Active', 'Paused') or b.current_version_id is null then
    raise exception 'only a published Brief has edits to preview' using errcode = '55000';
  end if;
  if b.draft_config is null then
    raise exception 'there are no draft changes to preview' using errcode = '22023';
  end if;
  insert into public.brief_preview
    (org_id, workspace_id, brief_id, base_version_id, draft_hash, draft_config, requested_by)
  values
    (b.org_id, b.workspace_id, p_brief, b.current_version_id, md5(b.draft_config::text),
     b.draft_config, app.current_member_id(b.org_id))
  returning id into v_id;
  perform app.emit('brief.preview_requested'::public.event_type, b.org_id, b.workspace_id,
                   'brief_preview', v_id,
                   jsonb_build_object('brief_id', p_brief, 'preview_id', v_id));
  return v_id;
end $$;

create function app.complete_brief_preview(
  p_id uuid, p_tier public.edit_tier, p_rejudge boolean, p_result jsonb,
  p_estimate numeric, p_minutes int, p_over_budget boolean
) returns void
  language sql security definer set search_path = ''
as $$
  update public.brief_preview
     set status = 'ready', tier = p_tier, rejudge = p_rejudge, result = coalesce(p_result, '{}'),
         estimate_usd = p_estimate, duration_minutes = p_minutes, over_budget = p_over_budget,
         completed_at = now()
   where id = p_id
$$;

create function app.fail_brief_preview(p_id uuid) returns void
  language sql security definer set search_path = ''
as $$
  update public.brief_preview set status = 'failed', completed_at = now() where id = p_id
$$;

create function public.request_publish_approval(p_preview uuid) returns uuid
  language plpgsql security definer set search_path = ''
as $$
declare
  p    public.brief_preview%rowtype;
  v_id uuid;
begin
  select * into p from public.brief_preview where id = p_preview;
  if not found or not app.has_workspace_role(p.workspace_id, 'Editor') then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  if p.status <> 'ready' or not coalesce(p.over_budget, false) then
    raise exception 'only a ready, over-budget preview needs an approval' using errcode = '55000';
  end if;
  select id into v_id from public.budget_approval
   where job_type = 'brief.publish' and job_args ->> 'preview_id' = p_preview::text
     and status in ('pending', 'approved');
  if v_id is not null then
    return v_id;
  end if;
  insert into public.budget_approval (org_id, workspace_id, brief_id, job_type, job_args, estimate_usd, reason)
  values (p.org_id, p.workspace_id, p.brief_id, 'brief.publish',
          jsonb_build_object('brief_id', p.brief_id, 'preview_id', p_preview),
          coalesce(p.estimate_usd, 0), 'Brief edit over budget')
  returning id into v_id;
  perform app.audit(p.org_id, p.workspace_id, 'budget.approval_requested', 'budget_approval', v_id,
                    null, jsonb_build_object('preview_id', p_preview, 'estimate_usd', p.estimate_usd));
  return v_id;
end $$;

-- ── 10. Calibrate ────────────────────────────────────────────────────────────
-- Marks a pilot verdict right ('confirm') or wrong ('correct' + value) through the verdict
-- review path, so feedback, the human hold and the audit match the review queue. With
-- p_add_example the account name joins the draft's positive (Confirmed/Likely) or negative
-- examples and leaves the other list.
create function public.mark_pilot_verdict(
  p_verdict     uuid,
  p_action      text,
  p_value       public.verdict_value default null,
  p_add_example boolean default false
) returns void
  language plpgsql security definer set search_path = ''
as $$
declare
  v       public.verdict%rowtype;
  b       public.brief%rowtype;
  v_rev   uuid;
  v_name  text;
  v_list  text;
  v_other text;
  v_cfg   jsonb;
begin
  select * into v from public.verdict where id = p_verdict;
  if not found or not app.has_workspace_role(v.workspace_id, 'Editor') then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  select * into b from public.brief where current_version_id = v.brief_version_id;
  if not found or b.state <> 'Pilot' then
    raise exception 'only verdicts of a Brief in Pilot are calibrated here' using errcode = '55000';
  end if;

  select id into v_rev from public.verdict_review
   where verdict_id = p_verdict and reason = 'pilot' and status = 'pending';
  if v_rev is null then
    insert into public.verdict_review (org_id, workspace_id, verdict_id, reason, verdict_value)
    values (v.org_id, v.workspace_id, p_verdict, 'pilot', v.value)
    returning id into v_rev;
  end if;
  perform public.resolve_verdict_review(v_rev, p_action, p_value, 'pilot calibration');

  if p_add_example then
    select display_name into v_name from public.entity where id = v.entity_id;
    select value::text into v_list from public.verdict where id = p_verdict;
    v_list  := case when v_list in ('Confirmed', 'Likely') then 'positive' else 'negative' end;
    v_other := case when v_list = 'positive' then 'negative' else 'positive' end;
    v_cfg := coalesce(b.draft_config,
                      (select config from public.brief_version where id = b.current_version_id));
    v_cfg := jsonb_set(v_cfg, array['examples', v_other],
               coalesce((select jsonb_agg(e) from jsonb_array_elements(v_cfg -> 'examples' -> v_other) e
                          where e <> to_jsonb(v_name)), '[]'::jsonb), true);
    if not coalesce(v_cfg -> 'examples' -> v_list ? v_name, false) then
      v_cfg := jsonb_set(v_cfg, array['examples', v_list],
                 coalesce(v_cfg -> 'examples' -> v_list, '[]'::jsonb) || to_jsonb(v_name), true);
    end if;
    update public.brief set draft_config = v_cfg, updated_at = now() where id = b.id;
  end if;
end $$;

-- ── 11. Wrappers and grants ──────────────────────────────────────────────────
create function public.start_pilot(p_brief uuid) returns uuid
  language plpgsql security definer set search_path = ''
as $$ begin return app.start_pilot(p_brief); end $$;

create function public.go_live(p_brief uuid) returns void
  language plpgsql security definer set search_path = ''
as $$ begin perform app.go_live(p_brief); end $$;

revoke execute on function
  app.assert_brief_publishable(jsonb), app.matching_preview(uuid), app.preview_approved(uuid),
  app.publish_from_preview(uuid, uuid, text), app.publish_brief_approved(uuid, uuid),
  app.start_pilot(uuid), app.go_live(uuid), app.claim_pilot_slot(uuid, uuid),
  app.complete_brief_preview(uuid, public.edit_tier, boolean, jsonb, numeric, int, boolean),
  app.fail_brief_preview(uuid)
  from public, anon, authenticated;
grant execute on function
  app.publish_brief_approved(uuid, uuid), app.claim_pilot_slot(uuid, uuid),
  app.complete_brief_preview(uuid, public.edit_tier, boolean, jsonb, numeric, int, boolean),
  app.fail_brief_preview(uuid)
  to postgres, service_role;

revoke execute on function
  public.start_pilot(uuid), public.go_live(uuid), public.request_brief_preview(uuid),
  public.request_publish_approval(uuid),
  public.mark_pilot_verdict(uuid, text, public.verdict_value, boolean)
  from public, anon;
grant execute on function
  public.start_pilot(uuid), public.go_live(uuid), public.request_brief_preview(uuid),
  public.request_publish_approval(uuid),
  public.mark_pilot_verdict(uuid, text, public.verdict_value, boolean)
  to authenticated;

-- ── 12. Kill switches ────────────────────────────────────────────────────────
insert into public.job_type_config (job_type, enabled) values
  ('brief.preview', true), ('brief.rederive', true), ('brief.publish', true)
on conflict (job_type) do nothing;
