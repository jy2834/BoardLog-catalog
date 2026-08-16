from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.export_approved import ExportError, ApprovedSubmission, apply_approved_submissions


ORIGIN_ONE = "11111111-1111-4111-8111-111111111111"
ORIGIN_TWO = "22222222-2222-4222-8222-222222222222"
OWNER_ONE = "33333333-3333-4333-8333-333333333333"
IMAGE_ONE = f"{OWNER_ONE}/{ORIGIN_ONE}.jpg"


def public_game(key: str, *, origin: str = ORIGIN_ONE, bgg_id: int | None = None) -> dict:
    return {
        "key": key,
        "name": "공개 게임",
        "englishName": "Public Game",
        "aliases": ["퍼블릭 게임"],
        "yearPublished": 2026,
        "koreanEditionYear": None,
        "catalogSource": "COMMUNITY",
        "entryType": "BASE_GAME",
        "minPlayers": 2,
        "maxPlayers": 4,
        "minPlayMinutes": 30,
        "maxPlayMinutes": 60,
        "tags": ["FAMILY"],
        "bggId": bgg_id,
        "imageUrl": "https://jy2834.github.io/BoardLog-catalog/catalog/images/no-cover.svg",
        "publicRating": None,
        "weight": 2.0,
        "listPriceWon": None,
        "priceKind": "UNAVAILABLE",
        "sourceUrls": ["https://example.com/official"],
        "originSubmissionId": origin,
        "publishedAt": "2026-08-13T00:00:00Z",
    }


def document(*games: dict, revision: int = 1) -> dict:
    return {
        "schemaVersion": 2,
        "revision": revision,
        "generatedAt": "2026-08-12T00:00:00Z",
        "games": list(games),
    }


class ExportApprovedTest(unittest.TestCase):
    def test_suppression_removes_only_the_matching_origin_deterministically(self):
        suppressed = public_game("suppressed", origin=ORIGIN_ONE)
        untouched = public_game("untouched", origin=ORIGIN_TWO)

        first, exported_ids = apply_approved_submissions(
            document(suppressed, untouched, revision=7),
            [],
            suppressed_origin_ids=[ORIGIN_ONE],
            generated_at="2026-08-16T01:02:03Z",
        )
        second, _ = apply_approved_submissions(
            first,
            [],
            suppressed_origin_ids=[ORIGIN_ONE],
            generated_at="2026-08-16T04:05:06Z",
        )

        self.assertEqual([untouched], first["games"])
        self.assertEqual(8, first["revision"])
        self.assertEqual("2026-08-16T01:02:03Z", first["generatedAt"])
        self.assertEqual([], exported_ids)
        self.assertEqual(first, second)

    def test_restored_approved_row_with_null_exported_at_is_exported_again(self):
        restored = ApprovedSubmission(
            ORIGIN_ONE, "APPROVED", public_game("restored"), None,
            "2026-08-16T01:00:00Z",
        )

        updated, exported_ids = apply_approved_submissions(
            document(revision=3),
            [restored],
            suppressed_origin_ids=[],
            generated_at="2026-08-16T01:02:03Z",
        )

        self.assertEqual(["restored"], [game["key"] for game in updated["games"]])
        self.assertEqual([ORIGIN_ONE], exported_ids)

    def test_retained_delete_tombstone_cannot_recreate_its_catalog_row(self):
        deleted = ApprovedSubmission(
            ORIGIN_ONE, "APPROVED", public_game("deleted"), None,
            "2026-08-16T01:00:00Z",
        )

        updated, exported_ids = apply_approved_submissions(
            document(public_game("deleted"), revision=4),
            [deleted],
            suppressed_origin_ids=[ORIGIN_ONE],
            generated_at="2026-08-16T01:02:03Z",
        )

        self.assertEqual([], updated["games"])
        self.assertEqual([], exported_ids)

    def test_remote_fetches_only_suppression_origin_ids_in_bounded_pages(self):
        from scripts.export_approved import SupabaseExportRemote

        captured_paths: list[str] = []
        remote = SupabaseExportRemote("https://project.supabase.co", "sb_secret_test")

        def request(path, **_kwargs):
            captured_paths.append(path)
            return json.dumps([{"origin_submission_id": ORIGIN_ONE}]).encode()

        remote._request = request
        self.assertEqual({ORIGIN_ONE}, remote.fetch_suppressions())
        self.assertEqual(1, len(captured_paths))
        self.assertIn("/rest/v1/public_catalog_suppressions?", captured_paths[0])
        self.assertIn("select=origin_submission_id", captured_paths[0])
        self.assertNotIn("reason", captured_paths[0])
        self.assertNotIn("actor", captured_paths[0])

    def test_suppression_only_cycle_rewrites_and_publishes_the_catalog(self):
        from scripts.export_approved import export_cycle

        events: list[str] = []

        class Remote:
            def fetch_pending(self, _submission_id): return []
            def fetch_suppressions(self): return {ORIGIN_ONE}
            def mark_exported(self, _ids): raise AssertionError("no rows were exported")
            def delete_images(self, _paths): raise AssertionError("no temporary images exist")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "catalog" / "images").mkdir(parents=True)
            (root / "catalog" / "images" / "no-cover.svg").write_text("<svg/>", encoding="utf-8")
            (root / "catalog" / "catalog.json").write_text(
                json.dumps(document(public_game("suppressed"))), encoding="utf-8"
            )
            (root / "catalog" / "schema.json").write_text(
                Path("catalog/schema.json").read_text(), encoding="utf-8"
            )

            exported = export_cycle(
                Remote(), root, lambda: events.append("publish"),
                generated_at="2026-08-16T01:02:03Z",
            )
            updated = json.loads((root / "catalog" / "catalog.json").read_text())

        self.assertEqual([], exported)
        self.assertEqual([], updated["games"])
        self.assertEqual(["publish"], events)

    def test_inserts_approved_rows_deterministically_and_increments_once(self):
        submissions = [
            ApprovedSubmission(ORIGIN_TWO, "APPROVED", public_game("z-game", origin=ORIGIN_TWO), None),
            ApprovedSubmission(ORIGIN_ONE, "APPROVED", public_game("a-game"), None),
        ]

        updated, exported_ids = apply_approved_submissions(
            document(), submissions, generated_at="2026-08-13T01:02:03Z"
        )

        self.assertEqual(["a-game", "z-game"], [game["key"] for game in updated["games"]])
        self.assertEqual(2, updated["revision"])
        self.assertEqual("2026-08-13T01:02:03Z", updated["generatedAt"])
        self.assertEqual([ORIGIN_ONE, ORIGIN_TWO], exported_ids)

    def test_database_review_timestamp_is_the_published_timestamp(self):
        game = public_game("reviewed-game")
        game["publishedAt"] = "2026-08-13T00:00:00Z"
        updated, _ = apply_approved_submissions(
            document(),
            [ApprovedSubmission(ORIGIN_ONE, "APPROVED", game, None, "2026-08-13T03:04:05Z")],
            generated_at="2026-08-13T03:04:06Z",
        )
        self.assertEqual("2026-08-13T03:04:05Z", updated["games"][0]["publishedAt"])

    def test_replay_is_idempotent_and_does_not_increment_revision(self):
        existing = public_game("same-game")
        current = document(existing, revision=7)

        updated, exported_ids = apply_approved_submissions(
            current,
            [ApprovedSubmission(ORIGIN_ONE, "APPROVED", dict(existing), None)],
            generated_at="2026-08-13T01:02:03Z",
        )

        self.assertEqual(current, updated)
        self.assertEqual([ORIGIN_ONE], exported_ids)

    def test_replay_ignores_json_object_field_order(self):
        original = public_game("same-game")
        reordered = dict(reversed(list(original.items())))

        updated, exported_ids = apply_approved_submissions(
            document(original, revision=7),
            [ApprovedSubmission(ORIGIN_ONE, "APPROVED", reordered, None)],
            generated_at="2026-08-13T01:02:03Z",
        )

        self.assertEqual(document(original, revision=7), updated)
        self.assertEqual([ORIGIN_ONE], exported_ids)

    def test_rejects_stable_key_and_bgg_collisions_without_deleting_existing_rows(self):
        existing = public_game("existing", bgg_id=42)
        for colliding in (
            public_game("existing", origin=ORIGIN_TWO),
            public_game("different", origin=ORIGIN_TWO, bgg_id=42),
        ):
            with self.subTest(key=colliding["key"], bgg=colliding["bggId"]):
                with self.assertRaises(ExportError):
                    apply_approved_submissions(
                        document(existing),
                        [ApprovedSubmission(ORIGIN_TWO, "APPROVED", colliding, None)],
                        generated_at="2026-08-13T01:02:03Z",
                    )

    def test_merge_patches_only_the_named_target_and_preserves_other_rows(self):
        target = public_game("target", origin=ORIGIN_ONE, bgg_id=42)
        target["imageUrl"] = "https://example.com/original-cover.webp"
        untouched = public_game("untouched", origin=ORIGIN_TWO, bgg_id=43)
        patch = {**target, "key": "review-patch", "originSubmissionId": ORIGIN_TWO, "updateTargetKey": "target", "aliases": ["수정 별칭"]}
        patch.pop("listPriceWon")
        patch.pop("priceKind")
        patch.pop("koreanEditionYear")

        updated, exported_ids = apply_approved_submissions(
            document(target, untouched),
            [ApprovedSubmission(ORIGIN_TWO, "MERGED", patch, None)],
            generated_at="2026-08-13T01:02:03Z",
        )

        by_key = {game["key"]: game for game in updated["games"]}
        self.assertEqual(["수정 별칭"], by_key["target"]["aliases"])
        self.assertNotIn("updateTargetKey", by_key["target"])
        self.assertEqual("https://example.com/original-cover.webp", by_key["target"]["imageUrl"])
        self.assertIsNone(by_key["target"]["listPriceWon"])
        self.assertEqual("UNAVAILABLE", by_key["target"]["priceKind"])
        self.assertIsNone(by_key["target"]["koreanEditionYear"])
        self.assertEqual(untouched, by_key["untouched"])
        self.assertNotIn("review-patch", by_key)
        self.assertEqual([ORIGIN_TWO], exported_ids)

    def test_later_merge_replaces_the_existing_bundled_target_patch(self):
        existing_patch = public_game("first-patch", origin=ORIGIN_ONE, bgg_id=42)
        existing_patch["updateTargetKey"] = "bundled-game"
        later = public_game("second-patch", origin=ORIGIN_TWO, bgg_id=42)
        later["targetKey"] = "bundled-game"
        later["aliases"] = ["최신 별칭"]

        updated, _ = apply_approved_submissions(
            document(existing_patch),
            [ApprovedSubmission(ORIGIN_TWO, "MERGED", later, None)],
            generated_at="2026-08-13T01:02:03Z",
        )

        self.assertEqual(1, len(updated["games"]))
        self.assertEqual("first-patch", updated["games"][0]["key"])
        self.assertEqual("bundled-game", updated["games"][0]["updateTargetKey"])
        self.assertEqual(["최신 별칭"], updated["games"][0]["aliases"])
        self.assertEqual(ORIGIN_TWO, updated["games"][0]["originSubmissionId"])

    def test_invalid_complete_catalog_is_rejected_before_write(self):
        invalid = public_game("invalid")
        invalid["purchasePrice"] = 1
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ExportError):
                apply_approved_submissions(
                    document(),
                    [ApprovedSubmission(ORIGIN_ONE, "APPROVED", invalid, None)],
                    generated_at="2026-08-13T01:02:03Z",
                    schema_path=Path("catalog/schema.json"),
                    images_dir=Path(directory),
                )

    def test_catalog_writer_is_byte_deterministic(self):
        from scripts.export_approved import canonical_catalog_bytes

        original = document(public_game("same-game"))
        reordered = {
            "games": [dict(reversed(list(original["games"][0].items())))],
            "generatedAt": original["generatedAt"],
            "revision": original["revision"],
            "schemaVersion": original["schemaVersion"],
        }
        first = canonical_catalog_bytes(original)
        second = canonical_catalog_bytes(reordered)
        self.assertEqual(first, second)

    def test_merge_can_publish_a_patch_for_a_bundled_only_target(self):
        patch = public_game("review-patch")
        patch.pop("key")
        patch["targetKey"] = "missing-target"
        updated, _ = apply_approved_submissions(
            document(),
            [ApprovedSubmission(ORIGIN_ONE, "MERGED", patch, None)],
            generated_at="2026-08-13T01:02:03Z",
        )
        self.assertEqual(f"merge-{ORIGIN_ONE}", updated["games"][0]["key"])
        self.assertEqual("missing-target", updated["games"][0]["updateTargetKey"])

    def test_duplicate_submission_ids_are_rejected(self):
        row = ApprovedSubmission(ORIGIN_ONE, "APPROVED", public_game("new-game"), None)
        with self.assertRaisesRegex(ExportError, "Duplicate submission"):
            apply_approved_submissions(
                document(), [row, row], generated_at="2026-08-13T01:02:03Z"
            )

    def test_multiple_merge_rows_cannot_silently_overwrite_the_same_target(self):
        first = public_game("first-patch", origin=ORIGIN_ONE)
        first["targetKey"] = "bundled-game"
        second = public_game("second-patch", origin=ORIGIN_TWO)
        second["targetKey"] = "bundled-game"

        with self.assertRaisesRegex(ExportError, "same target"):
            apply_approved_submissions(
                document(),
                [
                    ApprovedSubmission(ORIGIN_ONE, "MERGED", first, None),
                    ApprovedSubmission(ORIGIN_TWO, "MERGED", second, None),
                ],
                generated_at="2026-08-13T01:02:03Z",
            )

    def test_supabase_acknowledgement_happens_only_after_publish_callback(self):
        from scripts.export_approved import export_cycle

        events: list[str] = []

        class Remote:
            def fetch_pending(self, _submission_id):
                return [ApprovedSubmission(ORIGIN_ONE, "APPROVED", public_game("new-game"), None)]

            def mark_exported(self, submission_ids):
                events.append("ack:" + ",".join(submission_ids))

            def delete_images(self, _paths):
                events.append("delete")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "catalog" / "images").mkdir(parents=True)
            (root / "catalog" / "images" / "no-cover.svg").write_text("<svg/>", encoding="utf-8")
            (root / "catalog" / "catalog.json").write_text(json.dumps(document()), encoding="utf-8")
            (root / "catalog" / "schema.json").write_text(Path("catalog/schema.json").read_text(), encoding="utf-8")

            def publish():
                events.append("publish")

            export_cycle(Remote(), root, publish, generated_at="2026-08-13T01:02:03Z")

        self.assertEqual(["publish", f"ack:{ORIGIN_ONE}"], events)

    def test_deferred_acknowledgement_leaves_remote_rows_and_images_until_pages_succeeds(self):
        from scripts.export_approved import export_cycle

        events: list[str] = []

        class Remote:
            def fetch_pending(self, _submission_id):
                return [ApprovedSubmission(ORIGIN_ONE, "APPROVED", public_game("new-game"), None)]

            def mark_exported(self, _ids):
                events.append("ack")

            def delete_images(self, _paths):
                events.append("delete")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "catalog" / "images").mkdir(parents=True)
            (root / "catalog" / "images" / "no-cover.svg").write_text("<svg/>", encoding="utf-8")
            (root / "catalog" / "catalog.json").write_text(json.dumps(document()), encoding="utf-8")
            (root / "catalog" / "schema.json").write_text(Path("catalog/schema.json").read_text(), encoding="utf-8")

            exported = export_cycle(
                Remote(), root, lambda: events.append("publish"),
                generated_at="2026-08-13T01:02:03Z",
                acknowledge=False,
            )

        self.assertEqual([ORIGIN_ONE], exported)
        self.assertEqual(["publish"], events)

    def test_post_pages_acknowledgement_refetches_rows_before_cleanup(self):
        from scripts.export_approved import acknowledge_exports

        events: list[object] = []

        class Remote:
            def fetch_pending(self, submission_id):
                events.append(("fetch", submission_id))
                return [ApprovedSubmission(ORIGIN_ONE, "APPROVED", public_game("new-game"), IMAGE_ONE)]

            def mark_exported(self, ids):
                events.append(("ack", list(ids)))

            def delete_images(self, paths):
                events.append(("delete", list(paths)))

        acknowledge_exports(Remote(), [ORIGIN_ONE])

        self.assertEqual([
            ("fetch", ORIGIN_ONE),
            ("ack", [ORIGIN_ONE]),
            ("delete", [IMAGE_ONE]),
        ], events)

    def test_publish_failure_keeps_remote_row_and_image_for_retry(self):
        from scripts.export_approved import export_cycle

        events: list[str] = []

        class Remote:
            def fetch_pending(self, _submission_id):
                return [ApprovedSubmission(ORIGIN_ONE, "APPROVED", public_game("new-game"), IMAGE_ONE)]

            def download_image(self, _path):
                return b"not-used-for-this-test"

            def mark_exported(self, _submission_ids):
                events.append("ack")

            def delete_images(self, _paths):
                events.append("delete")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "catalog" / "images").mkdir(parents=True)
            (root / "catalog" / "images" / "no-cover.svg").write_text("<svg/>", encoding="utf-8")
            (root / "catalog" / "catalog.json").write_text(json.dumps(document()), encoding="utf-8")
            (root / "catalog" / "schema.json").write_text(Path("catalog/schema.json").read_text(), encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "push failed"):
                export_cycle(
                    Remote(), root, lambda: (_ for _ in ()).throw(RuntimeError("push failed")),
                    generated_at="2026-08-13T01:02:03Z",
                    image_converter=lambda _data, path: path.write_bytes(b"RIFFfakeWEBP"),
                )

        self.assertEqual([], events)

    def test_rejects_private_image_paths_outside_the_submission_layout(self):
        from scripts.export_approved import export_cycle

        class Remote:
            def fetch_pending(self, _submission_id):
                return [ApprovedSubmission(ORIGIN_ONE, "APPROVED", public_game("new-game"), "../private.jpg")]

            def download_image(self, _path):
                raise AssertionError("unsafe paths must be rejected before download")

            def mark_exported(self, _ids):
                raise AssertionError("unsafe rows must remain retryable")

            def delete_images(self, _paths):
                raise AssertionError("unsafe paths must never be deleted")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "catalog" / "images").mkdir(parents=True)
            (root / "catalog" / "images" / "no-cover.svg").write_text("<svg/>", encoding="utf-8")
            (root / "catalog" / "catalog.json").write_text(json.dumps(document()), encoding="utf-8")
            (root / "catalog" / "schema.json").write_text(Path("catalog/schema.json").read_text(), encoding="utf-8")

            with self.assertRaisesRegex(ExportError, "Submission image path"):
                export_cycle(Remote(), root, lambda: None)

    def test_retry_rechecks_git_publish_even_when_failed_run_left_matching_files(self):
        from scripts.export_approved import export_cycle

        events: list[str] = []

        class Remote:
            def fetch_pending(self, _submission_id):
                return [ApprovedSubmission(ORIGIN_ONE, "APPROVED", public_game("new-game"), None)]

            def mark_exported(self, submission_ids):
                events.append("ack:" + ",".join(submission_ids))

            def delete_images(self, _paths):
                raise AssertionError("text-only export has no temporary image")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "catalog" / "images").mkdir(parents=True)
            (root / "catalog" / "images" / "no-cover.svg").write_text("<svg/>", encoding="utf-8")
            (root / "catalog" / "catalog.json").write_text(json.dumps(document()), encoding="utf-8")
            (root / "catalog" / "schema.json").write_text(Path("catalog/schema.json").read_text(), encoding="utf-8")

            with self.assertRaises(RuntimeError):
                export_cycle(Remote(), root, lambda: (_ for _ in ()).throw(RuntimeError("push failed")), generated_at="2026-08-13T01:02:03Z")
            export_cycle(Remote(), root, lambda: events.append("publish"), generated_at="2026-08-13T01:02:03Z")

        self.assertEqual(["publish", f"ack:{ORIGIN_ONE}"], events)

    def test_remote_payload_is_bounded_to_one_export_batch(self):
        from scripts.export_approved import SupabaseExportRemote, MAX_EXPORT_BATCH

        remote = SupabaseExportRemote("https://project.supabase.co", "sb_secret_test")
        remote._request = lambda _path, **_kwargs: json.dumps([
            {"id": ORIGIN_ONE, "status": "APPROVED", "public_game": public_game("game"), "image_object_path": None, "reviewed_at": "2026-08-13T00:00:00Z"}
        ] * (MAX_EXPORT_BATCH + 1)).encode()
        with self.assertRaisesRegex(ExportError, "batch"):
            remote.fetch_pending(None)

    def test_git_publisher_retries_push_even_when_previous_run_already_committed(self):
        from unittest.mock import patch
        from scripts.export_approved import publish_with_git

        completed = __import__("subprocess").CompletedProcess([], 0, stdout="", stderr="")
        with patch("scripts.export_approved.subprocess.run", return_value=completed) as run:
            publish_with_git(Path("."))

        commands = [call.args[0] for call in run.call_args_list]
        self.assertNotIn(["git", "commit", "-m", "data: publish approved BoardLog games"], commands)
        self.assertEqual(["git", "push", "origin", "HEAD:main"], commands[-1])

    def test_workflow_uses_only_new_secret_key_and_runs_on_schedule_or_dispatch(self):
        workflow = Path(".github/workflows/export-approved.yml").read_text(encoding="utf-8")
        self.assertIn("cron: '*/15 * * * *'", workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("if: github.ref == 'refs/heads/main'", workflow)
        self.assertIn("submission_id:", workflow)
        self.assertIn("INPUT_ALL_PENDING", workflow)
        self.assertIn("SUPABASE_SECRET_KEY: ${{ secrets.SUPABASE_SECRET_KEY }}", workflow)
        self.assertEqual(2, workflow.count("SUPABASE_SECRET_KEY: ${{ secrets.SUPABASE_SECRET_KEY }}"))
        self.assertNotIn("    env:\n      SUPABASE_URL", workflow)
        self.assertNotIn("SERVICE_ROLE", workflow)
        self.assertIn("concurrency:", workflow)
        self.assertIn("pillow==12.3.0", workflow.lower())
        self.assertNotIn("cache: pip", workflow)
        self.assertIn("pages: write", workflow)
        self.assertIn("actions/deploy-pages@v4", workflow)
        self.assertIn("steps.publish.outputs.exported_count != '0'", workflow)
        self.assertIn("--defer-acknowledgement", workflow)
        self.assertIn("--acknowledge-ids", workflow)
        self.assertLess(workflow.index("actions/deploy-pages@v4"), workflow.index("--acknowledge-ids"))

    def test_secret_key_remote_uses_correct_rest_and_private_storage_paths(self):
        from scripts.export_approved import SupabaseExportRemote

        class Response:
            def __enter__(self): return self
            def __exit__(self, *_args): return None
            def read(self, _limit): return b"data"

        remote = SupabaseExportRemote("https://project.supabase.co", "sb_secret_test")
        from unittest.mock import patch
        with patch.object(remote.opener, "open", return_value=Response()) as opened:
            remote.download_image(IMAGE_ONE)
            remote.delete_images([IMAGE_ONE])

        download = opened.call_args_list[0].args[0]
        deletion = opened.call_args_list[1].args[0]
        self.assertEqual(f"https://project.supabase.co/storage/v1/object/submission-images/{IMAGE_ONE}", download.full_url)
        self.assertEqual("https://project.supabase.co/storage/v1/object/submission-images", deletion.full_url)
        self.assertEqual("DELETE", deletion.method)
        self.assertIn(f'"prefixes": ["{IMAGE_ONE}"]'.encode(), deletion.data)
        self.assertEqual("sb_secret_test", download.get_header("Apikey"))
        self.assertIsNone(download.get_header("Authorization"))

    def test_secret_remote_rejects_unsafe_origins_and_redirects(self):
        from scripts.export_approved import NoRedirectHandler, SupabaseExportRemote

        for origin in (
            "http://project.supabase.co",
            "https://user:password@project.supabase.co",
            "https://project.supabase.co/?redirect=1",
            "https://project.supabase.co/#fragment",
        ):
            with self.subTest(origin=origin):
                with self.assertRaisesRegex(ExportError, "HTTPS project origin"):
                    SupabaseExportRemote(origin, "sb_secret_test")
        self.assertIsNone(NoRedirectHandler().redirect_request(None, None, 302, "Found", {}, "https://other.example"))

    def test_remote_rows_must_match_the_reviewed_database_shape(self):
        from scripts.export_approved import SupabaseExportRemote

        remote = SupabaseExportRemote("https://project.supabase.co", "sb_secret_test")
        bad_rows = [
            {"id": ORIGIN_ONE, "status": "PENDING", "public_game": public_game("game"), "image_object_path": None, "reviewed_at": "2026-08-13T00:00:00Z"},
            {"id": ORIGIN_ONE, "status": "APPROVED", "public_game": public_game("game"), "image_object_path": 123, "reviewed_at": "2026-08-13T00:00:00Z"},
            {"id": ORIGIN_ONE, "status": "APPROVED", "public_game": public_game("game"), "image_object_path": None, "reviewed_at": None},
        ]
        for row in bad_rows:
            with self.subTest(row=row):
                remote._request = lambda _path, value=row, **_kwargs: json.dumps([value]).encode()
                with self.assertRaisesRegex(ExportError, "invalid approved row"):
                    remote.fetch_pending(None)

    def test_approved_key_is_validated_before_it_can_name_an_output_file(self):
        unsafe = public_game("../escape")
        with self.assertRaisesRegex(ExportError, "stable key"):
            apply_approved_submissions(
                document(),
                [ApprovedSubmission(ORIGIN_ONE, "APPROVED", unsafe, IMAGE_ONE)],
                generated_at="2026-08-13T01:02:03Z",
            )

    def test_cleanup_failure_does_not_retract_an_already_published_export(self):
        from contextlib import redirect_stderr
        from io import StringIO
        from scripts.export_approved import export_cycle

        events: list[str] = []

        class Remote:
            def fetch_pending(self, _submission_id):
                return [ApprovedSubmission(ORIGIN_ONE, "APPROVED", public_game("new-game"), IMAGE_ONE)]
            def download_image(self, _path): return b"cover"
            def mark_exported(self, _ids): events.append("ack")
            def delete_images(self, _paths): raise ExportError("cleanup offline")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "catalog" / "images").mkdir(parents=True)
            (root / "catalog" / "images" / "no-cover.svg").write_text("<svg/>", encoding="utf-8")
            (root / "catalog" / "catalog.json").write_text(json.dumps(document()), encoding="utf-8")
            (root / "catalog" / "schema.json").write_text(Path("catalog/schema.json").read_text(), encoding="utf-8")
            error_output = StringIO()
            with redirect_stderr(error_output):
                exported = export_cycle(
                    Remote(), root, lambda: events.append("publish"),
                    generated_at="2026-08-13T01:02:03Z",
                    image_converter=lambda _data, path: path.write_bytes(b"RIFFfakeWEBP"),
                )

        self.assertEqual([ORIGIN_ONE], exported)
        self.assertEqual(["publish", "ack"], events)
        self.assertIn("temporary image cleanup deferred", error_output.getvalue())

    def test_acknowledgement_must_confirm_every_exported_id_before_cleanup(self):
        from scripts.export_approved import SupabaseExportRemote

        remote = SupabaseExportRemote("https://project.supabase.co", "sb_secret_test")
        remote._request = lambda _path, **_kwargs: b"[]"
        with self.assertRaisesRegex(ExportError, "acknowledge"):
            remote.mark_exported([ORIGIN_ONE])

    def test_acknowledgement_clears_the_temporary_image_reference(self):
        from scripts.export_approved import SupabaseExportRemote

        captured = {}
        remote = SupabaseExportRemote("https://project.supabase.co", "sb_secret_test")
        def request(_path, **kwargs):
            captured.update(kwargs)
            return json.dumps([{"id": ORIGIN_ONE}]).encode()
        remote._request = request

        remote.mark_exported([ORIGIN_ONE])

        body = json.loads(captured["body"])
        self.assertIsNone(body["image_object_path"])
        self.assertIsInstance(body["exported_at"], str)

    def test_acknowledgement_rejects_a_non_array_response(self):
        from scripts.export_approved import SupabaseExportRemote

        remote = SupabaseExportRemote("https://project.supabase.co", "sb_secret_test")
        remote._request = lambda _path, **_kwargs: b'{"id":"unexpected"}'
        with self.assertRaisesRegex(ExportError, "invalid export acknowledgement"):
            remote.mark_exported([ORIGIN_ONE])

    def test_remote_rejects_invalid_json_without_a_traceback(self):
        from scripts.export_approved import SupabaseExportRemote

        remote = SupabaseExportRemote("https://project.supabase.co", "sb_secret_test")
        remote._request = lambda _path, **_kwargs: b"not-json"
        with self.assertRaisesRegex(ExportError, "invalid JSON"):
            remote.fetch_pending(None)

    def test_cli_runs_directly_and_fails_closed_without_secrets(self):
        import subprocess

        completed = subprocess.run(
            ["python3", "scripts/export_approved.py"],
            cwd=Path.cwd(),
            capture_output=True,
            text=True,
            env={},
            check=False,
        )
        self.assertEqual(2, completed.returncode)
        self.assertIn("SUPABASE_URL and SUPABASE_SECRET_KEY are required", completed.stderr)


if __name__ == "__main__":
    unittest.main()
