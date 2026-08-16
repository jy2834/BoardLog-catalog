import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase" / "migrations" / "202608120001_public_catalog.sql"
HARDENING_MIGRATION = ROOT / "supabase" / "migrations" / "202608120002_harden_public_catalog.sql"
EDGE_RPC_MIGRATION = ROOT / "supabase" / "migrations" / "202608120003_edge_submission_rpc.sql"
EDGE_ONLY_MIGRATION = ROOT / "supabase" / "migrations" / "202608120004_require_edge_for_submission.sql"
STORAGE_LOCKDOWN_MIGRATION = ROOT / "supabase" / "migrations" / "202608120005_lock_submission_storage.sql"
OWNER_STATUS_MIGRATION = ROOT / "supabase" / "migrations" / "202608120006_owner_submission_status.sql"
ADMIN_CONSOLE_MIGRATION = ROOT / "supabase" / "migrations" / "202608130007_admin_console_reads.sql"
USAGE_MONITOR_MIGRATION = ROOT / "supabase" / "migrations" / "202608130008_free_usage_monitor.sql"
USAGE_ACL_MIGRATION = ROOT / "supabase" / "migrations" / "202608130009_lock_usage_monitor_acl.sql"
IN_APP_ADMIN_MIGRATION_GLOB = "*_in_app_admin_public_feed.sql"
RLS_TEST = ROOT / "supabase" / "tests" / "public_catalog_rls.test.sql"
CONFIG = ROOT / "supabase" / "config.toml"


class SupabaseMigrationContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql = MIGRATION.read_text(encoding="utf-8")
        cls.hardening_sql = HARDENING_MIGRATION.read_text(encoding="utf-8")
        cls.edge_rpc_sql = EDGE_RPC_MIGRATION.read_text(encoding="utf-8")
        cls.edge_only_sql = EDGE_ONLY_MIGRATION.read_text(encoding="utf-8")
        cls.storage_lockdown_sql = STORAGE_LOCKDOWN_MIGRATION.read_text(encoding="utf-8")
        cls.owner_status_sql = (
            OWNER_STATUS_MIGRATION.read_text(encoding="utf-8")
            if OWNER_STATUS_MIGRATION.exists()
            else ""
        )
        cls.admin_console_sql = (
            ADMIN_CONSOLE_MIGRATION.read_text(encoding="utf-8")
            if ADMIN_CONSOLE_MIGRATION.exists()
            else ""
        )
        cls.usage_monitor_sql = (
            USAGE_MONITOR_MIGRATION.read_text(encoding="utf-8")
            if USAGE_MONITOR_MIGRATION.exists()
            else ""
        )
        cls.usage_acl_sql = (
            USAGE_ACL_MIGRATION.read_text(encoding="utf-8")
            if USAGE_ACL_MIGRATION.exists()
            else ""
        )
        in_app_admin_migrations = sorted(
            (ROOT / "supabase" / "migrations").glob(IN_APP_ADMIN_MIGRATION_GLOB)
        )
        cls.in_app_admin_sql = (
            in_app_admin_migrations[-1].read_text(encoding="utf-8")
            if in_app_admin_migrations
            else ""
        )
        cls.public_unverified_projection = (
            cls.in_app_admin_sql.lower()
            .split("create or replace view public.public_unverified_catalog_games", 1)[-1]
            .split("from public.game_submissions", 1)[0]
        )
        cls.config = CONFIG.read_text(encoding="utf-8")
        cls.config_auth = cls.config.split("[auth]", 1)[1].split("[auth.rate_limit]", 1)[0]
        cls.config_email = cls.config.split("[auth.email]", 1)[1].split("[auth.sms]", 1)[0]
        cls.effective_sql = (
            cls.sql + "\n" + cls.hardening_sql + "\n" + cls.edge_rpc_sql
            + "\n" + cls.edge_only_sql + "\n" + cls.storage_lockdown_sql
            + "\n" + cls.owner_status_sql
            + "\n" + cls.admin_console_sql
            + "\n" + cls.usage_monitor_sql
            + "\n" + cls.usage_acl_sql
            + "\n" + cls.in_app_admin_sql
        )
        cls.rls_test = RLS_TEST.read_text(encoding="utf-8")

    def test_defines_required_types_tables_views_and_rpc(self):
        for token in (
            "submission_status",
            "usage_level",
            "service_state",
            "game_submissions",
            "moderation_events",
            "admin_users",
            "service_status",
            "approved_catalog_games",
            "my_game_submissions",
            "submit_game",
            "update_submission",
            "withdraw_submission",
            "review_submission",
        ):
            with self.subTest(token=token):
                self.assertIn(token, self.sql)

    def test_enables_rls_without_a_table_wide_submission_select_grant(self):
        for table in ("game_submissions", "moderation_events", "admin_users", "service_status"):
            self.assertRegex(
                self.sql,
                rf"alter\s+table\s+public\.{table}\s+enable\s+row\s+level\s+security",
            )
        self.assertNotRegex(
            self.effective_sql,
            r"grant\s+select\s+on\s+public\.game_submissions\s+to\s+(?:anon|authenticated)",
        )

    def test_in_app_admin_public_feed_contract(self):
        sql = self.in_app_admin_sql.lower()
        self.assertIn("create type public.submission_visibility", sql)
        self.assertIn("create table public.catalog_suppressions", sql)
        self.assertIn("create or replace view public.public_unverified_catalog_games", sql)
        self.assertIn("create or replace view public.public_catalog_suppressions", sql)
        self.assertIn("create or replace function public.request_submission_removal", sql)
        self.assertIn("create or replace function public.set_submission_visibility", sql)
        self.assertIn("create or replace function public.prepare_submission_delete", sql)
        self.assertIn("create or replace function public.finalize_submission_delete", sql)
        self.assertIn("(select auth.jwt()->>'is_anonymous')::boolean is false", sql)
        self.assertIn("grant select on public.public_unverified_catalog_metadata to anon, authenticated", sql)
        self.assertIn("grant select on public.public_catalog_suppressions to anon, authenticated", sql)
        self.assertNotIn("owner_user_id", self.public_unverified_projection)
        self.assertNotIn("admin_note", self.public_unverified_projection)

    def test_in_app_admin_public_feed_preserves_view_compatibility_and_admin_scope(self):
        sql = self.in_app_admin_sql.lower()
        my_view = sql.split("create or replace view public.my_game_submissions", 1)[1].split(";", 1)[0]
        admin_view = sql.split("create or replace view public.admin_game_submissions", 1)[1].split(";", 1)[0]
        metadata_view = sql.split("create or replace view public.public_unverified_catalog_metadata", 1)[1].split(";", 1)[0]
        admin_helper = sql.split("create or replace function public.is_catalog_admin", 1)[-1].split("$$;", 1)[0]

        my_view = re.sub(r"\s+", " ", my_view)
        admin_view = re.sub(r"\s+", " ", admin_view)
        self.assertIn(
            "select id, public_game, image_object_path, status, submitter_message, created_at, updated_at, reviewed_at, visibility, removal_requested_at",
            my_view,
        )
        self.assertIn(
            "select id, owner_user_id, public_game, image_object_path, status, submitter_message, admin_note, reviewer_user_id, created_at, updated_at, reviewed_at, exported_at, visibility",
            admin_view,
        )
        self.assertIn("from public.game_submissions", metadata_view)
        self.assertNotIn("from public.public_unverified_catalog_games", metadata_view)
        self.assertNotIn("image_object_path", metadata_view)
        self.assertIn("create or replace view public.approved_catalog_games", sql)
        self.assertIn("visibility <> 'hidden'", sql)
        self.assertIn("auth.uid() is not null", admin_helper)
        self.assertIn("coalesce((select auth.jwt()->>'is_anonymous')::boolean, true) is false", admin_helper)

    def test_email_signup_is_disabled_without_disabling_anonymous_users(self):
        self.assertIn("enable_signup = true", self.config_auth)
        self.assertIn("enable_anonymous_sign_ins = true", self.config_auth)
        self.assertIn("[auth.email]", self.config)
        self.assertIn("enable_signup = false", self.config_email)

    def test_owner_realtime_uses_a_privacy_safe_invalidation_signal(self):
        self.assertTrue(OWNER_STATUS_MIGRATION.exists(), "owner status follow-up migration is required")
        self.assertIn("create table public.game_submission_status_signals", self.owner_status_sql)
        self.assertRegex(
            self.owner_status_sql,
            r"create\s+policy\s+\"owners read own submission status signal\"[\s\S]+?"
            r"for\s+select\s+to\s+authenticated[\s\S]+?"
            r"owns_game_submission_status_signal\(signal_key\)",
        )
        self.assertRegex(
            self.owner_status_sql,
            r"create\s+or\s+replace\s+function\s+public\.owns_game_submission_status_signal"
            r"[\s\S]+?owner_user_id\s*=\s*auth\.uid\(\)",
        )
        self.assertRegex(
            self.owner_status_sql,
            r"grant\s+select\s*\(\s*signal_key\s*,\s*revision\s*,\s*updated_at\s*\)"
            r"\s+on\s+public\.game_submission_status_signals\s+to\s+authenticated",
        )
        self.assertIn("drop policy if exists \"owners read own submission rows\"", self.owner_status_sql)
        self.assertNotIn("grant select (\n  id,", self.owner_status_sql)
        self.assertNotRegex(
            self.owner_status_sql,
            r"grant\s+select\s*\([^)]*\)\s+on\s+public\.game_submissions",
        )
        self.assertIn("alter publication supabase_realtime drop table public.game_submissions", self.owner_status_sql)
        self.assertNotIn("add table public.game_submissions;", self.owner_status_sql)
        self.assertIn(
            "alter publication supabase_realtime add table public.game_submission_status_signals",
            self.owner_status_sql,
        )

    def test_status_signal_delete_identity_cannot_expose_a_submission_or_owner(self):
        self.assertIn("create table public.game_submission_status_signals", self.owner_status_sql)
        table_sql = self.owner_status_sql.split(
            "create table public.game_submission_status_signals", 1
        )[1].split(";", 1)[0]
        self.assertIn("signal_key uuid primary key default gen_random_uuid()", table_sql)
        self.assertNotIn("submission_id", table_sql)
        self.assertNotIn("owner_user_id", table_sql)
        self.assertIn("create table public.game_submission_status_signal_owners", self.owner_status_sql)
        self.assertIn("owner_user_id uuid primary key", self.owner_status_sql)
        self.assertIn(
            "revoke all on public.game_submission_status_signal_owners from anon, authenticated",
            self.owner_status_sql,
        )
        self.assertIn(
            "alter table public.game_submission_status_signals replica identity default",
            self.owner_status_sql,
        )
        self.assertIn(
            "after insert or update or delete on public.game_submissions",
            self.owner_status_sql,
        )
        self.assertNotRegex(
            self.owner_status_sql,
            r"delete\s+from\s+public\.game_submission_status_signals",
        )

    def test_image_limited_update_accepts_only_the_exact_existing_non_null_path(self):
        self.assertTrue(OWNER_STATUS_MIGRATION.exists(), "owner status follow-up migration is required")
        self.assertIn("create or replace function public.update_submission", self.owner_status_sql)
        self.assertRegex(
            self.owner_status_sql,
            r"p_image_path\s+is\s+not\s+null[\s\S]+?"
            r"p_image_path\s+is\s+distinct\s+from\s+v_existing_image_path[\s\S]+?"
            r"v_state\s*=\s*'IMAGE_LIMITED'",
        )
        self.assertIn(
            "if p_image_path is not null\n"
            "    and p_image_path is distinct from v_existing_image_path then",
            self.owner_status_sql,
        )
        self.assertRegex(
            self.owner_status_sql,
            r"where\s+id\s*=\s*p_id\s+and\s+owner_user_id\s*=\s*v_owner"
            r"\s+and\s+status\s*=\s*'PENDING'",
        )
        self.assertIn("v_state in ('SUBMISSION_CLOSED', 'MAINTENANCE')", self.owner_status_sql)
        self.assertIn("message = 'submission updates are unavailable'", self.owner_status_sql)

    def test_submission_rpc_enforces_privacy_state_size_owner_prefix_and_rate_limit(self):
        for token in (
            "contains_forbidden_public_key",
            "32 * 1024",
            "SUBMISSION_CLOSED",
            "MAINTENANCE",
            "IMAGE_LIMITED",
            "interval '24 hours'",
            "auth.uid()::text || '/%'",
        ):
            with self.subTest(token=token):
                self.assertIn(token, self.sql)

    def test_usage_monitor_functions_are_secret_only_and_preserve_maintenance(self):
        self.assertTrue(USAGE_MONITOR_MIGRATION.exists(), "usage monitor migration is required")
        self.assertEqual(4, self.usage_monitor_sql.count("set search_path = pg_catalog, pg_temp"))
        self.assertNotIn("set search_path = public", self.usage_monitor_sql)
        for function in (
            "catalog_usage_snapshot",
            "apply_catalog_usage_status",
            "catalog_prunable_images",
            "acknowledge_pruned_submission_images",
        ):
            with self.subTest(function=function):
                self.assertIn(f"function public.{function}", self.usage_monitor_sql)
                self.assertRegex(
                    self.usage_monitor_sql,
                    rf"revoke\s+all\s+on\s+function\s+public\.{function}[\s\S]+?from\s+public",
                )
                self.assertRegex(
                    self.usage_monitor_sql,
                    rf"grant\s+execute\s+on\s+function\s+public\.{function}[\s\S]+?to\s+service_role",
                )
        self.assertRegex(
            self.usage_monitor_sql,
            r"service_state\s*=\s*case[\s\S]+?when\s+service_state\s*=\s*'MAINTENANCE'",
        )
        self.assertIn("pg_database_size(current_database())", self.usage_monitor_sql)
        self.assertRegex(
            self.usage_monitor_sql,
            r"count\(\*\)\s+filter[\s\S]+?metadata->>'size'\s+is\s+null"
            r"[\s\S]+?then\s+null",
        )
        self.assertIn("bucket_id = 'submission-images'", self.usage_monitor_sql)
        self.assertIn("interval '30 days'", self.usage_monitor_sql)
        self.assertIn("interval '1 day'", self.usage_monitor_sql)
        self.assertIn("p_verified_at >= last_verified_at", self.usage_monitor_sql)
        self.assertIn("p_verified_at > now() + interval '5 minutes'", self.usage_monitor_sql)
        self.assertIn("usage level and service state do not match", self.usage_monitor_sql)
        self.assertIn("pruned image deletion is not verified", self.usage_monitor_sql)
        self.assertRegex(
            self.usage_monitor_sql,
            r"storage\.objects\s+o[\s\S]+?o\.name\s*=\s*any\s*\(p_paths\)"
            r"[\s\S]+?update\s+public\.game_submissions",
        )
        self.assertRegex(
            self.usage_monitor_sql,
            r"active\.image_object_path\s*=\s*s\.image_object_path[\s\S]+?active\.status\s*<>\s*'REJECTED'",
        )
        self.assertRegex(
            self.usage_monitor_sql,
            r"from\s+public\.game_submissions\s+s[\s\S]+?s\.status\s*=\s*'REJECTED'"
            r"[\s\S]+?s\.image_object_path\s+is\s+not\s+null",
        )
        self.assertIn("union\n", self.usage_monitor_sql.lower())
        self.assertNotIn("union all\n\n    select o.name", self.usage_monitor_sql.lower())
        self.assertRegex(
            self.usage_monitor_sql,
            r"not\s+exists\s*\([\s\S]+?game_submissions[\s\S]+?image_object_path\s*=\s*o\.name",
        )

    def test_usage_monitor_follow_up_removes_explicit_mobile_function_grants(self):
        self.assertTrue(USAGE_ACL_MIGRATION.exists(), "usage monitor ACL follow-up is required")
        normalized_acl = re.sub(r"\s+", " ", self.usage_acl_sql).strip()
        normalized_acl = normalized_acl.replace("( ", "(").replace(" )", ")")
        for signature in (
            "catalog_usage_snapshot()",
            "apply_catalog_usage_status(public.usage_level, public.service_state, timestamptz, jsonb)",
            "catalog_prunable_images(timestamptz, timestamptz, integer)",
            "acknowledge_pruned_submission_images(text[])",
        ):
            with self.subTest(signature=signature):
                self.assertIn(
                    f"revoke all on function public.{signature} from public, anon, authenticated;",
                    normalized_acl,
                )
                self.assertIn(
                    f"grant execute on function public.{signature} to service_role;",
                    normalized_acl,
                )

    def test_remote_pgtap_never_directly_deletes_storage_objects(self):
        self.assertNotRegex(
            self.rls_test,
            r"delete\s+from\s+storage\.objects",
            "Supabase Storage objects must be deleted through the Storage API",
        )

    def test_remote_pgtap_resets_secret_role_before_reading_private_rows(self):
        self.assertRegex(
            self.rls_test,
            r"secret cleanup can acknowledge deleted image paths'\s*\n\);\s*"
            r"reset role;\s*insert into tap_results select is\(",
        )

    def test_owner_update_results_are_read_through_the_safe_view(self):
        owner_update_section = self.rls_test.split(
            "owner can reuse the exact existing image path", 1
        )[1].split("owner cannot replace an image path", 1)[0]
        self.assertNotIn("from public.game_submissions", owner_update_section)
        self.assertGreaterEqual(owner_update_section.count("from public.my_game_submissions"), 2)

    def test_storage_bucket_is_private_and_owner_scoped(self):
        self.assertRegex(
            self.sql,
            r"insert\s+into\s+storage\.buckets[\s\S]+?'submission-images'[\s\S]+?false",
        )
        self.assertIn("(storage.foldername(name))[1] = auth.uid()::text", self.sql)
        self.assertNotIn("public = true", self.sql.lower())

    def test_authenticated_clients_cannot_upload_directly_to_submission_storage(self):
        self.assertIn('drop policy if exists "owners upload submission images"', self.storage_lockdown_sql)
        self.assertIn('drop policy if exists "owners read submission images"', self.storage_lockdown_sql)
        self.assertIn('drop policy if exists "owners delete submission images"', self.storage_lockdown_sql)
        self.assertNotRegex(
            self.storage_lockdown_sql,
            r"for\s+insert\s+to\s+authenticated",
        )
        self.assertIn("public.is_catalog_admin()", self.storage_lockdown_sql)

    def test_public_and_owner_views_do_not_expose_private_columns(self):
        owner_view = self.sql.split("create or replace view public.my_game_submissions", 1)[1].split(";", 1)[0]
        owner_projection = owner_view.split("from public.game_submissions", 1)[0]
        public_view = self.sql.split("create or replace view public.approved_catalog_games", 1)[1].split(";", 1)[0]
        public_projection = public_view.split("from public.game_submissions", 1)[0]
        for forbidden in ("admin_note", "owner_user_id"):
            self.assertNotIn(forbidden, owner_projection)
        for forbidden in ("admin_note", "owner_user_id", "image_object_path"):
            self.assertNotIn(forbidden, public_projection)

    def test_pgtap_covers_cross_user_admin_and_public_visibility(self):
        for phrase in (
            "another user cannot read the pending submission",
            "authenticated clients cannot bypass Turnstile with direct submission RPC",
            "authenticated clients cannot upload submission images outside the edge",
            "edge can submit a privacy-safe game for an authenticated owner",
            "owner can read the submitter-safe status view",
            "submission rows are not published for Realtime changes",
            "authenticated clients cannot select base submission rows",
            "owner can read one opaque status signal",
            "another user cannot read a cross-owner status signal",
            "status signal has no submission or owner identifier",
            "status signal delete identity is only an opaque key",
            "status signal uses default primary-key-only delete identity",
            "authenticated clients cannot select private signal ownership mappings",
            "private signal ownership mappings are not published",
            "withdrawal updates the owner signal without publishing a submission delete",
            "owner can reuse the exact existing image path while image submissions are limited",
            "image-limited metadata update preserves the existing image path",
            "owner cannot replace an image path while image submissions are limited",
            "another user cannot update a cross-owner submission",
            "submission-closed state blocks owner updates",
            "maintenance state blocks owner updates",
            "owner cannot replace an image path during normal operation",
            "status signals are published for Realtime changes",
            "admin retains access to the full moderation view",
            "non-admin cannot review a submission",
            "admin can approve a submission",
            "pending rows never enter the approved public view",
            "approved test row enters the approved public view",
            "anonymous owner cannot hide public content",
            "owner can request public removal without deleting the row",
            "anon can read public unverified metadata without private image paths",
            "anon cannot query the private unverified image view",
            "authenticated anonymous user can read public unverified metadata",
            "anonymous admin membership is rejected",
            "anonymous admin membership cannot read the admin moderation view",
            "admin can hide an approved public row",
            "hide creates a public suppression tombstone",
            "hidden approved row is absent from the public catalog view",
            "admin can restore a hidden public row",
            "restore removes the public suppression tombstone",
            "restored approved row re-enters the public catalog view",
            "admin can prepare a hidden submission for deletion",
            "delete preparation creates a public suppression tombstone",
            "admin can finalize a prepared submission deletion",
            "finalized deletion removes the submission row",
            "finalized deletion retains the public suppression tombstone",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.rls_test)

    def test_admin_console_reads_are_admin_scoped_and_privacy_bounded(self):
        self.assertTrue(
            ADMIN_CONSOLE_MIGRATION.exists(),
            "admin console read contract migration is required",
        )
        self.assertIn("create or replace view public.admin_service_status", self.admin_console_sql)
        self.assertIn("create or replace view public.admin_moderation_events", self.admin_console_sql)
        self.assertIn("where public.is_catalog_admin()", self.admin_console_sql)
        self.assertIn("usage_level", self.admin_console_sql)
        usage_view, remainder = self.admin_console_sql.split(
            "create or replace view public.admin_moderation_events",
            1,
        )
        audit_view = remainder.split("revoke all on public.admin_service_status", 1)[0]
        self.assertNotIn("owner_user_id", usage_view + audit_view)
        self.assertNotIn("actor_user_id", usage_view + audit_view)
        self.assertIn(
            "grant select on public.admin_service_status to authenticated",
            self.admin_console_sql,
        )
        self.assertIn(
            "grant select on public.admin_moderation_events to authenticated",
            self.admin_console_sql,
        )
        for phrase in (
            "non-admin cannot read admin usage status",
            "non-admin cannot read moderation audit events",
            "admin can read usage level for quota banners",
            "admin can read privacy-bounded moderation audit events",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.rls_test)

    def test_review_contract_requires_submitter_visible_rejection_reason(self):
        self.assertRegex(
            self.admin_console_sql,
            r"create\s+or\s+replace\s+function\s+public\.review_submission[\s\S]+?"
            r"p_decision\s*=\s*'REJECTED'[\s\S]+?rejection reason required",
        )
        self.assertIn("admin cannot reject without a submitter-visible reason", self.rls_test)
        self.assertRegex(
            self.admin_console_sql,
            r"p_public_game->>'targetKey'\)\s*!~\s*'\^\[a-z0-9\]",
        )
        self.assertIn("admin cannot merge to a malformed stable key", self.rls_test)

    def test_follow_up_migration_keeps_remote_and_fresh_installs_equivalent(self):
        for function_name in (
            "is_valid_submission_payload",
            "is_valid_reviewed_game",
            "review_submission",
        ):
            with self.subTest(function_name=function_name):
                self.assertIn(
                    f"create or replace function public.{function_name}",
                    self.hardening_sql,
                )

    def test_optional_public_metadata_is_strictly_typed_and_bounded(self):
        for field in ("bggId", "publicRating", "koreanEditionYear"):
            with self.subTest(field=field):
                self.assertRegex(
                    self.effective_sql,
                    rf"p_payload\s*\?\s*'{field}'[\s\S]+?jsonb_typeof\(p_payload->'{field}'\)",
                )

    def test_audit_assertion_runs_only_after_leaving_authenticated_role(self):
        approval = self.rls_test.index("admin can approve a submission")
        audit = self.rls_test.index("approval creates an audit event")
        between = self.rls_test[approval:audit]
        self.assertIn("reset role;", between)

    def test_remote_pgtap_script_turns_any_not_ok_into_a_query_failure(self):
        self.assertIn("create temporary table tap_results", self.rls_test)
        self.assertIn("result like 'not ok %'", self.rls_test)
        self.assertIn("raise exception 'pgTAP failures", self.rls_test)

    def test_pgtap_plan_and_summary_match_the_assertion_count(self):
        planned = int(re.search(r"select plan\((\d+)\)", self.rls_test).group(1))
        summary = int(re.search(r"ok - all (\d+) pgTAP assertions passed", self.rls_test).group(1))
        inserted_results = len(re.findall(r"insert into tap_results select ", self.rls_test))
        self.assertEqual(planned, inserted_results - 1, "finish() is stored but is not a planned assertion")
        self.assertEqual(planned, summary)

    def test_edge_rpc_accepts_a_preallocated_id_without_weakening_owner_checks(self):
        for token in (
            "submit_game_with_id",
            "p_submission_id uuid",
            "auth.uid()",
            "auth.uid()::text || '/%'",
            "daily submission limit reached",
            "invalid public game payload",
        ):
            with self.subTest(token=token):
                self.assertIn(token, self.edge_rpc_sql)
        self.assertIn("revoke all on function public.submit_game_with_id", self.edge_rpc_sql)
        self.assertIn("grant execute on function public.submit_game_with_id", self.edge_rpc_sql)

    def test_submission_creation_requires_the_edge_service_role(self):
        for signature in (
            "public.submit_game(jsonb, text)",
            "public.submit_game_with_id(uuid, jsonb, text)",
        ):
            with self.subTest(signature=signature):
                self.assertIn(f"revoke execute on function {signature} from authenticated", self.edge_only_sql)
        self.assertIn("submit_game_from_edge", self.edge_only_sql)
        self.assertIn("p_owner_user_id uuid", self.edge_only_sql)
        self.assertIn("grant execute on function public.submit_game_from_edge", self.edge_only_sql)
        self.assertIn("to service_role", self.edge_only_sql)
        self.assertNotIn("to authenticated", self.edge_only_sql)


if __name__ == "__main__":
    unittest.main()
