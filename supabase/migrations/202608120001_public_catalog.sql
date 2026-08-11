create type public.submission_status as enum ('PENDING', 'APPROVED', 'REJECTED', 'MERGED');
create type public.usage_level as enum ('NORMAL', 'NOTICE_80', 'WARNING_90', 'CRITICAL_95', 'EXHAUSTED_100');
create type public.service_state as enum ('NORMAL', 'IMAGE_LIMITED', 'SUBMISSION_CLOSED', 'MAINTENANCE');

create table public.admin_users (
  user_id uuid primary key references auth.users(id) on delete cascade,
  created_at timestamptz not null default now()
);

create table public.service_status (
  singleton boolean primary key default true check (singleton),
  usage_level public.usage_level not null default 'NORMAL',
  service_state public.service_state not null default 'NORMAL',
  last_verified_at timestamptz not null default now(),
  metrics jsonb not null default '{}'::jsonb check (jsonb_typeof(metrics) = 'object'),
  operator_message text,
  updated_at timestamptz not null default now()
);

insert into public.service_status (singleton) values (true);

create table public.game_submissions (
  id uuid primary key default gen_random_uuid(),
  owner_user_id uuid not null references auth.users(id) on delete cascade,
  public_game jsonb not null check (jsonb_typeof(public_game) = 'object'),
  image_object_path text,
  status public.submission_status not null default 'PENDING',
  submitter_message text,
  admin_note text,
  reviewer_user_id uuid references auth.users(id) on delete set null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  reviewed_at timestamptz,
  exported_at timestamptz,
  check (octet_length(public_game::text) <= 32 * 1024),
  check (image_object_path is null or octet_length(image_object_path) <= 512)
);

create index game_submissions_owner_created_idx on public.game_submissions (owner_user_id, created_at desc);
create index game_submissions_status_created_idx on public.game_submissions (status, created_at);

create table public.moderation_events (
  id bigint generated always as identity primary key,
  submission_id uuid not null,
  owner_user_id uuid,
  actor_user_id uuid,
  action text not null check (action in ('SUBMITTED', 'UPDATED', 'WITHDRAWN', 'APPROVED', 'REJECTED', 'MERGED')),
  detail jsonb not null default '{}'::jsonb check (jsonb_typeof(detail) = 'object'),
  created_at timestamptz not null default now()
);

create index moderation_events_submission_idx on public.moderation_events (submission_id, created_at);
create index moderation_events_owner_submit_idx on public.moderation_events (owner_user_id, created_at)
  where action = 'SUBMITTED';

create or replace function public.contains_forbidden_public_key(p_value jsonb)
returns boolean
language plpgsql
immutable
strict
set search_path = public, pg_temp
as $$
declare
  v_key text;
  v_child jsonb;
begin
  if jsonb_typeof(p_value) = 'object' then
    for v_key, v_child in select key, value from jsonb_each(p_value)
    loop
      if v_key = any (array[
        'purchasePrice', 'basePrice', 'componentPrice', 'extraComponentsPrice',
        'organizerPrice', 'memo', 'reviewMemo', 'localPath', 'imageRef',
        'ownerId', 'ownerUserId', 'personalRating'
      ]) then
        return true;
      end if;
      if public.contains_forbidden_public_key(v_child) then
        return true;
      end if;
    end loop;
  elsif jsonb_typeof(p_value) = 'array' then
    for v_child in select value from jsonb_array_elements(p_value)
    loop
      if public.contains_forbidden_public_key(v_child) then
        return true;
      end if;
    end loop;
  end if;
  return false;
end;
$$;

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
  if jsonb_typeof(p_payload->'aliases') is distinct from 'array' or jsonb_array_length(p_payload->'aliases') > 20 then
    return false;
  end if;
  for v_alias in select value from jsonb_array_elements(p_payload->'aliases')
  loop
    if jsonb_typeof(v_alias) <> 'string' or char_length(v_alias #>> '{}') not between 1 and 200 then
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
  if p_payload ? 'yearPublished' and p_payload->'yearPublished' <> 'null'::jsonb and (
    jsonb_typeof(p_payload->'yearPublished') is distinct from 'number'
    or (p_payload->>'yearPublished') !~ '^[0-9]+$'
    or (p_payload->>'yearPublished')::integer not between 1900 and 2100
  ) then
    return false;
  end if;
  if p_payload ? 'entryType' and p_payload->>'entryType' not in ('BASE_GAME', 'EXPANSION') then
    return false;
  end if;
  if jsonb_typeof(p_payload->'sourceUrls') is distinct from 'array' or jsonb_array_length(p_payload->'sourceUrls') < 1 then
    return false;
  end if;
  for v_url in select value from jsonb_array_elements(p_payload->'sourceUrls')
  loop
    if jsonb_typeof(v_url) <> 'string' or (v_url #>> '{}') !~ '^https://[^[:space:]]+$' then
      return false;
    end if;
  end loop;
  return true;
exception
  when others then
    return false;
end;
$$;

create or replace function public.is_valid_reviewed_game(p_payload jsonb)
returns boolean
language sql
immutable
strict
set search_path = public, pg_temp
as $$
  select coalesce(
    jsonb_typeof(p_payload) = 'object'
    and not public.contains_forbidden_public_key(p_payload)
    and public.is_valid_submission_payload(
      p_payload - array[
        'key', 'bggId', 'imageUrl', 'publicRating', 'listPriceWon', 'priceKind',
        'catalogSource', 'originSubmissionId', 'publishedAt'
      ]
    )
    and jsonb_typeof(p_payload->'key') = 'string'
    and (p_payload->>'key') ~ '^[a-z0-9]+(?:-[a-z0-9]+)*$'
    and jsonb_typeof(p_payload->'name') = 'string'
    and char_length(btrim(p_payload->>'name')) between 1 and 200
    and p_payload->>'catalogSource' = 'COMMUNITY'
    and p_payload->>'entryType' in ('BASE_GAME', 'EXPANSION')
    and jsonb_typeof(p_payload->'sourceUrls') = 'array'
    and jsonb_array_length(p_payload->'sourceUrls') > 0
    and jsonb_typeof(p_payload->'imageUrl') = 'string'
    and (p_payload->>'imageUrl') ~ '^https://[^[:space:]]+$'
    and (
      p_payload->'bggId' = 'null'::jsonb
      or (jsonb_typeof(p_payload->'bggId') = 'number' and (p_payload->>'bggId') ~ '^[1-9][0-9]*$')
    )
    and (
      p_payload->'publicRating' = 'null'::jsonb
      or (
        jsonb_typeof(p_payload->'publicRating') = 'number'
        and (p_payload->>'publicRating')::numeric between 0 and 5
      )
    )
    and p_payload->>'priceKind' in ('DOMESTIC_LIST_PRICE', 'USD_MSRP_CONVERTED', 'UNAVAILABLE')
    and (
      (p_payload->>'priceKind' = 'UNAVAILABLE' and p_payload->'listPriceWon' = 'null'::jsonb)
      or (
        p_payload->>'priceKind' <> 'UNAVAILABLE'
        and jsonb_typeof(p_payload->'listPriceWon') = 'number'
        and (p_payload->>'listPriceWon') ~ '^[1-9][0-9]*$'
      )
    )
    and jsonb_typeof(p_payload->'originSubmissionId') = 'string'
    and (p_payload->>'originSubmissionId') ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    and jsonb_typeof(p_payload->'publishedAt') = 'string'
    and (p_payload->>'publishedAt') ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[^[:space:]]+Z$'
  , false)
$$;

create or replace function public.is_catalog_admin()
returns boolean
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select exists (select 1 from public.admin_users where user_id = auth.uid())
$$;

create or replace function public.submit_game(p_payload jsonb, p_image_path text default null)
returns uuid
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_owner uuid := auth.uid();
  v_submission_id uuid := gen_random_uuid();
  v_state public.service_state;
begin
  if v_owner is null then
    raise exception using errcode = '42501', message = 'authenticated user required';
  end if;
  select service_state into v_state from public.service_status where singleton;
  if v_state in ('SUBMISSION_CLOSED', 'MAINTENANCE') then
    raise exception using errcode = '55000', message = 'new submissions are unavailable';
  end if;
  if v_state = 'IMAGE_LIMITED' and p_image_path is not null then
    raise exception using errcode = '55000', message = 'image submissions are unavailable';
  end if;
  if octet_length(p_payload::text) > 32 * 1024 or not public.is_valid_submission_payload(p_payload) then
    raise exception using errcode = '22023', message = 'invalid public game payload';
  end if;
  if p_image_path is not null and (
    p_image_path not like auth.uid()::text || '/%'
    or p_image_path like '%..%'
    or octet_length(p_image_path) > 512
  ) then
    raise exception using errcode = '22023', message = 'invalid image path';
  end if;
  if (
    select count(*) from public.moderation_events
    where owner_user_id = v_owner
      and action = 'SUBMITTED'
      and created_at >= now() - interval '24 hours'
  ) >= 3 then
    raise exception using errcode = 'P0001', message = 'daily submission limit reached';
  end if;
  insert into public.game_submissions (id, owner_user_id, public_game, image_object_path)
  values (v_submission_id, v_owner, p_payload, p_image_path);
  insert into public.moderation_events (submission_id, owner_user_id, actor_user_id, action)
  values (v_submission_id, v_owner, v_owner, 'SUBMITTED');
  return v_submission_id;
end;
$$;

create or replace function public.update_submission(p_id uuid, p_payload jsonb, p_image_path text default null)
returns void
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_owner uuid := auth.uid();
  v_state public.service_state;
begin
  if v_owner is null then
    raise exception using errcode = '42501', message = 'authenticated user required';
  end if;
  select service_state into v_state from public.service_status where singleton;
  if v_state in ('SUBMISSION_CLOSED', 'MAINTENANCE') then
    raise exception using errcode = '55000', message = 'submission updates are unavailable';
  end if;
  if v_state = 'IMAGE_LIMITED' and p_image_path is not null then
    raise exception using errcode = '55000', message = 'image submissions are unavailable';
  end if;
  if octet_length(p_payload::text) > 32 * 1024 or not public.is_valid_submission_payload(p_payload) then
    raise exception using errcode = '22023', message = 'invalid public game payload';
  end if;
  if p_image_path is not null and (p_image_path not like auth.uid()::text || '/%' or p_image_path like '%..%') then
    raise exception using errcode = '22023', message = 'invalid image path';
  end if;
  update public.game_submissions
  set public_game = p_payload, image_object_path = p_image_path, updated_at = now()
  where id = p_id and owner_user_id = v_owner and status = 'PENDING';
  if not found then
    raise exception using errcode = '42501', message = 'pending owned submission not found';
  end if;
  insert into public.moderation_events (submission_id, owner_user_id, actor_user_id, action)
  values (p_id, v_owner, v_owner, 'UPDATED');
end;
$$;

create or replace function public.withdraw_submission(p_id uuid)
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
  delete from public.game_submissions
  where id = p_id and owner_user_id = v_owner and status = 'PENDING';
  if not found then
    raise exception using errcode = '42501', message = 'pending owned submission not found';
  end if;
  insert into public.moderation_events (submission_id, owner_user_id, actor_user_id, action)
  values (p_id, v_owner, v_owner, 'WITHDRAWN');
end;
$$;

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
  if p_decision = 'APPROVED' and public.is_valid_reviewed_game(p_public_game) is not true then
    raise exception using errcode = '22023', message = 'invalid reviewed public game';
  end if;
  if p_decision = 'MERGED' and (
    jsonb_typeof(p_public_game) <> 'object'
    or public.contains_forbidden_public_key(p_public_game)
    or jsonb_typeof(p_public_game->'targetKey') <> 'string'
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

create or replace view public.my_game_submissions
with (security_barrier = true)
as
select id, public_game, image_object_path, status, submitter_message, created_at, updated_at, reviewed_at
from public.game_submissions
where owner_user_id = auth.uid();

create or replace view public.admin_game_submissions
with (security_barrier = true)
as
select id, owner_user_id, public_game, image_object_path, status, submitter_message, admin_note,
       reviewer_user_id, created_at, updated_at, reviewed_at, exported_at
from public.game_submissions
where public.is_catalog_admin();

create or replace view public.approved_catalog_games
with (security_barrier = true)
as
select id as origin_submission_id, status, public_game, reviewed_at as published_at
from public.game_submissions
where status in ('APPROVED', 'MERGED');

alter table public.game_submissions enable row level security;
alter table public.moderation_events enable row level security;
alter table public.admin_users enable row level security;
alter table public.service_status enable row level security;

create policy "admins read submission rows" on public.game_submissions
for select to authenticated using (public.is_catalog_admin());
create policy "admins read moderation events" on public.moderation_events
for select to authenticated using (public.is_catalog_admin());
create policy "admins read admin membership" on public.admin_users
for select to authenticated using (public.is_catalog_admin());
create policy "public reads service status" on public.service_status
for select to anon, authenticated using (true);

revoke all on public.game_submissions from anon, authenticated;
revoke all on public.moderation_events from anon, authenticated;
revoke all on public.admin_users from anon, authenticated;
revoke all on public.service_status from anon, authenticated;
grant select (service_state, last_verified_at, operator_message) on public.service_status to anon, authenticated;

revoke all on public.my_game_submissions from anon, authenticated;
revoke all on public.admin_game_submissions from anon, authenticated;
revoke all on public.approved_catalog_games from anon, authenticated;
grant select on public.my_game_submissions to authenticated;
grant select on public.admin_game_submissions to authenticated;
grant select on public.approved_catalog_games to anon, authenticated;

revoke all on function public.contains_forbidden_public_key(jsonb) from public;
revoke all on function public.is_valid_submission_payload(jsonb) from public;
revoke all on function public.is_valid_reviewed_game(jsonb) from public;
revoke all on function public.is_catalog_admin() from public;
revoke all on function public.submit_game(jsonb, text) from public;
revoke all on function public.update_submission(uuid, jsonb, text) from public;
revoke all on function public.withdraw_submission(uuid) from public;
revoke all on function public.review_submission(uuid, public.submission_status, jsonb, text) from public;
grant execute on function public.is_catalog_admin() to authenticated;
grant execute on function public.submit_game(jsonb, text) to authenticated;
grant execute on function public.update_submission(uuid, jsonb, text) to authenticated;
grant execute on function public.withdraw_submission(uuid) to authenticated;
grant execute on function public.review_submission(uuid, public.submission_status, jsonb, text) to authenticated;

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('submission-images', 'submission-images', false, 2097152, array['image/jpeg', 'image/webp'])
on conflict (id) do update
set public = false,
    file_size_limit = excluded.file_size_limit,
    allowed_mime_types = excluded.allowed_mime_types;

create policy "owners upload submission images" on storage.objects
for insert to authenticated
with check (
  bucket_id = 'submission-images'
  and (storage.foldername(name))[1] = auth.uid()::text
);

create policy "owners read submission images" on storage.objects
for select to authenticated
using (
  bucket_id = 'submission-images'
  and ((storage.foldername(name))[1] = auth.uid()::text or public.is_catalog_admin())
);

create policy "owners delete submission images" on storage.objects
for delete to authenticated
using (
  bucket_id = 'submission-images'
  and ((storage.foldername(name))[1] = auth.uid()::text or public.is_catalog_admin())
);
