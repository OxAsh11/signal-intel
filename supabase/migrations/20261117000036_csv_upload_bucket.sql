-- CSV radar uploads (M2) save to Storage before the radar is created, but the bucket never
-- existed, so every upload failed. Private bucket; files live at uploads/<workspace id>/<file>.
-- Editors of that workspace may upload and read them back; the worker reads with the service role.
-- Skipped where there is no Storage (the in-memory test database).

do $migration$
begin
  if to_regclass('storage.buckets') is null then
    raise notice 'storage schema not present; skipping the signal-uploads bucket';
    return;
  end if;

  insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
  values ('signal-uploads', 'signal-uploads', false, 10485760,
          array['text/csv', 'application/vnd.ms-excel', 'text/plain'])
  on conflict (id) do nothing;

  execute $policy$
    create function app.upload_workspace(p_name text) returns uuid
    language sql immutable set search_path = '' as $f$
      select case
        when split_part(p_name, '/', 1) = 'uploads'
         and split_part(p_name, '/', 2) ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
        then split_part(p_name, '/', 2)::uuid
      end
    $f$
  $policy$;
  grant execute on function app.upload_workspace(text) to authenticated;

  execute $policy$
    create policy signal_uploads_insert on storage.objects for insert to authenticated
      with check (bucket_id = 'signal-uploads'
                  and app.has_workspace_role(app.upload_workspace(name), 'Editor'))
  $policy$;
  execute $policy$
    create policy signal_uploads_read on storage.objects for select to authenticated
      using (bucket_id = 'signal-uploads'
             and app.has_workspace_role(app.upload_workspace(name), 'Editor'))
  $policy$;
end
$migration$;
