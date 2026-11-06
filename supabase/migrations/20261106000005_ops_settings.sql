-- S1 kill switches and limits per job type; workspace settings with PRD defaults.

create table public.job_type_config (
  job_type                  text primary key,
  enabled                   boolean not null default true,         -- the kill switch
  max_concurrency           int not null default 4 check (max_concurrency > 0),
  per_workspace_concurrency int not null default 2 check (per_workspace_concurrency > 0),
  timeout_seconds           int not null default 900 check (timeout_seconds > 0),
  max_retries               int not null default 5 check (max_retries >= 0),
  expected_window           interval,   -- M17 silence rule: no run inside this window = incident
  updated_at                timestamptz not null default now()
);
alter table public.job_type_config enable row level security;   -- service role only

insert into public.job_type_config (job_type, expected_window) values
  ('system.heartbeat', interval '5 minutes'),
  ('outbox.relay',     null);

create table public.workspace_settings (
  workspace_id            uuid primary key references public.workspace (id) on delete cascade,
  org_id                  uuid not null references public.organisation (id) on delete restrict,
  gate_auto_threshold     int not null default 90 check (gate_auto_threshold between 1 and 100),  -- S1
  gate_review_threshold   int not null default 40 check (gate_review_threshold between 0 and 99), -- S1
  dormancy_days           int not null default 45 check (dormancy_days > 0),                      -- M11
  attribution_window_days int not null default 90 check (attribution_window_days > 0),            -- M12
  bounce_warn_pct         numeric(5,2) not null default 5,                                        -- M9
  bounce_pause_pct        numeric(5,2) not null default 10,                                       -- M9
  verdict_sample_pct      numeric(5,2) not null default 5,                                        -- M5
  verdict_sample_min      int not null default 10,                                                -- M5
  pilot_sample_size       int not null default 20,                                                -- M1
  next_step_mode          public.next_step_mode not null default 'Suggest',                       -- M12
  updated_at              timestamptz not null default now(),
  constraint gate_order   check (gate_review_threshold < gate_auto_threshold),
  constraint bounce_order check (bounce_warn_pct < bounce_pause_pct)
);

create function app.create_workspace_settings() returns trigger
language plpgsql security definer set search_path = '' as $$
begin
  insert into public.workspace_settings (workspace_id, org_id) values (new.id, new.org_id);
  return new;
end $$;
create trigger workspace_settings_default after insert on public.workspace
  for each row execute function app.create_workspace_settings();

alter table public.workspace_settings enable row level security;
create policy workspace_settings_read on public.workspace_settings for select to authenticated
  using (app.has_workspace_role(workspace_id, 'Viewer'));
grant select on public.workspace_settings to authenticated;
