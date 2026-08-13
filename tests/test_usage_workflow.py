import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "monitor-free-usage.yml"
RUNBOOK = ROOT / "docs" / "free-tier-runbook.md"


class UsageWorkflowTest(unittest.TestCase):
    def test_daily_main_only_monitor_has_bounded_permissions_and_no_legacy_key(self):
        self.assertTrue(WORKFLOW.exists())
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("schedule:", workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("github.ref == 'refs/heads/main'", workflow)
        self.assertIn("contents: read", workflow)
        self.assertIn("issues: write", workflow)
        self.assertIn("SUPABASE_SECRET_KEY", workflow)
        self.assertNotIn("SERVICE_ROLE", workflow)
        self.assertNotIn("SUPABASE_ACCESS_TOKEN", workflow)
        self.assertIn("timeout-minutes:", workflow)
        self.assertIn("python3 scripts/monitor_free_usage.py", workflow)
        self.assertIn("python3 scripts/prune_temporary_images.py", workflow)
        self.assertNotIn("metrics_json", workflow)

    def test_issue_is_opened_only_at_ninety_percent_or_higher(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("WARNING_90|CRITICAL_95|EXHAUSTED_100", workflow)
        self.assertIn("always()", workflow)
        self.assertIn("gh issue create", workflow)
        self.assertIn("gh issue edit", workflow)
        self.assertIn("gh issue close", workflow)
        self.assertIn("free-tier-usage", workflow)
        self.assertNotIn("billing upgrade", workflow.lower())

    def test_runbook_is_explicit_about_verified_and_dashboard_only_metrics(self):
        self.assertTrue(RUNBOOK.exists())
        text = RUNBOOK.read_text(encoding="utf-8")
        for phrase in (
            "500 MB",
            "1 GB",
            "50,000 MAU",
            "5 GB uncached",
            "5 GB cached",
            "500,000 Edge Function",
            "2 million Realtime",
            "200 peak connections",
            "DB와 Storage만",
            "자동 결제 전환이나 유료 플랜 업그레이드를 하지 않는다",
            "MAINTENANCE",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)


if __name__ == "__main__":
    unittest.main()
