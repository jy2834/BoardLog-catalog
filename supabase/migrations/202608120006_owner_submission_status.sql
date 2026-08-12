-- Realtime DELETE events cannot be owner-filtered with RLS. Keep submission
-- rows out of the publication and publish only an opaque, owner-scoped
-- invalidation signal. Clients refetch the existing submitter-safe view after
-- receiving their signal.

drop policy if exists "owners read own submission rows" on public.game_submissions;
revoke all on public.game_submissions from authenticated;

create table public.game_submission_status_signal_owners (
  owner_user_id uuid primary key references auth.users(id) on delete cascade,
  signal_key uuid unique not null default gen_random_uuid()
);

create table public.game_submission_status_signals (
  signal_key uuid primary key default gen_random_uuid(),
  revision bigint not null default 1 check (revision > 0),
  updated_at timestamptz not null default now()
);

alter table public.game_submission_status_signal_owners enable row level security;
alter table public.game_submission_status_signals enable row level security;
alter table public.game_submission_status_signals replica identity default;

create or replace function public.owns_game_submission_status_signal(p_signal_key uuid)
returns boolean
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select exists (
    select 1
    from public.game_submission_status_signal_owners
    where signal_key = p_signal_key
      and owner_user_id = auth.uid()
  )
$$;

create policy "owners read own submission status signal"
on public.game_submission_status_signals
for select to authenticated
using (public.owns_game_submission_status_signal(signal_key));

revoke all on public.game_submission_status_signal_owners from anon, authenticated;
revoke all on public.game_submission_status_signals from anon, authenticated;
revoke all on function public.owns_game_submission_status_signal(uuid) from public;
grant execute on function public.owns_game_submission_status_signal(uuid) to authenticated;
grant select (signal_key, revision, updated_at)
on public.game_submission_status_signals to authenticated;

create or replace function public.bump_game_submission_status_signal()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_owner uuid;
  v_signal_key uuid;
begin
  if tg_op = 'DELETE' then
    v_owner := old.owner_user_id;
  else
    v_owner := new.owner_user_id;
  end if;

  insert into public.game_submission_status_signal_owners (owner_user_id)
  values (v_owner)
  on conflict (owner_user_id) do nothing;

  select signal_key into strict v_signal_key
  from public.game_submission_status_signal_owners
  where owner_user_id = v_owner;

  insert into public.game_submission_status_signals (signal_key)
  values (v_signal_key)
  on conflict (signal_key) do update
  set revision = game_submission_status_signals.revision + 1,
      updated_at = now();

  return null;
end;
$$;

revoke all on function public.bump_game_submission_status_signal() from public;

insert into public.game_submission_status_signal_owners (owner_user_id)
select distinct owner_user_id
from public.game_submissions
on conflict (owner_user_id) do nothing;

insert into public.game_submission_status_signals (signal_key)
select signal_key
from public.game_submission_status_signal_owners
on conflict (signal_key) do nothing;

drop trigger if exists game_submissions_status_signal on public.game_submissions;
create trigger game_submissions_status_signal
after insert or update or delete on public.game_submissions
for each row execute function public.bump_game_submission_status_signal();

do $$
begin
  if exists (
    select 1 from pg_publication where pubname = 'supabase_realtime'
  ) and exists (
    select 1
    from pg_publication_tables
    where pubname = 'supabase_realtime'
      and schemaname = 'public'
      and tablename = 'game_submissions'
  ) then
    alter publication supabase_realtime drop table public.game_submissions;
  end if;
  if exists (
    select 1 from pg_publication where pubname = 'supabase_realtime'
  ) and not exists (
    select 1
    from pg_publication_tables
    where pubname = 'supabase_realtime'
      and schemaname = 'public'
      and tablename = 'game_submission_status_signals'
  ) then
    alter publication supabase_realtime add table public.game_submission_status_signals;
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
