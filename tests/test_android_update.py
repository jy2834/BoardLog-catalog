import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.validate_android_update import validate_android_update


REPO_ROOT = Path(__file__).resolve().parents[1]
CERTIFICATE_SHA256 = "1ACFD934FA432EDEDBB98800172924A34DE185BB17BF1BA503B7FFBDED078D51"


def valid_manifest(apk_bytes: bytes = b"BoardLog v0.3.4 verified APK fixture\n"):
    return {
        "schemaVersion": 1,
        "channel": "stable",
        "packageName": "com.boardlog.app",
        "versionCode": 7,
        "versionName": "0.3.4",
        "publishedAt": "2026-08-19T00:00:00Z",
        "downloadUrl": "https://github.com/jy2834/BoardLog-catalog/releases/download/android-v0.3.4/BoardLog-v0.3.4.apk",
        "releasePageUrl": "https://github.com/jy2834/BoardLog-catalog/releases/tag/android-v0.3.4",
        "sizeBytes": len(apk_bytes),
        "sha256": hashlib.sha256(apk_bytes).hexdigest(),
        "signingCertificateSha256": CERTIFICATE_SHA256,
        "mandatory": False,
        "releaseNotes": [
            "공용 목록 직접 새로고침",
            "새 버전 알림과 공개 다운로드 연결",
        ],
    }


def invalid_documents():
    document = valid_manifest()
    return [
        {**document, "unexpected": True},
        {key: value for key, value in document.items() if key != "mandatory"},
        {**document, "versionCode": "7"},
        {**document, "publishedAt": "2026-08-19T00:00:00.000Z"},
        {**document, "downloadUrl": document["downloadUrl"].replace("android-v0.3.4", "android-v0.3.5")},
        {**document, "releasePageUrl": "https://attacker@github.com/jy2834/BoardLog-catalog/releases/tag/android-v0.3.4"},
        {**document, "sha256": "a" * 64},
        {**document, "signingCertificateSha256": CERTIFICATE_SHA256[:-1] + "0"},
        {**document, "releaseNotes": ["x"] * 11},
    ]


class AndroidUpdateManifestTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.apk = Path(self.temp_dir.name) / "BoardLog-v0.3.4.apk"
        self.apk_bytes = b"BoardLog v0.3.4 verified APK fixture\n"
        self.apk.write_bytes(self.apk_bytes)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_valid_manifest_matches_optional_apk(self):
        self.assertEqual([], validate_android_update(valid_manifest(self.apk_bytes), apk_path=self.apk))

    def test_unknown_fields_url_mismatch_and_placeholder_digest_are_rejected(self):
        for document in invalid_documents():
            with self.subTest(document=document):
                self.assertTrue(validate_android_update(document))

    def test_size_and_hash_mismatches_are_rejected_against_optional_apk(self):
        for field, value in (("sizeBytes", len(self.apk_bytes) + 1), ("sha256", "b" * 64)):
            with self.subTest(field=field):
                document = valid_manifest(self.apk_bytes)
                document[field] = value
                self.assertTrue(validate_android_update(document, apk_path=self.apk))

    def test_published_at_must_match_release_metadata_when_expected(self):
        document = valid_manifest(self.apk_bytes)

        self.assertEqual(
            [],
            validate_android_update(
                document,
                expected_published_at=document["publishedAt"],
            ),
        )
        self.assertIn(
            "publishedAt: does not match expected published timestamp",
            validate_android_update(
                document,
                expected_published_at="2026-08-20T00:00:00Z",
            ),
        )

    def test_cli_rejects_published_at_that_differs_from_release_metadata(self):
        manifest = Path(self.temp_dir.name) / "android-update.json"
        manifest.write_text(json.dumps(valid_manifest(self.apk_bytes)), encoding="utf-8")

        completed = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "validate_android_update.py"),
                "--manifest",
                str(manifest),
                "--expected-published-at",
                "2026-08-20T00:00:00Z",
            ],
            capture_output=True,
            check=False,
            text=True,
        )

        self.assertEqual(1, completed.returncode)
        self.assertIn("publishedAt: does not match expected published timestamp", completed.stderr)

    def test_builder_writes_byte_identical_manifest_for_identical_inputs(self):
        first = Path(self.temp_dir.name) / "first.json"
        second = Path(self.temp_dir.name) / "second.json"
        arguments = [
            "--apk", str(self.apk),
            "--version-code", "7",
            "--version-name", "0.3.4",
            "--published-at", "2026-08-19T00:00:00Z",
            "--certificate-sha256", CERTIFICATE_SHA256,
        ]

        for output in (first, second):
            completed = subprocess.run(
                [sys.executable, str(REPO_ROOT / "scripts" / "build_android_update_manifest.py"), *arguments, "--output", str(output)],
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)

        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual(valid_manifest(self.apk_bytes), json.loads(first.read_text(encoding="utf-8")))

    def test_catalog_tree_never_contains_apk_or_zip_binaries(self):
        binaries = [path for path in Path("catalog").rglob("*") if path.suffix.lower() in {".apk", ".zip"}]
        self.assertEqual([], binaries)


if __name__ == "__main__":
    unittest.main()
