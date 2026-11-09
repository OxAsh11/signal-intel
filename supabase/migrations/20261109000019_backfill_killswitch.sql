-- Phase 1d fix: seed kill-switch for the extract.backfill job type.
insert into public.job_type_config (job_type, enabled) values
  ('extract.backfill', true)
on conflict do nothing;
