import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.monitor_free_usage import (
    MonitorError,
    SupabaseUsageRemote,
    evaluate_usage,
    monitor_once,
    public_summary,
)


class FakeRemote:
    def __init__(self, snapshot=None, *, snapshot_error=None):
        self.snapshot = snapshot
        self.snapshot_error = snapshot_error
        self.applied = []

    def fetch_snapshot(self):
        if self.snapshot_error:
            raise self.snapshot_error
        return self.snapshot

    def apply_status(self, report):
        self.applied.append(report)


class MonitorFreeUsageTest(unittest.TestCase):
    def test_thresholds_are_conservative_and_deterministic(self):
        cases = (
            (0.7999, "NORMAL", "NORMAL"),
            (0.80, "NOTICE_80", "NORMAL"),
            (0.90, "WARNING_90", "NORMAL"),
            (0.95, "CRITICAL_95", "IMAGE_LIMITED"),
            (1.00, "EXHAUSTED_100", "SUBMISSION_CLOSED"),
        )
        for ratio, level, state in cases:
            with self.subTest(ratio=ratio):
                report = evaluate_usage(
                    {
                        "capturedAt": "2026-08-13T00:00:00Z",
                        "databaseBytes": int(500_000_000 * ratio),
                        "storageBytes": 0,
                    }
                )
                self.assertEqual(level, report["usageLevel"])
                self.assertEqual(state, report["serviceState"])

    def test_largest_verified_metric_controls_status(self):
        report = evaluate_usage(
            {
                "capturedAt": "2026-08-13T00:00:00Z",
                "databaseBytes": 25_000_000,
                "storageBytes": 960_000_000,
            }
        )
        self.assertEqual("CRITICAL_95", report["usageLevel"])
        self.assertEqual("IMAGE_LIMITED", report["serviceState"])
        self.assertEqual("storageBytes", report["limitingMetric"])
        self.assertEqual(0.96, report["metrics"]["storageBytes"]["ratio"])

    def test_unverified_optional_metrics_are_not_invented(self):
        report = evaluate_usage(
            {
                "capturedAt": "2026-08-13T00:00:00Z",
                "databaseBytes": 20,
                "storageBytes": 30,
            }
        )
        self.assertNotIn("monthlyActiveUsers", report["metrics"])
        self.assertNotIn("egressBytes", report["metrics"])
        self.assertEqual(["databaseBytes", "storageBytes"], report["verifiedMetrics"])

    def test_missing_or_invalid_required_snapshot_fails_closed(self):
        for snapshot in (
            None,
            {},
            {"capturedAt": "bad", "databaseBytes": 0, "storageBytes": 0},
            {"capturedAt": "2026-08-13T00:00:00Z", "databaseBytes": -1, "storageBytes": 0},
            {"capturedAt": "2026-08-13T00:00:00Z", "databaseBytes": True, "storageBytes": 0},
        ):
            with self.subTest(snapshot=snapshot):
                with self.assertRaises(MonitorError):
                    evaluate_usage(snapshot)

    def test_monitor_does_not_overwrite_status_when_snapshot_fails(self):
        remote = FakeRemote(snapshot_error=MonitorError("offline"))
        with self.assertRaises(MonitorError):
            monitor_once(remote)
        self.assertEqual([], remote.applied)

    def test_monitor_applies_one_verified_report(self):
        remote = FakeRemote(
            {
                "capturedAt": "2026-08-13T00:00:00Z",
                "databaseBytes": 450_000_000,
                "storageBytes": 100_000_000,
            }
        )
        report = monitor_once(remote)
        self.assertEqual("WARNING_90", report["usageLevel"])
        self.assertEqual([report], remote.applied)

    def test_public_summary_never_exposes_exact_usage_values(self):
        report = evaluate_usage(
            {
                "capturedAt": "2026-08-13T00:00:00Z",
                "databaseBytes": 123456789,
                "storageBytes": 987654321,
            }
        )
        output = json.dumps(public_summary(report), sort_keys=True)
        self.assertNotIn("123456789", output)
        self.assertNotIn("987654321", output)
        self.assertNotIn("metrics", output.lower())
        self.assertEqual(
            {"limitingMetric", "serviceState", "usageLevel"},
            set(public_summary(report)),
        )

    def test_http_remote_uses_only_apikey_and_expected_rpcs(self):
        requests = []

        class Response:
            def __init__(self, body):
                self.body = body

            def read(self, amount=-1):
                return self.body

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        def opener(request, timeout):
            requests.append((request, timeout))
            if request.full_url.endswith("/rpc/catalog_usage_snapshot"):
                return Response(
                    json.dumps(
                        {
                            "capturedAt": "2026-08-13T00:00:00Z",
                            "databaseBytes": 1,
                            "storageBytes": 2,
                        }
                    ).encode()
                )
            return Response(b"")

        remote = SupabaseUsageRemote(
            "https://project.supabase.co",
            "sb_secret_test",
            opener=opener,
        )
        report = monitor_once(remote)
        self.assertEqual(2, len(requests))
        snapshot_request = requests[0][0]
        update_request = requests[1][0]
        self.assertTrue(snapshot_request.full_url.endswith("/rest/v1/rpc/catalog_usage_snapshot"))
        self.assertTrue(update_request.full_url.endswith("/rest/v1/rpc/apply_catalog_usage_status"))
        for request, _timeout in requests:
            headers = dict(request.header_items())
            self.assertEqual("sb_secret_test", headers["Apikey"])
            self.assertNotIn("Authorization", headers)
        payload = json.loads(update_request.data)
        self.assertEqual(report["usageLevel"], payload["p_usage_level"])
        self.assertEqual(report["serviceState"], payload["p_service_state"])

    def test_cli_fails_closed_without_secrets(self):
        env = dict(os.environ)
        env.pop("SUPABASE_URL", None)
        env.pop("SUPABASE_SECRET_KEY", None)
        completed = subprocess.run(
            [sys.executable, "scripts/monitor_free_usage.py"],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(2, completed.returncode)
        self.assertIn("required", completed.stderr)
        self.assertEqual("", completed.stdout)


if __name__ == "__main__":
    unittest.main()
