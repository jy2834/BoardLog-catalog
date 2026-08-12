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
RLS_TEST = ROOT / "supabase" / "tests" / "public_catalog_rls.test.sql"


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
        cls.effective_sql = (
            cls.sql + "\n" + cls.hardening_sql + "\n" + cls.edge_rpc_sql
            + "\n" + cls.edge_only_sql + "\n" + cls.storage_lockdown_sql
            + "\n" + cls.owner_status_sql
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

    def test_owner_realtime_select_is_row_and_column_scoped(self):
        self.assertTrue(OWNER_STATUS_MIGRATION.exists(), "owner status follow-up migration is required")
        self.assertRegex(
            self.owner_status_sql,
            r"create\s+policy\s+\"owners read own submission rows\"[\s\S]+?"
            r"for\s+select\s+to\s+authenticated[\s\S]+?owner_user_id\s*=\s*auth\.uid\(\)",
        )
        self.assertRegex(
            self.owner_status_sql,
            r"grant\s+select\s*\(\s*id\s*,\s*public_game\s*,\s*image_object_path\s*,\s*status\s*,"
            r"\s*submitter_message\s*,\s*created_at\s*,\s*updated_at\s*,\s*reviewed_at\s*\)"
            r"\s+on\s+public\.game_submissions\s+to\s+authenticated",
        )
        for private_column in ("owner_user_id", "admin_note", "reviewer_user_id", "exported_at"):
            with self.subTest(private_column=private_column):
                self.assertNotRegex(
                    self.owner_status_sql,
                    rf"grant\s+select\s*\([^)]*\b{private_column}\b[^)]*\)"
                    r"\s+on\s+public\.game_submissions\s+to\s+authenticated",
                )
        self.assertIn("alter publication supabase_realtime add table public.game_submissions", self.owner_status_sql)

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
            "owner can select Realtime-safe columns from the base row",
            "another user cannot select a cross-owner base row",
            "authenticated owners cannot select admin notes",
            "owner base-row select cannot expose an admin note",
            "owner can reuse the exact existing image path while image submissions are limited",
            "image-limited metadata update preserves the existing image path",
            "owner cannot replace an image path while image submissions are limited",
            "another user cannot update a cross-owner submission",
            "submission-closed state blocks owner updates",
            "maintenance state blocks owner updates",
            "owner cannot replace an image path during normal operation",
            "submission rows are published for Realtime changes",
            "admin retains access to the full moderation view",
            "non-admin cannot review a submission",
            "admin can approve a submission",
            "pending rows never enter the approved public view",
            "approved rows enter the approved public view",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.rls_test)

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
