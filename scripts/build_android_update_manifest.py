#!/usr/bin/env python3
"""Build a deterministic BoardLog Android update manifest from an APK."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Sequence

from validate_android_update import (
    BOARDLOG_PACKAGE_NAME,
    validate_android_update,
)


RELEASE_NOTES_BY_VERSION = {
    "0.3.4": [
        "공용 목록 직접 새로고침",
        "새 버전 알림과 공개 다운로드 연결",
    ],
    "0.3.5": [
        "공용 게임과 직접 등록 게임 수를 하나로 합쳐 표시",
        "달력 사진을 날짜 칸 전체에 크게 표시",
    ],
    "0.3.6": [
        "실제 설치된 앱 버전을 자동으로 표시",
    ],
    "0.3.7": [
        "달력 기록에서 전체·내 게임·추천을 한 화면에서 검색",
        "체크박스로 여러 게임을 한 번에 선택하고 기존 콜라주 설정을 보존",
    ],
}


def build_android_update_manifest(
    *,
    apk_path: Path,
    version_code: int,
    version_name: str,
    published_at: str,
    certificate_sha256: str,
) -> dict[str, object]:
    apk_bytes = apk_path.read_bytes()
    try:
        release_notes = RELEASE_NOTES_BY_VERSION[version_name]
    except KeyError as error:
        raise ValueError(f"release notes are not audited for version {version_name}") from error
    tag = f"android-v{version_name}"
    asset = f"BoardLog-v{version_name}.apk"
    return {
        "schemaVersion": 1,
        "channel": "stable",
        "packageName": BOARDLOG_PACKAGE_NAME,
        "versionCode": version_code,
        "versionName": version_name,
        "publishedAt": published_at,
        "downloadUrl": f"https://github.com/jy2834/BoardLog-catalog/releases/download/{tag}/{asset}",
        "releasePageUrl": f"https://github.com/jy2834/BoardLog-catalog/releases/tag/{tag}",
        "sizeBytes": len(apk_bytes),
        "sha256": hashlib.sha256(apk_bytes).hexdigest(),
        "signingCertificateSha256": certificate_sha256,
        "mandatory": False,
        "releaseNotes": list(release_notes),
    }


def write_manifest_atomically(document: dict[str, object], output: Path, apk_path: Path) -> None:
    errors = validate_android_update(document, apk_path=apk_path)
    if errors:
        raise ValueError("\n".join(errors))
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent, text=True)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as temporary:
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, output)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apk", type=Path, required=True)
    parser.add_argument("--version-code", type=int, required=True)
    parser.add_argument("--version-name", required=True)
    parser.add_argument("--published-at", required=True)
    parser.add_argument("--certificate-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        document = build_android_update_manifest(
            apk_path=args.apk,
            version_code=args.version_code,
            version_name=args.version_name,
            published_at=args.published_at,
            certificate_sha256=args.certificate_sha256,
        )
        write_manifest_atomically(document, args.output, args.apk)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
