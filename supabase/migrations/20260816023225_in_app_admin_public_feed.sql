create type public.submission_visibility as enum ('PUBLIC', 'REMOVAL_REQUESTED', 'HIDDEN');

alter table public.game_submissions
  add column visibility public.submission_visibility not null default 'PUBLIC',
  add column removal_requested_at timestamptz,
  add column hidden_at timestamptz,
  add column hidden_reason text;

create table public.catalog_suppressions (
  origin_submission_id uuid primary key,
  actor_user_id uuid not null references auth.users(id) on delete restrict,
  reason text not null check (char_length(btrim(reason)) between 1 and 500),
  created_at timestamptz not null default now()
);

alter table public.catalog_suppressions enable row level security;

alter table public.moderation_events
  drop constraint if exists moderation_events_action_check,
  add constraint moderation_events_action_check check (
    action in (
      'SUBMITTED', 'UPDATED', 'WITHDRAWN', 'APPROVED', 'REJECTED', 'MERGED',
      'REMOVAL_REQUESTED', 'HIDDEN', 'RESTORED', 'DELETED'
    )
  );

create or replace function public.is_valid_submission_payload(p_payload jsonb)
returns boolean
language plpgsql
immutable
strict
set search_path = public, pg_temp
as $$
declare
  v_tag jsonb;
  v_url jsonb;
  v_alias jsonb;
  v_key text;
  v_allowed_tags constant text[] := array[
    'STRATEGY', 'PARTY', 'FAMILY', 'COOPERATIVE', 'DEDUCTION', 'SOCIAL_DEDUCTION',
    'MURDER_MYSTERY', 'BLUFFING', 'TWO_PLAYER', 'CARD', 'DECK_BUILDING',
    'TILE_PLACEMENT', 'WORKER_PLACEMENT', 'ENGINE_BUILDING', 'ECONOMIC', 'DICE',
    'WORD', 'TEAM', 'NEGOTIATION', 'ASYMMETRIC', 'ADVENTURE', 'CIVILIZATION',
    'ROUTE_BUILDING', 'TRICK_TAKING'
  ];
begin
  if jsonb_typeof(p_payload) <> 'object' or public.contains_forbidden_public_key(p_payload) then
    return false;
  end if;

  for v_key in select jsonb_object_keys(p_payload)
  loop
    if not (v_key = any (array[
      'name', 'englishName', 'aliases', 'minPlayers', 'maxPlayers',
      'minPlayMinutes', 'maxPlayMinutes', 'tags', 'weight', 'yearPublished',
      'koreanEditionYear', 'entryType', 'sourceUrls', 'bggId', 'publicRating'
    ])) then
      return false;
    end if;
  end loop;

  if p_payload ?| array['listPriceWon', 'priceKind'] then
    return false;
  end if;
  if jsonb_typeof(p_payload->'name') is distinct from 'string'
    or char_length(btrim(p_payload->>'name')) not between 1 and 200
    or p_payload->>'name' <> btrim(p_payload->>'name') then
    return false;
  end if;
  if p_payload ? 'englishName' and (
    jsonb_typeof(p_payload->'englishName') is distinct from 'string'
    or char_length(p_payload->>'englishName') > 200
    or p_payload->>'englishName' <> btrim(p_payload->>'englishName')
  ) then
    return false;
  end if;
  if jsonb_typeof(p_payload->'aliases') is distinct from 'array'
    or jsonb_array_length(p_payload->'aliases') > 20 then
    return false;
  end if;
  for v_alias in select value from jsonb_array_elements(p_payload->'aliases')
  loop
    if jsonb_typeof(v_alias) <> 'string'
      or char_length(v_alias #>> '{}') not between 1 and 200
      or (v_alias #>> '{}') <> btrim(v_alias #>> '{}') then
      return false;
    end if;
  end loop;

  if jsonb_typeof(p_payload->'minPlayers') is distinct from 'number'
    or jsonb_typeof(p_payload->'maxPlayers') is distinct from 'number'
    or (p_payload->>'minPlayers') !~ '^[0-9]+$'
    or (p_payload->>'maxPlayers') !~ '^[0-9]+$'
    or (p_payload->>'minPlayers')::integer not between 1 and 100
    or (p_payload->>'maxPlayers')::integer not between (p_payload->>'minPlayers')::integer and 100 then
    return false;
  end if;
  if jsonb_typeof(p_payload->'minPlayMinutes') is distinct from 'number'
    or jsonb_typeof(p_payload->'maxPlayMinutes') is distinct from 'number'
    or (p_payload->>'minPlayMinutes') !~ '^[0-9]+$'
    or (p_payload->>'maxPlayMinutes') !~ '^[0-9]+$'
    or (p_payload->>'minPlayMinutes')::integer not between 1 and 10080
    or (p_payload->>'maxPlayMinutes')::integer not between (p_payload->>'minPlayMinutes')::integer and 10080 then
    return false;
  end if;
  if jsonb_typeof(p_payload->'tags') is distinct from 'array'
    or jsonb_array_length(p_payload->'tags') not between 1 and 12 then
    return false;
  end if;
  for v_tag in select value from jsonb_array_elements(p_payload->'tags')
  loop
    if jsonb_typeof(v_tag) <> 'string' or not ((v_tag #>> '{}') = any (v_allowed_tags)) then
      return false;
    end if;
  end loop;

  if p_payload ? 'weight' and p_payload->'weight' <> 'null'::jsonb and (
    jsonb_typeof(p_payload->'weight') is distinct from 'number'
    or (p_payload->>'weight')::numeric not between 0.5 and 5.0
  ) then
    return false;
  end if;
  if p_payload ? 'publicRating' and p_payload->'publicRating' <> 'null'::jsonb and (
    jsonb_typeof(p_payload->'publicRating') is distinct from 'number'
    or (p_payload->>'publicRating')::numeric not between 0 and 5
  ) then
    return false;
  end if;
  if p_payload ? 'bggId' and p_payload->'bggId' <> 'null'::jsonb and (
    jsonb_typeof(p_payload->'bggId') is distinct from 'number'
    or (p_payload->>'bggId') !~ '^[1-9][0-9]*$'
    or (p_payload->>'bggId')::numeric > 2147483647
  ) then
    return false;
  end if;
  if p_payload ? 'yearPublished' and p_payload->'yearPublished' <> 'null'::jsonb and (
    jsonb_typeof(p_payload->'yearPublished') is distinct from 'number'
    or (p_payload->>'yearPublished') !~ '^[0-9]+$'
    or (p_payload->>'yearPublished')::integer not between 1900 and 2100
  ) then
    return false;
  end if;
  if p_payload ? 'koreanEditionYear' and p_payload->'koreanEditionYear' <> 'null'::jsonb and (
    jsonb_typeof(p_payload->'koreanEditionYear') is distinct from 'number'
    or (p_payload->>'koreanEditionYear') !~ '^[0-9]+$'
    or (p_payload->>'koreanEditionYear')::integer not between 1900 and 2100
  ) then
    return false;
  end if;
  if p_payload ? 'entryType' and p_payload->>'entryType' not in ('BASE_GAME', 'EXPANSION') then
    return false;
  end if;
  if p_payload ? 'sourceUrls' then
    if jsonb_typeof(p_payload->'sourceUrls') is distinct from 'array'
      or jsonb_array_length(p_payload->'sourceUrls') > 10 then
      return false;
    end if;
    for v_url in select value from jsonb_array_elements(p_payload->'sourceUrls')
    loop
      if jsonb_typeof(v_url) <> 'string'
        or char_length(v_url #>> '{}') > 2048
        or (v_url #>> '{}') <> btrim(v_url #>> '{}')
        or (v_url #>> '{}') !~ '^https://[^[:space:]]+$' then
        return false;
      end if;
    end loop;
  end if;
  return true;
exception
  when others then
    return false;
end;
$$;

create or replace view public.public_unverified_catalog_games
with (security_invoker = true, security_barrier = true)
as
select
  id as origin_submission_id,
  ('community-' || replace(id::text, '-', '')) as catalog_key,
  public_game,
  image_object_path,
  status,
  created_at,
  updated_at
from public.game_submissions
where (status = 'PENDING' or (status = 'APPROVED' and exported_at is null))
  and visibility in ('PUBLIC', 'REMOVAL_REQUESTED');

create or replace view public.public_unverified_catalog_metadata
with (security_barrier = true)
as
select origin_submission_id, catalog_key, public_game, status, created_at, updated_at
from public.public_unverified_catalog_games;

revoke all on public.public_unverified_catalog_games from public, anon, authenticated;
revoke all on public.public_unverified_catalog_metadata from public, anon, authenticated;
grant select on public.public_unverified_catalog_metadata to anon, authenticated;

create or replace view public.public_catalog_suppressions
with (security_barrier = true)
as
select origin_submission_id from public.catalog_suppressions;

revoke all on public.catalog_suppressions from public, anon, authenticated;
revoke all on public.public_catalog_suppressions from public, anon, authenticated;
grant select on public.public_catalog_suppressions to anon, authenticated;

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
    and visibility <> 'HIDDEN';
  if not found then
    raise exception using errcode = '42501', message = 'owned visible submission not found';
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

create or replace function public.set_submission_visibility(
  p_id uuid,
  p_visibility public.submission_visibility,
  p_reason text
)
returns void
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_admin uuid := auth.uid();
  v_owner uuid;
  v_status public.submission_status;
  v_reason text := nullif(btrim(p_reason), '');
begin
  if not (
    auth.uid() is not null
    and coalesce((select auth.jwt()->>'is_anonymous')::boolean, true) is false
    and (select auth.jwt()->>'is_anonymous')::boolean is false
    and public.is_catalog_admin()
  ) then
    raise exception using errcode = '42501', message = 'admin access required';
  end if;
  if p_visibility not in ('PUBLIC', 'HIDDEN') then
    raise exception using errcode = '22023', message = 'invalid submission visibility';
  end if;
  if p_visibility = 'HIDDEN' and (v_reason is null or char_length(v_reason) > 500) then
    raise exception using errcode = '22023', message = 'hidden reason required';
  end if;

  select owner_user_id, status into v_owner, v_status
  from public.game_submissions
  where id = p_id
  for update;
  if v_owner is null then
    raise exception using errcode = '22023', message = 'submission not found';
  end if;

  if p_visibility = 'HIDDEN' then
    update public.game_submissions
    set visibility = 'HIDDEN',
        hidden_at = now(),
        hidden_reason = v_reason,
        updated_at = now()
    where id = p_id;
    insert into public.catalog_suppressions (origin_submission_id, actor_user_id, reason)
    values (p_id, v_admin, v_reason)
    on conflict (origin_submission_id) do update
    set actor_user_id = excluded.actor_user_id,
        reason = excluded.reason,
        created_at = now();
    insert into public.moderation_events (submission_id, owner_user_id, actor_user_id, action, detail)
    values (p_id, v_owner, v_admin, 'HIDDEN', jsonb_build_object('reasonProvided', true));
  else
    update public.game_submissions
    set visibility = 'PUBLIC',
        removal_requested_at = null,
        hidden_at = null,
        hidden_reason = null,
        exported_at = case when v_status = 'APPROVED' then null else exported_at end,
        updated_at = now()
    where id = p_id;
    delete from public.catalog_suppressions where origin_submission_id = p_id;
    insert into public.moderation_events (submission_id, owner_user_id, actor_user_id, action)
    values (p_id, v_owner, v_admin, 'RESTORED');
  end if;
end;
$$;

create or replace function public.prepare_submission_delete(p_id uuid, p_reason text)
returns table (submission_id uuid, image_object_path text)
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_admin uuid := auth.uid();
  v_owner uuid;
  v_image_object_path text;
  v_reason text := nullif(btrim(p_reason), '');
begin
  if not (
    auth.uid() is not null
    and coalesce((select auth.jwt()->>'is_anonymous')::boolean, true) is false
    and (select auth.jwt()->>'is_anonymous')::boolean is false
    and public.is_catalog_admin()
  ) then
    raise exception using errcode = '42501', message = 'admin access required';
  end if;
  if v_reason is null or char_length(v_reason) > 500 then
    raise exception using errcode = '22023', message = 'hidden reason required';
  end if;

  select owner_user_id, game_submissions.image_object_path into v_owner, v_image_object_path
  from public.game_submissions
  where id = p_id
  for update;
  if v_owner is null then
    raise exception using errcode = '22023', message = 'submission not found';
  end if;

  update public.game_submissions
  set visibility = 'HIDDEN',
      hidden_at = now(),
      hidden_reason = v_reason,
      updated_at = now()
  where id = p_id;
  insert into public.catalog_suppressions (origin_submission_id, actor_user_id, reason)
  values (p_id, v_admin, v_reason)
  on conflict (origin_submission_id) do update
  set actor_user_id = excluded.actor_user_id,
      reason = excluded.reason,
      created_at = now();
  insert into public.moderation_events (submission_id, owner_user_id, actor_user_id, action, detail)
  values (p_id, v_owner, v_admin, 'HIDDEN', jsonb_build_object('reasonProvided', true));

  return query select p_id, v_image_object_path;
end;
$$;

create or replace function public.finalize_submission_delete(p_id uuid)
returns void
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_admin uuid := auth.uid();
  v_owner uuid;
  v_visibility public.submission_visibility;
begin
  if not (
    auth.uid() is not null
    and coalesce((select auth.jwt()->>'is_anonymous')::boolean, true) is false
    and (select auth.jwt()->>'is_anonymous')::boolean is false
    and public.is_catalog_admin()
  ) then
    raise exception using errcode = '42501', message = 'admin access required';
  end if;

  select owner_user_id, visibility into v_owner, v_visibility
  from public.game_submissions
  where id = p_id
  for update;
  if v_owner is null then
    raise exception using errcode = '22023', message = 'submission not found';
  end if;
  if v_visibility <> 'HIDDEN' then
    raise exception using errcode = '22023', message = 'submission must be hidden before deletion';
  end if;

  insert into public.moderation_events (submission_id, owner_user_id, actor_user_id, action)
  values (p_id, v_owner, v_admin, 'DELETED');
  delete from public.game_submissions where id = p_id;
end;
$$;

create or replace view public.my_game_submissions
with (security_barrier = true)
as
select id, public_game, image_object_path, status, visibility, removal_requested_at,
       submitter_message, created_at, updated_at, reviewed_at
from public.game_submissions
where owner_user_id = auth.uid();

create or replace view public.admin_game_submissions
with (security_barrier = true)
as
select id, owner_user_id, public_game, image_object_path, status, visibility,
       removal_requested_at, hidden_at, hidden_reason, submitter_message, admin_note,
       reviewer_user_id, created_at, updated_at, reviewed_at, exported_at
from public.game_submissions
where public.is_catalog_admin();

revoke all on function public.request_submission_removal(uuid) from public;
revoke all on function public.withdraw_submission(uuid) from public;
revoke all on function public.set_submission_visibility(uuid, public.submission_visibility, text) from public;
revoke all on function public.prepare_submission_delete(uuid, text) from public;
revoke all on function public.finalize_submission_delete(uuid) from public;
grant execute on function public.request_submission_removal(uuid) to authenticated;
grant execute on function public.withdraw_submission(uuid) to authenticated;
grant execute on function public.set_submission_visibility(uuid, public.submission_visibility, text) to authenticated;
grant execute on function public.prepare_submission_delete(uuid, text) to authenticated;
grant execute on function public.finalize_submission_delete(uuid) to authenticated;
