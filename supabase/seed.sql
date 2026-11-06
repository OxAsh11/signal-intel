-- Local / CI seed only (applied by `supabase db reset`). Never run against production.
-- Two unrelated demo industries from day one (PRD Part 9 rule). Briefs are seeded once the
-- M1 migration lands; this seeds the tenants they will live in.
insert into auth.users (id, instance_id, aud, role, email, raw_user_meta_data, email_confirmed_at, created_at, updated_at)
values
  ('11111111-1111-4111-8111-111111111111', '00000000-0000-0000-0000-000000000000', 'authenticated', 'authenticated',
   'owner@demo-cad.test', '{"full_name":"Demo CAD Owner"}', now(), now(), now()),
  ('22222222-2222-4222-8222-222222222222', '00000000-0000-0000-0000-000000000000', 'authenticated', 'authenticated',
   'owner@demo-coldchain.test', '{"full_name":"Demo Cold-chain Owner"}', now(), now(), now())
on conflict (id) do nothing;

do $$
begin
  perform set_config('request.jwt.claims', '{"sub":"11111111-1111-4111-8111-111111111111","role":"authenticated"}', true);
  perform public.create_organisation('Demo · Design-software resellers', 'CAD adopters EU');
  perform set_config('request.jwt.claims', '{"sub":"22222222-2222-4222-8222-222222222222","role":"authenticated"}', true);
  perform public.create_organisation('Demo · Cold-chain equipment', 'Cold-storage operators');
end $$;
