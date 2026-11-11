begin;
create extension if not exists pgtap with schema extensions;
select plan(1);
select pass('skeleton');
select * from finish();
rollback;
