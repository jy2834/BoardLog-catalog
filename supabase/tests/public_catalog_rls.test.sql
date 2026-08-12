begin;

create extension if not exists pgtap with schema extensions;
select plan(34);
create temporary table tap_results (result text not null);
grant insert, select on tap_results to anon, authenticated, service_role;

insert into tap_results select has_table('public', 'game_submissions', 'game_submissions exists');
insert into tap_results select has_table('public', 'moderation_events', 'moderation_events exists');
insert into tap_results select has_table('public', 'admin_users', 'admin_users exists');
insert into tap_results select has_table('public', 'service_status', 'service_status exists');
insert into tap_results select has_function('public', 'submit_game', array['jsonb', 'text'], 'submit_game exists');
insert into tap_results select has_function('public', 'submit_game_from_edge', array['uuid', 'uuid', 'jsonb', 'text'], 'submit_game_from_edge exists');
insert into tap_results select has_function('public', 'update_submission', array['uuid', 'jsonb', 'text'], 'update_submission exists');
insert into tap_results select has_function('public', 'review_submission', array['uuid', 'submission_status', 'jsonb', 'text'], 'review_submission exists');
insert into tap_results select is(
  (
    select count(*)::integer
    from pg_policies
    where schemaname = 'storage'
      and tablename = 'objects'
      and cmd = 'INSERT'
      and 'authenticated' = any(roles)
  ),
  0,
  'authenticated clients cannot upload submission images outside the edge'
);
insert into tap_results select is(
  (
    select count(*)::integer
    from pg_publication_tables
    where pubname = 'supabase_realtime'
      and schemaname = 'public'
      and tablename = 'game_submissions'
  ),
  1,
  'submission rows are published for Realtime changes'
);

insert into auth.users (id, aud, role, email, encrypted_password, email_confirmed_at, created_at, updated_at)
values
  ('11111111-1111-4111-8111-111111111111', 'authenticated', 'authenticated', 'owner@example.invalid', '', now(), now(), now()),
  ('22222222-2222-4222-8222-222222222222', 'authenticated', 'authenticated', 'other@example.invalid', '', now(), now(), now()),
  ('33333333-3333-4333-8333-333333333333', 'authenticated', 'authenticated', 'admin@example.invalid', '', now(), now(), now());

insert into public.admin_users (user_id) values ('33333333-3333-4333-8333-333333333333');

insert into tap_results select ok(
  not has_function_privilege('authenticated', 'public.submit_game(jsonb,text)', 'execute'),
  'authenticated clients cannot bypass Turnstile with direct submission RPC'
);

set local role service_role;
insert into tap_results select lives_ok(
  $$select public.submit_game_from_edge(
    '11111111-1111-4111-8111-111111111111'::uuid,
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'::uuid,
    '{"name":"테스트 게임","englishName":"Test Game","aliases":[],"minPlayers":2,"maxPlayers":4,"minPlayMinutes":30,"maxPlayMinutes":60,"tags":["FAMILY"],"weight":2.0,"yearPublished":2026,"entryType":"BASE_GAME","sourceUrls":["https://example.com/game"]}'::jsonb,
    '11111111-1111-4111-8111-111111111111/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa.webp'
  )$$,
  'edge can submit a privacy-safe game for an authenticated owner'
);

reset role;
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"11111111-1111-4111-8111-111111111111","role":"authenticated"}', true);
insert into tap_results select is((select count(*)::integer from public.my_game_submissions), 1, 'owner can read the submitter-safe status view');
insert into tap_results select is(
  (
    select count(id)::integer
    from public.game_submissions
    where id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
  ),
  1,
  'owner can select Realtime-safe columns from the base row'
);
insert into tap_results select ok(
  not has_column_privilege('authenticated', 'public.game_submissions', 'admin_note', 'select'),
  'authenticated owners cannot select admin notes'
);
insert into tap_results select throws_ok(
  $$select admin_note
    from public.game_submissions
    where id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'$$,
  '42501',
  'permission denied for table game_submissions',
  'owner base-row select cannot expose an admin note'
);

reset role;
create temporary table test_submission_ids as
select id from public.game_submissions order by created_at limit 1;
grant select on test_submission_ids to authenticated;

set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"22222222-2222-4222-8222-222222222222","role":"authenticated"}', true);
insert into tap_results select is((select count(*)::integer from public.my_game_submissions), 0, 'another user cannot read the pending submission');
insert into tap_results select is(
  (
    select count(id)::integer
    from public.game_submissions
    where id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
  ),
  0,
  'another user cannot select a cross-owner base row'
);
insert into tap_results select is((select count(*)::integer from public.approved_catalog_games), 0, 'pending rows never enter the approved public view');

insert into tap_results select throws_ok(
  $$select public.review_submission(
    (select id from test_submission_ids limit 1),
    'APPROVED'::public.submission_status,
    '{}'::jsonb,
    'not allowed'
  )$$,
  '42501',
  'admin access required',
  'non-admin cannot review a submission'
);
insert into tap_results select throws_ok(
  $$select public.update_submission(
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'::uuid,
    '{"name":"교차 수정","englishName":"Cross-owner Update","aliases":[],"minPlayers":2,"maxPlayers":4,"minPlayMinutes":30,"maxPlayMinutes":60,"tags":["FAMILY"],"weight":2.0,"yearPublished":2026,"entryType":"BASE_GAME","sourceUrls":["https://example.com/game"]}'::jsonb,
    null
  )$$,
  '42501',
  'pending owned submission not found',
  'another user cannot update a cross-owner submission'
);

reset role;
update public.service_status set service_state = 'IMAGE_LIMITED' where singleton;
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"11111111-1111-4111-8111-111111111111","role":"authenticated"}', true);

insert into tap_results select lives_ok(
  $$select public.update_submission(
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'::uuid,
    '{"name":"수정된 게임","englishName":"Updated Game","aliases":[],"minPlayers":2,"maxPlayers":4,"minPlayMinutes":30,"maxPlayMinutes":60,"tags":["FAMILY"],"weight":2.0,"yearPublished":2026,"entryType":"BASE_GAME","sourceUrls":["https://example.com/game"]}'::jsonb,
    '11111111-1111-4111-8111-111111111111/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa.webp'
  )$$,
  'owner can reuse the exact existing image path while image submissions are limited'
);
insert into tap_results select is(
  (
    select public_game->>'name'
    from public.game_submissions
    where id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
  ),
  '수정된 게임',
  'image-limited metadata update is persisted'
);
insert into tap_results select is(
  (
    select image_object_path
    from public.game_submissions
    where id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
  ),
  '11111111-1111-4111-8111-111111111111/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa.webp',
  'image-limited metadata update preserves the existing image path'
);
insert into tap_results select throws_ok(
  $$select public.update_submission(
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'::uuid,
    '{"name":"교체 시도","englishName":"Replacement Attempt","aliases":[],"minPlayers":2,"maxPlayers":4,"minPlayMinutes":30,"maxPlayMinutes":60,"tags":["FAMILY"],"weight":2.0,"yearPublished":2026,"entryType":"BASE_GAME","sourceUrls":["https://example.com/game"]}'::jsonb,
    '11111111-1111-4111-8111-111111111111/bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb.webp'
  )$$,
  '55000',
  'image submissions are unavailable',
  'owner cannot replace an image path while image submissions are limited'
);

reset role;
update public.service_status set service_state = 'SUBMISSION_CLOSED' where singleton;
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"11111111-1111-4111-8111-111111111111","role":"authenticated"}', true);
insert into tap_results select throws_ok(
  $$select public.update_submission(
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'::uuid,
    '{"name":"닫힘","englishName":"Closed","aliases":[],"minPlayers":2,"maxPlayers":4,"minPlayMinutes":30,"maxPlayMinutes":60,"tags":["FAMILY"],"weight":2.0,"yearPublished":2026,"entryType":"BASE_GAME","sourceUrls":["https://example.com/game"]}'::jsonb,
    null
  )$$,
  '55000',
  'submission updates are unavailable',
  'submission-closed state blocks owner updates'
);

reset role;
update public.service_status set service_state = 'MAINTENANCE' where singleton;
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"11111111-1111-4111-8111-111111111111","role":"authenticated"}', true);
insert into tap_results select throws_ok(
  $$select public.update_submission(
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'::uuid,
    '{"name":"점검","englishName":"Maintenance","aliases":[],"minPlayers":2,"maxPlayers":4,"minPlayMinutes":30,"maxPlayMinutes":60,"tags":["FAMILY"],"weight":2.0,"yearPublished":2026,"entryType":"BASE_GAME","sourceUrls":["https://example.com/game"]}'::jsonb,
    null
  )$$,
  '55000',
  'submission updates are unavailable',
  'maintenance state blocks owner updates'
);

reset role;
update public.service_status set service_state = 'NORMAL' where singleton;
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"11111111-1111-4111-8111-111111111111","role":"authenticated"}', true);
insert into tap_results select throws_ok(
  $$select public.update_submission(
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'::uuid,
    '{"name":"일반 교체 시도","englishName":"Normal Replacement Attempt","aliases":[],"minPlayers":2,"maxPlayers":4,"minPlayMinutes":30,"maxPlayMinutes":60,"tags":["FAMILY"],"weight":2.0,"yearPublished":2026,"entryType":"BASE_GAME","sourceUrls":["https://example.com/game"]}'::jsonb,
    '11111111-1111-4111-8111-111111111111/bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb.webp'
  )$$,
  '22023',
  'invalid image path',
  'owner cannot replace an image path during normal operation'
);

reset role;
select set_config('request.jwt.claims', '{"sub":"33333333-3333-4333-8333-333333333333","role":"authenticated"}', true);
set local role authenticated;
insert into tap_results select is(
  (select count(*)::integer from public.admin_game_submissions),
  1,
  'admin retains access to the full moderation view'
);

insert into tap_results select lives_ok(
  $$select public.review_submission(
    (select id from test_submission_ids limit 1),
    'APPROVED'::public.submission_status,
    '{"key":"community-test-game","name":"테스트 게임","englishName":"Test Game","aliases":[],"minPlayers":2,"maxPlayers":4,"minPlayMinutes":30,"maxPlayMinutes":60,"tags":["FAMILY"],"bggId":null,"imageUrl":"https://example.com/game.webp","publicRating":null,"weight":2.0,"listPriceWon":null,"priceKind":"UNAVAILABLE","yearPublished":2026,"koreanEditionYear":null,"catalogSource":"COMMUNITY","entryType":"BASE_GAME","sourceUrls":["https://example.com/game"],"originSubmissionId":"11111111-2222-4333-8444-555555555555","publishedAt":"2026-08-12T00:00:00Z"}'::jsonb,
    'approved'
  )$$,
  'admin can approve a submission'
);

insert into tap_results select is((select count(*)::integer from public.approved_catalog_games), 1, 'approved rows enter the approved public view');
reset role;
insert into tap_results select is((select count(*)::integer from public.moderation_events where action = 'APPROVED'), 1, 'approval creates an audit event');

set local role anon;
select set_config('request.jwt.claims', '{"role":"anon"}', true);
insert into tap_results select is((select count(*)::integer from public.approved_catalog_games), 1, 'anon can read only approved catalog rows');
insert into tap_results select is((select count(service_state)::integer from public.service_status), 1, 'anon can read public service status columns');

reset role;
insert into tap_results select * from finish();
do $$
declare
  v_failures text;
begin
  select string_agg(result, E'\n') into v_failures
  from tap_results
  where result like 'not ok %';
  if v_failures is not null then
    raise exception 'pgTAP failures:%', E'\n' || v_failures;
  end if;
end;
$$;
select 'ok - all 34 pgTAP assertions passed' as result;
rollback;
