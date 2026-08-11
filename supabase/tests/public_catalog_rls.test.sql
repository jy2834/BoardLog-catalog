begin;

create extension if not exists pgtap with schema extensions;
select plan(16);
create temporary table tap_results (result text not null);
grant insert, select on tap_results to anon, authenticated;

insert into tap_results select has_table('public', 'game_submissions', 'game_submissions exists');
insert into tap_results select has_table('public', 'moderation_events', 'moderation_events exists');
insert into tap_results select has_table('public', 'admin_users', 'admin_users exists');
insert into tap_results select has_table('public', 'service_status', 'service_status exists');
insert into tap_results select has_function('public', 'submit_game', array['jsonb', 'text'], 'submit_game exists');
insert into tap_results select has_function('public', 'review_submission', array['uuid', 'submission_status', 'jsonb', 'text'], 'review_submission exists');

insert into auth.users (id, aud, role, email, encrypted_password, email_confirmed_at, created_at, updated_at)
values
  ('11111111-1111-4111-8111-111111111111', 'authenticated', 'authenticated', 'owner@example.invalid', '', now(), now(), now()),
  ('22222222-2222-4222-8222-222222222222', 'authenticated', 'authenticated', 'other@example.invalid', '', now(), now(), now()),
  ('33333333-3333-4333-8333-333333333333', 'authenticated', 'authenticated', 'admin@example.invalid', '', now(), now(), now());

insert into public.admin_users (user_id) values ('33333333-3333-4333-8333-333333333333');

set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"11111111-1111-4111-8111-111111111111","role":"authenticated"}', true);

insert into tap_results select lives_ok(
  $$select public.submit_game(
    '{"name":"테스트 게임","englishName":"Test Game","aliases":[],"minPlayers":2,"maxPlayers":4,"minPlayMinutes":30,"maxPlayMinutes":60,"tags":["FAMILY"],"weight":2.0,"yearPublished":2026,"entryType":"BASE_GAME","sourceUrls":["https://example.com/game"]}'::jsonb,
    null
  )$$,
  'owner can submit a privacy-safe game'
);

insert into tap_results select is((select count(*)::integer from public.my_game_submissions), 1, 'owner can read the submitter-safe status view');

reset role;
create temporary table test_submission_ids as
select id from public.game_submissions order by created_at limit 1;
grant select on test_submission_ids to authenticated;

set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"22222222-2222-4222-8222-222222222222","role":"authenticated"}', true);
insert into tap_results select is((select count(*)::integer from public.my_game_submissions), 0, 'another user cannot read the pending submission');
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

reset role;
select set_config('request.jwt.claims', '{"sub":"33333333-3333-4333-8333-333333333333","role":"authenticated"}', true);
set local role authenticated;

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
select 'ok - all 16 pgTAP assertions passed' as result;
rollback;
