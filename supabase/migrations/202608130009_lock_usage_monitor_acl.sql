-- Supabase may assign explicit EXECUTE grants to API roles when functions are created.
-- Remove those grants so usage measurements and cleanup controls remain secret-only.

revoke all on function public.catalog_usage_snapshot()
from public, anon, authenticated;
grant execute on function public.catalog_usage_snapshot() to service_role;

revoke all on function public.apply_catalog_usage_status(
  public.usage_level, public.service_state, timestamptz, jsonb
) from public, anon, authenticated;
grant execute on function public.apply_catalog_usage_status(
  public.usage_level, public.service_state, timestamptz, jsonb
) to service_role;

revoke all on function public.catalog_prunable_images(timestamptz, timestamptz, integer)
from public, anon, authenticated;
grant execute on function public.catalog_prunable_images(timestamptz, timestamptz, integer)
to service_role;

revoke all on function public.acknowledge_pruned_submission_images(text[])
from public, anon, authenticated;
grant execute on function public.acknowledge_pruned_submission_images(text[]) to service_role;
