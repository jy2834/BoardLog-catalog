import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase" / "migrations" / "202608120001_public_catalog.sql"
HARDENING_MIGRATION = ROOT / "supabase" / "migrations" / "202608120002_harden_public_catalog.sql"
RLS_TEST = ROOT / "supabase" / "tests" / "public_catalog_rls.test.sql"


class SupabaseMigrationContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql = MIGRATION.read_text(encoding="utf-8")
        cls.hardening_sql = HARDENING_MIGRATION.read_text(encoding="utf-8")
        cls.effective_sql = cls.sql + "\n" + cls.hardening_sql
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

    def test_enables_rls_and_never_grants_base_submission_table_to_users(self):
        for table in ("game_submissions", "moderation_events", "admin_users", "service_status"):
            self.assertRegex(
                self.sql,
                rf"alter\s+table\s+public\.{table}\s+enable\s+row\s+level\s+security",
            )
        self.assertNotRegex(
            self.sql,
            r"grant\s+select\s+on\s+public\.game_submissions\s+to\s+(?:anon|authenticated)",
        )

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
            "owner can read the submitter-safe status view",
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


if __name__ == "__main__":
    unittest.main()
