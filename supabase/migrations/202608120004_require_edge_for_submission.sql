-- Submission creation must pass the Edge Function's Turnstile and request
-- bounds.  Authenticated clients retain owner-scoped update/withdraw RPCs but
-- can no longer call either creation RPC directly.

revoke execute on function public.submit_game(jsonb, text) from authenticated;
revoke execute on function public.submit_game_with_id(uuid, jsonb, text) from authenticated;
grant execute on function public.submit_game(jsonb, text) to service_role;
grant execute on function public.submit_game_with_id(uuid, jsonb, text) to service_role;

create or replace function public.submit_game_from_edge(
  p_owner_user_id uuid,
  p_submission_id uuid,
  p_payload jsonb,
  p_image_path text default null
)
returns uuid
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_state public.service_state;
begin
  if p_owner_user_id is null
    or not exists (select 1 from auth.users where id = p_owner_user_id) then
    raise exception using errcode = '42501', message = 'authenticated owner required';
  end if;
  if p_submission_id is null then
    raise exception using errcode = '22023', message = 'submission id required';
  end if;

  select service_state into v_state
  from public.service_status
  where singleton;
  if v_state in ('SUBMISSION_CLOSED', 'MAINTENANCE') then
    raise exception using errcode = '55000', message = 'new submissions are unavailable';
  end if;
  if v_state = 'IMAGE_LIMITED' and p_image_path is not null then
    raise exception using errcode = '55000', message = 'image submissions are unavailable';
  end if;
  if octet_length(p_payload::text) > 32 * 1024
    or public.is_valid_submission_payload(p_payload) is not true then
    raise exception using errcode = '22023', message = 'invalid public game payload';
  end if;

  if p_image_path is not null and p_image_path not in (
    p_owner_user_id::text || '/' || p_submission_id::text || '.jpg',
    p_owner_user_id::text || '/' || p_submission_id::text || '.webp'
  ) then
    raise exception using errcode = '22023', message = 'invalid image path';
  end if;
  if p_image_path is not null and (
    p_image_path not like p_owner_user_id::text || '/%'
    or p_image_path like '%..%'
    or octet_length(p_image_path) > 512
  ) then
    raise exception using errcode = '22023', message = 'invalid image path';
  end if;

  if (
    select count(*)
    from public.moderation_events
    where owner_user_id = p_owner_user_id
      and action = 'SUBMITTED'
      and created_at >= now() - interval '24 hours'
  ) >= 3 then
    raise exception using errcode = 'P0001', message = 'daily submission limit reached';
  end if;

  insert into public.game_submissions (id, owner_user_id, public_game, image_object_path)
  values (p_submission_id, p_owner_user_id, p_payload, p_image_path);
  insert into public.moderation_events (submission_id, owner_user_id, actor_user_id, action)
  values (p_submission_id, p_owner_user_id, p_owner_user_id, 'SUBMITTED');
  return p_submission_id;
end;
$$;

revoke all on function public.submit_game_from_edge(uuid, uuid, jsonb, text) from public;
grant execute on function public.submit_game_from_edge(uuid, uuid, jsonb, text) to service_role;
