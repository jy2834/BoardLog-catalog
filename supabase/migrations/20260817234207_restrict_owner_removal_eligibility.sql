create or replace function public.request_submission_removal(p_id uuid)
returns void
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_owner uuid := auth.uid();
begin
  if v_owner is null then
    raise exception using errcode = '42501', message = 'authenticated user required';
  end if;

  update public.game_submissions
  set visibility = 'REMOVAL_REQUESTED',
      removal_requested_at = now(),
      updated_at = now()
  where id = p_id
    and owner_user_id = v_owner
    and status in ('PENDING', 'APPROVED')
    and visibility = 'PUBLIC';
  if not found then
    raise exception using errcode = '42501', message = 'owned public removable submission not found';
  end if;

  insert into public.moderation_events (submission_id, owner_user_id, actor_user_id, action)
  values (p_id, v_owner, v_owner, 'REMOVAL_REQUESTED');
end;
$$;

create or replace function public.withdraw_submission(p_id uuid)
returns void
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  perform public.request_submission_removal(p_id);
end;
$$;

revoke all on function public.request_submission_removal(uuid) from public, anon;
revoke all on function public.withdraw_submission(uuid) from public, anon;
grant execute on function public.request_submission_removal(uuid) to authenticated;
grant execute on function public.withdraw_submission(uuid) to authenticated;
