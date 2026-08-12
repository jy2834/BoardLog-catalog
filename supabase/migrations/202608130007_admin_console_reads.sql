-- The public web bundle uses only the publishable API key and an authenticated
-- admin session. Expose the two read models the moderation console needs while
-- keeping free-tier metrics and audit events invisible to non-admin users.

create or replace view public.admin_service_status
with (security_barrier = true)
as
select usage_level, service_state, last_verified_at, metrics, operator_message, updated_at
from public.service_status
where public.is_catalog_admin();

create or replace view public.admin_moderation_events
with (security_barrier = true)
as
select id, submission_id, action, detail, created_at
from public.moderation_events
where public.is_catalog_admin();

revoke all on public.admin_service_status from anon, authenticated;
revoke all on public.admin_moderation_events from anon, authenticated;
grant select on public.admin_service_status to authenticated;
grant select on public.admin_moderation_events to authenticated;

-- The web form requires a reason before opening the rejection confirmation,
-- and the database repeats that invariant so direct RPC callers cannot clear
-- the submitter-visible explanation.
create or replace function public.review_submission(
  p_id uuid,
  p_decision public.submission_status,
  p_public_game jsonb,
  p_note text
)
returns void
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_admin uuid := auth.uid();
  v_owner uuid;
begin
  if not public.is_catalog_admin() then
    raise exception using errcode = '42501', message = 'admin access required';
  end if;
  if p_decision is null or p_decision not in ('APPROVED', 'REJECTED', 'MERGED') then
    raise exception using errcode = '22023', message = 'invalid review decision';
  end if;
  if p_decision = 'REJECTED' and nullif(btrim(p_note), '') is null then
    raise exception using errcode = '22023', message = 'rejection reason required';
  end if;
  if p_decision = 'APPROVED' and public.is_valid_reviewed_game(p_public_game) is not true then
    raise exception using errcode = '22023', message = 'invalid reviewed public game';
  end if;
  if p_decision = 'MERGED' and (
    jsonb_typeof(p_public_game) <> 'object'
    or public.contains_forbidden_public_key(p_public_game)
    or jsonb_typeof(p_public_game->'targetKey') <> 'string'
    or (p_public_game->>'targetKey') !~ '^[a-z0-9]+(?:-[a-z0-9]+)*$'
  ) then
    raise exception using errcode = '22023', message = 'invalid merge patch';
  end if;
  select owner_user_id into v_owner
  from public.game_submissions
  where id = p_id and status = 'PENDING'
  for update;
  if v_owner is null then
    raise exception using errcode = '22023', message = 'pending submission not found';
  end if;
  update public.game_submissions
  set status = p_decision,
      public_game = case when p_decision in ('APPROVED', 'MERGED') then p_public_game else public_game end,
      submitter_message = nullif(btrim(p_note), ''),
      admin_note = nullif(btrim(p_note), ''),
      reviewer_user_id = v_admin,
      reviewed_at = now(),
      updated_at = now()
  where id = p_id;
  insert into public.moderation_events (submission_id, owner_user_id, actor_user_id, action, detail)
  values (p_id, v_owner, v_admin, p_decision::text, jsonb_build_object('noteProvided', nullif(btrim(p_note), '') is not null));
end;
$$;

revoke all on function public.review_submission(uuid, public.submission_status, jsonb, text) from public;
grant execute on function public.review_submission(uuid, public.submission_status, jsonb, text) to authenticated;
