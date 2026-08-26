#!/usr/bin/env python3
"""Validate the immutable public BoardLog Android update manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence
from urllib.parse import urlparse


BOARDLOG_PACKAGE_NAME = "com.boardlog.app"
BOARDLOG_CERTIFICATE_SHA256 = "1ACFD934FA432EDEDBB98800172924A34DE185BB17BF1BA503B7FFBDED078D51"
REQUIRED_FIELDS = {
    "schemaVersion",
    "channel",
    "packageName",
    "versionCode",
    "versionName",
    "publishedAt",
    "downloadUrl",
    "releasePageUrl",
    "sizeBytes",
    "sha256",
    "signingCertificateSha256",
    "mandatory",
    "releaseNotes",
}
VERSION_NAME_PATTERN = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
PUBLISHED_AT_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
MAX_VERSION_NAME_LENGTH = 64
MAX_RELEASE_NOTES = 10
MAX_RELEASE_NOTE_CODE_POINTS = 200
MAX_SIGNED_64_BIT_INTEGER = 9_223_372_036_854_775_807


def _is_int(value: object) -> bool:
    return type(value) is int


def _is_canonical_timestamp(value: object) -> bool:
    if not isinstance(value, str) or not PUBLISHED_AT_PATTERN.fullmatch(value):
        return False
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return parsed.strftime("%Y-%m-%dT%H:%M:%SZ") == value


def _is_valid_github_url(value: object, expected_path: str) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = urlparse(value)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.netloc == "github.com"
        and port is None
        and parsed.username is None
        and parsed.password is None
        and parsed.params == ""
        and parsed.query == ""
        and parsed.fragment == ""
        and parsed.path == expected_path
    )


def _validate_apk(document: Mapping[str, object], apk_path: Path) -> list[str]:
    errors: list[str] = []
    try:
        apk_bytes = apk_path.read_bytes()
    except OSError as error:
        return [f"apk: cannot read {apk_path}: {error}"]

    size_bytes = document.get("sizeBytes")
    if _is_int(size_bytes) and size_bytes != len(apk_bytes):
        errors.append("sizeBytes: does not match APK bytes")
    sha256 = document.get("sha256")
    if isinstance(sha256, str) and sha256 != hashlib.sha256(apk_bytes).hexdigest():
        errors.append("sha256: does not match APK bytes")
    return errors


def validate_android_update(
    document: object,
    apk_path: Path | None = None,
    expected_published_at: str | None = None,
) -> list[str]:
    """Return every manifest-contract error without raising for invalid input."""
    if not isinstance(document, Mapping):
        return ["manifest: must be an object"]

    errors: list[str] = []
    field_names = tuple(document)
    string_field_names = {field for field in field_names if isinstance(field, str)}
    if any(not isinstance(field, str) for field in field_names):
        errors.append("manifest: field names must be strings")
    for field in sorted(string_field_names - REQUIRED_FIELDS):
        errors.append(f"{field}: unexpected field")
    for field in sorted(REQUIRED_FIELDS - string_field_names):
        errors.append(f"{field}: missing required field")

    if document.get("schemaVersion") != 1 or not _is_int(document.get("schemaVersion")):
        errors.append("schemaVersion: must be integer 1")
    if document.get("channel") != "stable":
        errors.append("channel: must equal stable")
    if document.get("packageName") != BOARDLOG_PACKAGE_NAME:
        errors.append(f"packageName: must equal {BOARDLOG_PACKAGE_NAME}")

    version_code = document.get("versionCode")
    if not _is_int(version_code) or not 0 < version_code <= MAX_SIGNED_64_BIT_INTEGER:
        errors.append("versionCode: must be a positive signed 64-bit integer")
    version_name = document.get("versionName")
    if not isinstance(version_name, str) or len(version_name) > MAX_VERSION_NAME_LENGTH or not VERSION_NAME_PATTERN.fullmatch(version_name):
        errors.append("versionName: must be a supported semantic version")

    published_at = document.get("publishedAt")
    if not _is_canonical_timestamp(published_at):
        errors.append("publishedAt: must be a canonical RFC 3339 UTC timestamp")
    elif expected_published_at is not None and published_at != expected_published_at:
        errors.append("publishedAt: does not match expected published timestamp")

    size_bytes = document.get("sizeBytes")
    if not _is_int(size_bytes) or not 0 < size_bytes <= MAX_SIGNED_64_BIT_INTEGER:
        errors.append("sizeBytes: must be a positive signed 64-bit integer")
    sha256 = document.get("sha256")
    if not isinstance(sha256, str) or not SHA256_PATTERN.fullmatch(sha256):
        errors.append("sha256: must be a lowercase SHA-256 digest")
    elif len(set(sha256)) == 1:
        errors.append("sha256: placeholder digest is forbidden")
    if document.get("signingCertificateSha256") != BOARDLOG_CERTIFICATE_SHA256:
        errors.append("signingCertificateSha256: must match the BoardLog release certificate")
    if type(document.get("mandatory")) is not bool:
        errors.append("mandatory: must be a boolean")

    release_notes = document.get("releaseNotes")
    if not isinstance(release_notes, list) or not 1 <= len(release_notes) <= MAX_RELEASE_NOTES:
        errors.append(f"releaseNotes: must contain 1 through {MAX_RELEASE_NOTES} strings")
    elif any(
        not isinstance(note, str)
        or not note.strip()
        or len(note) > MAX_RELEASE_NOTE_CODE_POINTS
        for note in release_notes
    ):
        errors.append(f"releaseNotes: each note must be non-blank and at most {MAX_RELEASE_NOTE_CODE_POINTS} code points")

    if isinstance(version_name, str) and VERSION_NAME_PATTERN.fullmatch(version_name):
        tag = f"android-v{version_name}"
        asset = f"BoardLog-v{version_name}.apk"
        if not _is_valid_github_url(
            document.get("downloadUrl"),
            f"/jy2834/BoardLog-catalog/releases/download/{tag}/{asset}",
        ):
            errors.append("downloadUrl: must identify the immutable GitHub Release APK for versionName")
        if not _is_valid_github_url(
            document.get("releasePageUrl"),
            f"/jy2834/BoardLog-catalog/releases/tag/{tag}",
        ):
            errors.append("releasePageUrl: must identify the immutable GitHub Release page for versionName")
    else:
        if "downloadUrl" in document:
            errors.append("downloadUrl: cannot validate without a valid versionName")
        if "releasePageUrl" in document:
            errors.append("releasePageUrl: cannot validate without a valid versionName")

    if apk_path is not None:
        errors.extend(_validate_apk(document, Path(apk_path)))
    return errors


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{path}: cannot read valid UTF-8 JSON: {error}") from error


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--apk", type=Path)
    parser.add_argument("--expected-published-at")
    args = parser.parse_args(argv)
    try:
        document = _read_json(args.manifest)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    errors = validate_android_update(document, args.apk, args.expected_published_at)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print(f"Validated Android update manifest: version={document['versionName']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
