-- Uploads are accepted only by the bounded Edge Function.  Allowing every
-- authenticated anonymous account to write directly would bypass Turnstile
-- and consume the Free-plan storage quota without creating a submission.

drop policy if exists "owners upload submission images" on storage.objects;
drop policy if exists "owners read submission images" on storage.objects;
drop policy if exists "owners delete submission images" on storage.objects;

create policy "admins read submission images" on storage.objects
for select to authenticated
using (
  bucket_id = 'submission-images'
  and public.is_catalog_admin()
);

create policy "admins delete submission images" on storage.objects
for delete to authenticated
using (
  bucket_id = 'submission-images'
  and public.is_catalog_admin()
);
