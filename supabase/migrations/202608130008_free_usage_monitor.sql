-- Secret-only measurements and cleanup candidates for the free-tier monitor.
-- Public/mobile/web clients never receive project usage metrics or storage paths.

create or replace function public.catalog_usage_snapshot()
returns jsonb
language sql
security definer
set search_path = pg_catalog, pg_temp
as $$
  select jsonb_build_object(
    'capturedAt', to_char(clock_timestamp() at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
    'databaseBytes', pg_database_size(current_database()),
    'storageBytes', (
      select case
        when count(*) filter (
          where o.metadata->>'size' is null
             or o.metadata->>'size' !~ '^[0-9]+$'
        ) > 0 then null
        else coalesce(sum((o.metadata->>'size')::numeric)::bigint, 0::bigint)
      end
      from storage.objects o
    )
  );
$$;

create or replace function public.apply_catalog_usage_status(
  p_usage_level public.usage_level,
  p_service_state public.service_state,
  p_verified_at timestamptz,
  p_metrics jsonb
)
returns void
language plpgsql
security definer
set search_path = pg_catalog, pg_temp
as $$
begin
  if p_usage_level is null
    or p_service_state is null
    or p_verified_at is null
    or jsonb_typeof(p_metrics) <> 'object' then
    raise exception using errcode = '22023', message = 'invalid verified usage report';
  end if;
  if p_verified_at > now() + interval '5 minutes' then
    raise exception using errcode = '22023', message = 'verified usage time is in the future';
  end if;
  if not (
    p_usage_level in ('NORMAL', 'NOTICE_80', 'WARNING_90') and p_service_state = 'NORMAL'
    or p_usage_level = 'CRITICAL_95' and p_service_state = 'IMAGE_LIMITED'
    or p_usage_level = 'EXHAUSTED_100' and p_service_state = 'SUBMISSION_CLOSED'
  ) then
    raise exception using errcode = '22023', message = 'usage level and service state do not match';
  end if;

  update public.service_status
  set usage_level = p_usage_level,
      service_state = case
        when service_state = 'MAINTENANCE' then service_state
        else p_service_state
      end,
      last_verified_at = p_verified_at,
      metrics = p_metrics,
      updated_at = now()
  where singleton
    and p_verified_at >= last_verified_at;

  if not found and not exists (select 1 from public.service_status where singleton) then
    raise exception using errcode = '55000', message = 'service status singleton missing';
  end if;
end;
$$;

create or replace function public.catalog_prunable_images(
  p_rejected_before timestamptz default now() - interval '30 days',
  p_orphan_before timestamptz default now() - interval '1 day',
  p_limit integer default 100
)
returns table("objectPath" text, reason text)
language sql
stable
security definer
set search_path = pg_catalog, pg_temp
as $$
  with candidates as (
    select s.image_object_path as object_path,
           'REJECTED_OLD'::text as prune_reason,
           min(coalesce(s.reviewed_at, s.updated_at)) as created_at
    from public.game_submissions s
    where s.status = 'REJECTED'
      and s.image_object_path is not null
      and coalesce(s.reviewed_at, s.updated_at) < p_rejected_before
      and not exists (
        select 1
        from public.game_submissions active
        where active.image_object_path = s.image_object_path
          and active.status <> 'REJECTED'
      )
    group by s.image_object_path

    union

    select o.name as object_path, 'ORPHAN'::text as prune_reason, o.created_at
    from storage.objects o
    where o.bucket_id = 'submission-images'
      and o.created_at < p_orphan_before
      and not exists (
        select 1
        from public.game_submissions s
        where s.image_object_path = o.name
      )
  )
  select object_path, prune_reason
  from candidates
  where object_path ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}/[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\.(jpg|webp)$'
  order by created_at, object_path
  limit least(greatest(coalesce(p_limit, 0), 0), 500);
$$;

create or replace function public.acknowledge_pruned_submission_images(p_paths text[])
returns void
language plpgsql
security definer
set search_path = pg_catalog, pg_temp
as $$
begin
  if p_paths is null
    or cardinality(p_paths) > 500
    or exists (
      select 1
      from unnest(p_paths) as path
      where path !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}/[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\.(jpg|webp)$'
    ) then
    raise exception using errcode = '22023', message = 'invalid pruned image paths';
  end if;

  if exists (
    select 1
    from storage.objects o
    where o.bucket_id = 'submission-images'
      and o.name = any (p_paths)
  ) then
    raise exception using errcode = '55000', message = 'pruned image deletion is not verified';
  end if;

  update public.game_submissions
  set image_object_path = null,
      updated_at = now()
  where status = 'REJECTED'
    and image_object_path = any (p_paths);
end;
$$;

revoke all on function public.catalog_usage_snapshot() from public;
grant execute on function public.catalog_usage_snapshot() to service_role;

revoke all on function public.apply_catalog_usage_status(
  public.usage_level, public.service_state, timestamptz, jsonb
) from public;
grant execute on function public.apply_catalog_usage_status(
  public.usage_level, public.service_state, timestamptz, jsonb
) to service_role;

revoke all on function public.catalog_prunable_images(timestamptz, timestamptz, integer) from public;
grant execute on function public.catalog_prunable_images(timestamptz, timestamptz, integer) to service_role;

revoke all on function public.acknowledge_pruned_submission_images(text[]) from public;
grant execute on function public.acknowledge_pruned_submission_images(text[]) to service_role;
