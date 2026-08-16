begin;

create extension if not exists pgtap with schema extensions;
select plan(82);
create temporary table tap_results (result text not null);
grant insert, select on tap_results to anon, authenticated, service_role;

insert into tap_results select has_table('public', 'game_submissions', 'game_submissions exists');
insert into tap_results select has_table('public', 'game_submission_status_signals', 'owner status signal table exists');
insert into tap_results select has_table('public', 'moderation_events', 'moderation_events exists');
insert into tap_results select has_table('public', 'admin_users', 'admin_users exists');
insert into tap_results select has_table('public', 'service_status', 'service_status exists');
insert into tap_results select has_view('public', 'admin_service_status', 'admin usage status view exists');
insert into tap_results select has_view('public', 'admin_moderation_events', 'admin moderation audit view exists');
insert into tap_results select has_function('public', 'submit_game', array['jsonb', 'text'], 'submit_game exists');
insert into tap_results select has_function('public', 'submit_game_from_edge', array['uuid', 'uuid', 'jsonb', 'text'], 'submit_game_from_edge exists');
insert into tap_results select has_function('public', 'update_submission', array['uuid', 'jsonb', 'text'], 'update_submission exists');
insert into tap_results select has_function('public', 'review_submission', array['uuid', 'submission_status', 'jsonb', 'text'], 'review_submission exists');
insert into tap_results select has_function('public', 'catalog_usage_snapshot', array[]::text[], 'usage snapshot function exists');
insert into tap_results select has_function('public', 'apply_catalog_usage_status', array['usage_level', 'service_state', 'timestamp with time zone', 'jsonb'], 'usage status function exists');
insert into tap_results select has_function('public', 'catalog_prunable_images', array['timestamp with time zone', 'timestamp with time zone', 'integer'], 'image prune candidate function exists');
insert into tap_results select has_function('public', 'acknowledge_pruned_submission_images', array['text[]'], 'image prune acknowledgement function exists');
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
  0,
  'submission rows are not published for Realtime changes'
);
insert into tap_results select is(
  (
    select count(*)::integer
    from pg_publication_tables
    where pubname = 'supabase_realtime'
      and schemaname = 'public'
      and tablename = 'game_submission_status_signals'
  ),
  1,
  'status signals are published for Realtime changes'
);
insert into tap_results select ok(
  not has_table_privilege('authenticated', 'public.game_submissions', 'select'),
  'authenticated clients cannot select base submission rows'
);
insert into tap_results select ok(
  not has_table_privilege('authenticated', 'public.game_submission_status_signal_owners', 'select'),
  'authenticated clients cannot select private signal ownership mappings'
);
insert into tap_results select is(
  (
    select count(*)::integer
    from pg_publication_tables
    where pubname = 'supabase_realtime'
      and schemaname = 'public'
      and tablename = 'game_submission_status_signal_owners'
  ),
  0,
  'private signal ownership mappings are not published'
);
insert into tap_results select is(
  (
    select string_agg(a.attname, ',' order by a.attnum)
    from pg_index i
    join pg_attribute a
      on a.attrelid = i.indrelid
     and a.attnum = any(i.indkey)
    where i.indrelid = 'public.game_submission_status_signals'::regclass
      and i.indisprimary
  ),
  'signal_key',
  'status signal delete identity is only an opaque key'
);
insert into tap_results select is(
  (
    select count(*)::integer
    from information_schema.columns
    where table_schema = 'public'
      and table_name = 'game_submission_status_signals'
      and column_name in ('submission_id', 'owner_user_id')
  ),
  0,
  'status signal has no submission or owner identifier'
);
insert into tap_results select is(
  (
    select relreplident
    from pg_class
    where oid = 'public.game_submission_status_signals'::regclass
  ),
  'd'::"char",
  'status signal uses default primary-key-only delete identity'
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
insert into tap_results select ok(
  not has_function_privilege('authenticated', 'public.catalog_usage_snapshot()', 'execute'),
  'authenticated clients cannot read private project usage metrics'
);

set local role service_role;
insert into tap_results select ok(
  (public.catalog_usage_snapshot()->>'databaseBytes')::bigint > 0
    and (public.catalog_usage_snapshot()->>'storageBytes')::bigint >= 0,
  'edge secret role can read bounded database and storage usage metrics'
);
reset role;
update public.service_status
set service_state = 'MAINTENANCE', operator_message = 'planned', last_verified_at = '2026-08-13T00:00:00Z'
where singleton;
set local role service_role;
insert into tap_results select lives_ok(
  $$select public.apply_catalog_usage_status(
    'CRITICAL_95'::public.usage_level,
    'IMAGE_LIMITED'::public.service_state,
    '2026-08-13T00:01:00Z'::timestamptz,
    '{"databaseBytes":{"value":475000000,"limit":500000000,"ratio":0.95}}'::jsonb
  )$$,
  'usage monitor can apply a verified status report'
);
insert into tap_results select is(
  (select service_state::text || ':' || operator_message from public.service_status where singleton),
  'MAINTENANCE:planned',
  'usage monitor preserves operator maintenance and message'
);
reset role;
update public.service_status set service_state = 'NORMAL', operator_message = null where singleton;
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
create temporary table test_submission_ids as
select 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'::uuid as id;
grant select on test_submission_ids to authenticated;

create function pg_temp.test_submission_id()
returns uuid
language sql
stable
as $$
  select id from test_submission_ids limit 1
$$;

set local role anon;
select set_config('request.jwt.claims', '{"role":"anon"}', true);
insert into tap_results select is(
  (select count(*)::integer from public.public_unverified_catalog_metadata),
  1,
  'anon can read public unverified metadata without private image paths'
);
insert into tap_results select throws_ok(
  $$select image_object_path from public.public_unverified_catalog_games$$,
  '42501',
  'permission denied for view public_unverified_catalog_games',
  'anon cannot query the private unverified image view'
);
reset role;
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"11111111-1111-4111-8111-111111111111","role":"authenticated","is_anonymous":true}', true);
insert into tap_results select is(
  (select count(*)::integer from public.public_unverified_catalog_metadata),
  1,
  'authenticated anonymous user can read public unverified metadata'
);
insert into tap_results select throws_ok(
  $$select public.set_submission_visibility(test_submission_id(), 'HIDDEN', 'not admin')$$,
  '42501', 'admin access required', 'anonymous owner cannot hide public content'
);
insert into tap_results select lives_ok(
  $$select public.request_submission_removal(test_submission_id())$$,
  'owner can request public removal without deleting the row'
);

reset role;
set local role service_role;
insert into tap_results select lives_ok(
  $$select public.submit_game_from_edge(
    '11111111-1111-4111-8111-111111111111'::uuid,
    'cccccccc-cccc-4ccc-8ccc-cccccccccccc'::uuid,
    '{"name":"철회 게임","englishName":"Withdraw Game","aliases":[],"minPlayers":1,"maxPlayers":2,"minPlayMinutes":10,"maxPlayMinutes":20,"tags":["CARD"],"weight":1.0,"yearPublished":2026,"entryType":"BASE_GAME","sourceUrls":["https://example.com/withdraw"]}'::jsonb,
    null
  )$$,
  'edge can create a submission used to test private withdrawal signaling'
);

reset role;
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"11111111-1111-4111-8111-111111111111","role":"authenticated"}', true);
insert into tap_results select is((select count(*)::integer from public.my_game_submissions), 2, 'owner can read the submitter-safe status view');
insert into tap_results select is(
  (
    select count(signal_key)::integer
    from public.game_submission_status_signals
  ),
  1,
  'owner can read one opaque status signal'
);
insert into tap_results select throws_ok(
  $$select id from public.game_submissions$$,
  '42501',
  'permission denied for table game_submissions',
  'owner cannot bypass the safe status view'
);
insert into tap_results select lives_ok(
  $$select public.withdraw_submission('cccccccc-cccc-4ccc-8ccc-cccccccccccc'::uuid)$$,
  'withdrawal updates the owner signal without publishing a submission delete'
);
insert into tap_results select is(
  (select revision from public.game_submission_status_signals),
  3::bigint,
  'withdrawal advances the owner status signal'
);

reset role;
insert into public.game_submissions (
  id, owner_user_id, public_game, image_object_path, status, reviewed_at, updated_at
) values (
  'dddddddd-dddd-4ddd-8ddd-dddddddddddd',
  '11111111-1111-4111-8111-111111111111',
  '{"name":"거절 이미지","englishName":"Rejected Image","aliases":[],"minPlayers":1,"maxPlayers":1,"minPlayMinutes":10,"maxPlayMinutes":10,"tags":["CARD"],"weight":1.0,"yearPublished":2026,"entryType":"BASE_GAME","sourceUrls":["https://example.com/rejected"]}'::jsonb,
  '11111111-1111-4111-8111-111111111111/dddddddd-dddd-4ddd-8ddd-dddddddddddd.webp',
  'REJECTED',
  now() - interval '31 days',
  now() - interval '31 days'
);
insert into storage.objects (bucket_id, name, metadata, created_at, updated_at)
values (
  'submission-images',
  '11111111-1111-4111-8111-111111111111/dddddddd-dddd-4ddd-8ddd-dddddddddddd.webp',
  '{"size":123}'::jsonb,
  now() - interval '31 days',
  now() - interval '31 days'
);
set local role service_role;
insert into tap_results select is(
  (
    select count(*)::integer
    from public.catalog_prunable_images(
      now() - interval '30 days', now() - interval '1 day', 100
    )
    where "objectPath" = '11111111-1111-4111-8111-111111111111/dddddddd-dddd-4ddd-8ddd-dddddddddddd.webp'
      and reason = 'REJECTED_OLD'
  ),
  1,
  'old rejected cover is selected for bounded cleanup'
);
insert into tap_results select throws_ok(
  $$select public.acknowledge_pruned_submission_images(array[
    '11111111-1111-4111-8111-111111111111/dddddddd-dddd-4ddd-8ddd-dddddddddddd.webp'
  ])$$,
  '55000',
  'pruned image deletion is not verified',
  'cleanup cannot clear a reference before storage confirms deletion'
);
reset role;
insert into public.game_submissions (
  id, owner_user_id, public_game, image_object_path, status, reviewed_at, updated_at
) values (
  'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee',
  '11111111-1111-4111-8111-111111111111',
  '{"name":"삭제 확인 완료","englishName":"Deletion Confirmed","aliases":[],"minPlayers":1,"maxPlayers":1,"minPlayMinutes":10,"maxPlayMinutes":10,"tags":["CARD"],"weight":1.0,"yearPublished":2026,"entryType":"BASE_GAME","sourceUrls":["https://example.com/deleted"]}'::jsonb,
  '11111111-1111-4111-8111-111111111111/eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee.webp',
  'REJECTED',
  now() - interval '31 days',
  now() - interval '31 days'
);
set local role service_role;
insert into tap_results select lives_ok(
  $$select public.acknowledge_pruned_submission_images(array[
    '11111111-1111-4111-8111-111111111111/eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee.webp'
  ])$$,
  'secret cleanup can acknowledge deleted image paths'
);
reset role;
insert into tap_results select is(
  (
    select image_object_path
    from public.game_submissions
    where id = 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee'
  ),
  null,
  'cleanup acknowledgement clears only the rejected image reference'
);

set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"22222222-2222-4222-8222-222222222222","role":"authenticated"}', true);
insert into tap_results select is((select count(*)::integer from public.my_game_submissions), 0, 'another user cannot read the pending submission');
insert into tap_results select is(
  (
    select count(signal_key)::integer
    from public.game_submission_status_signals
  ),
  0,
  'another user cannot read a cross-owner status signal'
);
insert into tap_results select is((select count(*)::integer from public.approved_catalog_games), 0, 'pending rows never enter the approved public view');
insert into tap_results select is((select count(*)::integer from public.admin_service_status), 0, 'non-admin cannot read admin usage status');
insert into tap_results select is((select count(*)::integer from public.admin_moderation_events), 0, 'non-admin cannot read moderation audit events');

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
    from public.my_game_submissions
    where id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
  ),
  '수정된 게임',
  'image-limited metadata update is persisted'
);
insert into tap_results select is(
  (
    select image_object_path
    from public.my_game_submissions
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
select set_config('request.jwt.claims', '{"sub":"33333333-3333-4333-8333-333333333333","role":"authenticated","is_anonymous":true}', true);
set local role authenticated;
insert into tap_results select is(
  public.is_catalog_admin(),
  false,
  'anonymous admin membership is rejected'
);
insert into tap_results select is(
  (select count(*)::integer from public.admin_game_submissions),
  0,
  'anonymous admin membership cannot read the admin moderation view'
);
reset role;
select set_config('request.jwt.claims', '{"sub":"33333333-3333-4333-8333-333333333333","role":"authenticated","is_anonymous":false}', true);
set local role authenticated;
insert into tap_results select is(
  (
    select count(*)::integer
    from public.admin_game_submissions
    where id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
  ),
  1,
  'admin retains access to the full moderation view'
);
insert into tap_results select is(
  (select count(*)::integer from public.admin_service_status),
  1,
  'admin can read usage level for quota banners'
);
insert into tap_results select ok(
  (select count(*) from public.admin_moderation_events) > 0,
  'admin can read privacy-bounded moderation audit events'
);

insert into tap_results select throws_ok(
  $$select public.review_submission(
    (select id from test_submission_ids limit 1),
    'REJECTED'::public.submission_status,
    '{}'::jsonb,
    '   '
  )$$,
  '22023',
  'rejection reason required',
  'admin cannot reject without a submitter-visible reason'
);

insert into tap_results select throws_ok(
  $$select public.review_submission(
    (select id from test_submission_ids limit 1),
    'MERGED'::public.submission_status,
    '{"targetKey":"Bad Target"}'::jsonb,
    'duplicate'
  )$$,
  '22023',
  'invalid merge patch',
  'admin cannot merge to a malformed stable key'
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

insert into tap_results select is(
  (
    select count(*)::integer
    from public.approved_catalog_games
    where origin_submission_id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
  ),
  1,
  'approved test row enters the approved public view'
);
reset role;
insert into tap_results select is(
  (
    select count(*)::integer
    from public.moderation_events
    where submission_id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
      and action = 'APPROVED'
  ),
  1,
  'approval creates an audit event for the test row'
);

set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"33333333-3333-4333-8333-333333333333","role":"authenticated","is_anonymous":false}', true);
insert into tap_results select lives_ok(
  $$select public.set_submission_visibility('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'::uuid, 'HIDDEN', 'withdraw public row')$$,
  'admin can hide an approved public row'
);
reset role;
set local role anon;
select set_config('request.jwt.claims', '{"role":"anon"}', true);
insert into tap_results select is(
  (select count(*)::integer from public.public_catalog_suppressions where origin_submission_id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'),
  1,
  'hide creates a public suppression tombstone'
);
insert into tap_results select is(
  (select count(*)::integer from public.approved_catalog_games where origin_submission_id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'),
  0,
  'hidden approved row is absent from the public catalog view'
);
reset role;
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"33333333-3333-4333-8333-333333333333","role":"authenticated","is_anonymous":false}', true);
insert into tap_results select lives_ok(
  $$select public.set_submission_visibility('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'::uuid, 'PUBLIC', 'restore public row')$$,
  'admin can restore a hidden public row'
);
reset role;
set local role anon;
select set_config('request.jwt.claims', '{"role":"anon"}', true);
insert into tap_results select is(
  (select count(*)::integer from public.public_catalog_suppressions where origin_submission_id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'),
  0,
  'restore removes the public suppression tombstone'
);
insert into tap_results select is(
  (select count(*)::integer from public.approved_catalog_games where origin_submission_id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'),
  1,
  'restored approved row re-enters the public catalog view'
);
reset role;
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"33333333-3333-4333-8333-333333333333","role":"authenticated","is_anonymous":false}', true);
insert into tap_results select lives_ok(
  $$select * from public.prepare_submission_delete('cccccccc-cccc-4ccc-8ccc-cccccccccccc'::uuid, 'delete requested')$$,
  'admin can prepare a hidden submission for deletion'
);
reset role;
set local role anon;
select set_config('request.jwt.claims', '{"role":"anon"}', true);
insert into tap_results select is(
  (select count(*)::integer from public.public_catalog_suppressions where origin_submission_id = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc'),
  1,
  'delete preparation creates a public suppression tombstone'
);
reset role;
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"33333333-3333-4333-8333-333333333333","role":"authenticated","is_anonymous":false}', true);
insert into tap_results select lives_ok(
  $$select public.finalize_submission_delete('cccccccc-cccc-4ccc-8ccc-cccccccccccc'::uuid)$$,
  'admin can finalize a prepared submission deletion'
);
reset role;
insert into tap_results select is(
  (select count(*)::integer from public.game_submissions where id = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc'),
  0,
  'finalized deletion removes the submission row'
);

set local role anon;
select set_config('request.jwt.claims', '{"role":"anon"}', true);
insert into tap_results select is(
  (select count(*)::integer from public.public_catalog_suppressions where origin_submission_id = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc'),
  1,
  'finalized deletion retains the public suppression tombstone'
);
insert into tap_results select is(
  (
    select count(*)::integer
    from public.approved_catalog_games
    where origin_submission_id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
  ),
  1,
  'anon can read the approved test row'
);
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
select 'ok - all 82 pgTAP assertions passed' as result;
rollback;
