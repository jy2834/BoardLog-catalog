-- Keep the already-linked hosted project and fresh installs on the same
-- validation contract.  This migration is intentionally idempotent because
-- the initial project was linked before the stricter validation review.

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
  if jsonb_typeof(p_payload->'sourceUrls') is distinct from 'array'
    or jsonb_array_length(p_payload->'sourceUrls') < 1 then
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
