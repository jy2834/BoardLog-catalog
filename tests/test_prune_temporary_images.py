import json
import os
import subprocess
import sys
import unittest

from scripts.prune_temporary_images import (
    PruneError,
    SupabasePruneRemote,
    prune_once,
)


OWNER = "11111111-1111-4111-8111-111111111111"
SUBMISSION = "22222222-2222-4222-8222-222222222222"
PATH = f"{OWNER}/{SUBMISSION}.jpg"


class FakeRemote:
    def __init__(self, candidates, *, delete_error=None):
        self.candidates = candidates
        self.delete_error = delete_error
        self.deleted = []
        self.acknowledged = []

    def fetch_candidates(self, rejected_before, orphan_before, limit):
        self.fetch_args = (rejected_before, orphan_before, limit)
        return self.candidates

    def delete_images(self, paths):
        if self.delete_error:
            raise self.delete_error
        self.deleted.append(list(paths))

    def acknowledge(self, paths):
        self.acknowledged.append(list(paths))


class PruneTemporaryImagesTest(unittest.TestCase):
    def test_deletes_only_valid_bounded_candidates_then_acknowledges(self):
        remote = FakeRemote(
            [
                {"objectPath": PATH, "reason": "REJECTED_OLD"},
                {"objectPath": f"{OWNER}/33333333-3333-4333-8333-333333333333.webp", "reason": "ORPHAN"},
            ]
        )
        result = prune_once(remote, now="2026-08-13T00:00:00Z", limit=100)
        self.assertEqual(2, result["deletedCount"])
        self.assertEqual(1, len(remote.deleted))
        self.assertEqual(remote.deleted, remote.acknowledged)
        rejected_before, orphan_before, limit = remote.fetch_args
        self.assertEqual("2026-07-14T00:00:00Z", rejected_before)
        self.assertEqual("2026-08-12T00:00:00Z", orphan_before)
        self.assertEqual(100, limit)

    def test_invalid_path_or_reason_fails_before_delete(self):
        invalid_rows = (
            [{"objectPath": "../escape.jpg", "reason": "ORPHAN"}],
            [{"objectPath": PATH, "reason": "PENDING"}],
            [{"objectPath": PATH, "reason": "ORPHAN"}, {"objectPath": PATH, "reason": "ORPHAN"}],
        )
        for rows in invalid_rows:
            with self.subTest(rows=rows):
                remote = FakeRemote(rows)
                with self.assertRaises(PruneError):
                    prune_once(remote, now="2026-08-13T00:00:00Z")
                self.assertEqual([], remote.deleted)
                self.assertEqual([], remote.acknowledged)

    def test_failed_storage_delete_is_never_acknowledged(self):
        remote = FakeRemote(
            [{"objectPath": PATH, "reason": "REJECTED_OLD"}],
            delete_error=PruneError("storage unavailable"),
        )
        with self.assertRaises(PruneError):
            prune_once(remote, now="2026-08-13T00:00:00Z")
        self.assertEqual([], remote.acknowledged)

    def test_no_candidates_is_a_noop(self):
        remote = FakeRemote([])
        result = prune_once(remote, now="2026-08-13T00:00:00Z")
        self.assertEqual({"deletedCount": 0, "paths": []}, result)
        self.assertEqual([], remote.deleted)

    def test_http_remote_calls_candidate_delete_and_ack_endpoints_without_bearer(self):
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
            if request.full_url.endswith("/rpc/catalog_prunable_images"):
                return Response(json.dumps([{"objectPath": PATH, "reason": "ORPHAN"}]).encode())
            if request.full_url.endswith("/storage/v1/object/submission-images"):
                return Response(json.dumps([{"id": "opaque-storage-id"}]).encode())
            return Response(b"")

        remote = SupabasePruneRemote(
            "https://project.supabase.co",
            "sb_secret_test",
            opener=opener,
        )
        result = prune_once(remote, now="2026-08-13T00:00:00Z")
        self.assertEqual(1, result["deletedCount"])
        self.assertEqual(3, len(requests))
        self.assertTrue(requests[0][0].full_url.endswith("/rest/v1/rpc/catalog_prunable_images"))
        self.assertTrue(requests[1][0].full_url.endswith("/storage/v1/object/submission-images"))
        self.assertTrue(requests[2][0].full_url.endswith("/rest/v1/rpc/acknowledge_pruned_submission_images"))
        for request, _timeout in requests:
            headers = dict(request.header_items())
            self.assertEqual("sb_secret_test", headers["Apikey"])
            self.assertNotIn("Authorization", headers)

    def test_cli_fails_closed_without_secrets(self):
        env = dict(os.environ)
        env.pop("SUPABASE_URL", None)
        env.pop("SUPABASE_SECRET_KEY", None)
        completed = subprocess.run(
            [sys.executable, "scripts/prune_temporary_images.py"],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(2, completed.returncode)
        self.assertIn("required", completed.stderr)
        self.assertEqual("", completed.stdout)


if __name__ == "__main__":
    unittest.main()
