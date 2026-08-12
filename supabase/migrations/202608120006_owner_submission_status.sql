-- Realtime cannot subscribe to the owner status view. Expose only the
-- submitter-safe base-table columns and let RLS restrict each session to its
-- own rows. The existing admin policy and admin view remain unchanged.

drop policy if exists "owners read own submission rows" on public.game_submissions;
create policy "owners read own submission rows" on public.game_submissions
for select to authenticated
using (owner_user_id = auth.uid());

revoke all on public.game_submissions from authenticated;
grant select (
  id,
  public_game,
  image_object_path,
  status,
  submitter_message,
  created_at,
  updated_at,
  reviewed_at
) on public.game_submissions to authenticated;

do $$
begin
  if exists (
    select 1 from pg_publication where pubname = 'supabase_realtime'
  ) and not exists (
    select 1
    from pg_publication_tables
    where pubname = 'supabase_realtime'
      and schemaname = 'public'
      and tablename = 'game_submissions'
  ) then
    alter publication supabase_realtime add table public.game_submissions;
  end if;
end;
$$;

create or replace function public.update_submission(
  p_id uuid,
  p_payload jsonb,
  p_image_path text default null
)
returns void
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_owner uuid := auth.uid();
  v_state public.service_state;
  v_existing_image_path text;
begin
  if v_owner is null then
    raise exception using errcode = '42501', message = 'authenticated user required';
  end if;

  select service_state into v_state
  from public.service_status
  where singleton;
  if v_state in ('SUBMISSION_CLOSED', 'MAINTENANCE') then
    raise exception using errcode = '55000', message = 'submission updates are unavailable';
  end if;

  if octet_length(p_payload::text) > 32 * 1024
    or public.is_valid_submission_payload(p_payload) is not true then
    raise exception using errcode = '22023', message = 'invalid public game payload';
  end if;
  if p_image_path is not null and (
    p_image_path not like auth.uid()::text || '/%'
    or p_image_path like '%..%'
  ) then
    raise exception using errcode = '22023', message = 'invalid image path';
  end if;

  select image_object_path into v_existing_image_path
  from public.game_submissions
  where id = p_id and owner_user_id = v_owner and status = 'PENDING'
  for update;
  if not found then
    raise exception using errcode = '42501', message = 'pending owned submission not found';
  end if;

  if p_image_path is not null
    and p_image_path is distinct from v_existing_image_path then
    if v_state = 'IMAGE_LIMITED' then
      raise exception using errcode = '55000', message = 'image submissions are unavailable';
    end if;
    raise exception using errcode = '22023', message = 'invalid image path';
  end if;

  update public.game_submissions
  set public_game = p_payload,
      image_object_path = p_image_path,
      updated_at = now()
  where id = p_id and owner_user_id = v_owner and status = 'PENDING';

  insert into public.moderation_events (submission_id, owner_user_id, actor_user_id, action)
  values (p_id, v_owner, v_owner, 'UPDATED');
end;
$$;
