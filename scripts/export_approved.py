#!/usr/bin/env python3
"""Publish reviewed BoardLog submissions without exposing private fields."""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.validate_catalog import validate_catalog


PAGES_IMAGE_PREFIX = "https://jy2834.github.io/BoardLog-catalog/catalog/images/"
NO_COVER_URL = PAGES_IMAGE_PREFIX + "no-cover.svg"
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_EXPORT_BATCH = 25
MAX_SUPPRESSION_IDS = 10_000
SUPPRESSION_PAGE_SIZE = 1_000
STABLE_KEY = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")


class ExportError(RuntimeError):
    """Fail closed while leaving the reviewed remote row retryable."""


class NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, _request, _file_pointer, _code, _message, _headers, _new_url):
        return None


@dataclass(frozen=True)
class ApprovedSubmission:
    submission_id: str
    status: str
    public_game: Mapping[str, Any]
    image_object_path: str | None
    reviewed_at: str = "2026-08-13T00:00:00Z"


@dataclass(frozen=True)
class ExportCycleResult:
    exported_ids: list[str]
    catalog_changed: bool


class ExportRemote(Protocol):
    def fetch_pending(self, submission_id: str | None) -> list[ApprovedSubmission]: ...
    def fetch_suppressions(self) -> set[str]: ...
    def download_image(self, path: str) -> bytes: ...
    def mark_exported(self, submission_ids: Sequence[str]) -> None: ...
    def delete_images(self, paths: Sequence[str]) -> None: ...


def canonical_catalog_bytes(document: Mapping[str, Any]) -> bytes:
    return (json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ExportError(f"Cannot read valid JSON from {path}: {error}") from error


def _same_public_game(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    # Postgres jsonb does not preserve object key order. Compare the public
    # payload structurally so an acknowledgement retry cannot mistake the same
    # reviewed game for a changed one merely because its fields were reordered.
    return left == right


def _validated_image_object_path(submission: ApprovedSubmission) -> str | None:
    path = submission.image_object_path
    if path is None:
        return None
    parts = path.split("/")
    if len(parts) != 2:
        raise ExportError("Submission image path does not match the private bucket layout")
    owner_id, file_name = parts
    suffix = Path(file_name).suffix.lower()
    if suffix not in (".jpg", ".webp") or Path(file_name).stem != submission.submission_id:
        raise ExportError("Submission image path does not match its submission")
    try:
        uuid.UUID(owner_id)
        uuid.UUID(Path(file_name).stem)
    except (ValueError, AttributeError, TypeError) as error:
        raise ExportError("Submission image path contains an invalid identity") from error
    return path


def _normalized_submission(submission: ApprovedSubmission) -> dict[str, Any]:
    try:
        uuid.UUID(submission.submission_id)
    except (ValueError, AttributeError, TypeError) as error:
        raise ExportError("Invalid submission identity") from error
    image_object_path = _validated_image_object_path(submission)
    game = copy.deepcopy(dict(submission.public_game))
    if submission.status == "APPROVED":
        if game.get("originSubmissionId") != submission.submission_id:
            raise ExportError("Approved game origin does not match its submission")
        game.pop("targetKey", None)
        game.pop("updateTargetKey", None)
    elif submission.status == "MERGED":
        target = game.pop("targetKey", game.get("updateTargetKey"))
        if not isinstance(target, str):
            raise ExportError("Merged submission has no explicit target key")
        game["key"] = f"merge-{submission.submission_id}"
        game["updateTargetKey"] = target
        game["catalogSource"] = "COMMUNITY"
        game["originSubmissionId"] = submission.submission_id
        game["publishedAt"] = submission.reviewed_at
    else:
        raise ExportError("Only approved or merged submissions can be exported")
    if not isinstance(game.get("key"), str) or not STABLE_KEY.fullmatch(game["key"]):
        raise ExportError("Reviewed game has an invalid stable key")
    if game.get("updateTargetKey") is not None and (
        not isinstance(game["updateTargetKey"], str)
        or not STABLE_KEY.fullmatch(game["updateTargetKey"])
    ):
        raise ExportError("Merged submission has an invalid target key")
    game["catalogSource"] = "COMMUNITY"
    game["originSubmissionId"] = submission.submission_id
    game["publishedAt"] = submission.reviewed_at
    if image_object_path:
        game["imageUrl"] = PAGES_IMAGE_PREFIX + f"{game['key']}.webp"
    elif submission.status == "APPROVED":
        game["imageUrl"] = NO_COVER_URL
    else:
        # A metadata-only merge must not erase an existing public cover merely
        # because the reviewed duplicate did not upload a replacement.
        game.pop("imageUrl", None)
    return game


def _with_public_defaults(game: Mapping[str, Any]) -> dict[str, Any]:
    completed = copy.deepcopy(dict(game))
    completed.setdefault("koreanEditionYear", None)
    completed.setdefault("listPriceWon", None)
    completed.setdefault("priceKind", "UNAVAILABLE")
    completed.setdefault("imageUrl", NO_COVER_URL)
    return completed


def _validated_suppression_ids(values: Iterable[str]) -> set[str]:
    suppressed: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            raise ExportError("Supabase returned an invalid suppression identity")
        try:
            parsed = uuid.UUID(value)
        except (ValueError, AttributeError, TypeError) as error:
            raise ExportError("Supabase returned an invalid suppression identity") from error
        canonical = str(parsed)
        if value.lower() != canonical:
            raise ExportError("Supabase returned an invalid suppression identity")
        suppressed.add(canonical)
        if len(suppressed) > MAX_SUPPRESSION_IDS:
            raise ExportError("Supabase returned too many suppression identities")
    return suppressed


def apply_approved_submissions(
    document: Mapping[str, Any],
    submissions: Iterable[ApprovedSubmission],
    *,
    generated_at: str,
    suppressed_origin_ids: Iterable[str] = (),
    schema_path: Path | None = None,
    images_dir: Path | None = None,
) -> tuple[dict[str, Any], list[str]]:
    updated = copy.deepcopy(dict(document))
    games = updated.get("games")
    if not isinstance(games, list):
        raise ExportError("Catalog games must be an array")
    by_key: dict[str, dict[str, Any]] = {
        game["key"]: copy.deepcopy(game)
        for game in games
        if isinstance(game, Mapping) and isinstance(game.get("key"), str)
    }
    if len(by_key) != len(games):
        raise ExportError("Existing catalog contains an invalid or duplicate key")
    suppressed = _validated_suppression_ids(suppressed_origin_ids)
    exported_ids: list[str] = []
    seen_submission_ids: set[str] = set()
    seen_target_keys: set[str] = set()
    suppressed_keys = [
        key for key, game in by_key.items()
        if game.get("originSubmissionId") in suppressed
    ]
    for key in suppressed_keys:
        del by_key[key]
    changed = bool(suppressed_keys)

    for submission in sorted(submissions, key=lambda row: row.submission_id):
        if submission.submission_id in seen_submission_ids:
            raise ExportError(f"Duplicate submission in export batch: {submission.submission_id}")
        seen_submission_ids.add(submission.submission_id)
        if submission.submission_id in suppressed:
            continue
        candidate = _normalized_submission(submission)
        key = candidate.get("key")
        if not isinstance(key, str):
            raise ExportError("Reviewed game has no stable key")
        target_key = candidate.get("updateTargetKey") if submission.status == "MERGED" else None
        if isinstance(target_key, str):
            if target_key in seen_target_keys:
                raise ExportError(f"Multiple submissions patch the same target: {target_key}")
            seen_target_keys.add(target_key)
        if isinstance(target_key, str) and target_key in by_key:
            candidate = {**copy.deepcopy(by_key[target_key]), **candidate}
            candidate["key"] = target_key
            candidate.pop("updateTargetKey", None)
            key = target_key
        elif isinstance(target_key, str):
            existing_patch_key = next(
                (game_key for game_key, game in by_key.items() if game.get("updateTargetKey") == target_key),
                None,
            )
            if existing_patch_key is not None:
                candidate = {**copy.deepcopy(by_key[existing_patch_key]), **candidate}
                candidate["key"] = existing_patch_key
                key = existing_patch_key
        candidate = _with_public_defaults(candidate)
        existing = by_key.get(key)
        if existing is not None:
            if existing.get("originSubmissionId") == submission.submission_id and _same_public_game(existing, candidate):
                exported_ids.append(submission.submission_id)
                continue
            if existing.get("originSubmissionId") == submission.submission_id or submission.status != "MERGED":
                raise ExportError(f"Published submission {submission.submission_id} changed after export")
        candidate_bgg = candidate.get("bggId")
        if candidate_bgg is not None:
            bgg_owner = next(
                (game_key for game_key, game in by_key.items() if game.get("bggId") == candidate_bgg and game_key != key),
                None,
            )
            if bgg_owner is not None and bgg_owner != target_key:
                raise ExportError(f"BGG ID collision with {bgg_owner}")
        if existing is None or not _same_public_game(existing, candidate):
            by_key[key] = candidate
            changed = True
        exported_ids.append(submission.submission_id)

    updated["games"] = [by_key[key] for key in sorted(by_key)]
    if changed:
        revision = updated.get("revision")
        if type(revision) is not int or revision < 1:
            raise ExportError("Catalog revision must be a positive integer")
        updated["revision"] = revision + 1
        updated["generatedAt"] = generated_at

    schema = _read_json(schema_path or REPO_ROOT / "catalog" / "schema.json")
    errors = validate_catalog(updated, schema, images_dir or REPO_ROOT / "catalog" / "images")
    if errors:
        raise ExportError("Catalog validation failed:\n" + "\n".join(errors))
    return updated, exported_ids


def convert_cover_to_webp(source: bytes, output: Path) -> None:
    if len(source) > MAX_IMAGE_BYTES:
        raise ExportError("Submission cover exceeds 2 MiB")
    try:
        from PIL import Image, ImageOps
    except ImportError as error:
        raise ExportError("Pillow is required to export reviewed images") from error
    Image.MAX_IMAGE_PIXELS = 20_000_000
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            from io import BytesIO

            with Image.open(BytesIO(source)) as original:
                original.load()
                image = ImageOps.exif_transpose(original)
                image.thumbnail((1200, 1200), Image.Resampling.LANCZOS)
                converted = image.convert("RGBA" if "A" in image.getbands() else "RGB")
                for quality in (84, 76, 68, 60, 52):
                    converted.save(output, format="WEBP", quality=quality, method=6)
                    if output.stat().st_size <= MAX_IMAGE_BYTES:
                        break
                else:
                    raise ExportError("Converted cover exceeds 2 MiB")
    except ExportError:
        raise
    except Exception as error:
        output.unlink(missing_ok=True)
        raise ExportError(f"Cannot safely convert submission cover: {error}") from error


def _prepare_validation_images(source: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for entry in source.iterdir():
        if entry.is_file():
            try:
                os.link(entry, target / entry.name)
            except OSError:
                shutil.copy2(entry, target / entry.name)


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise


def export_cycle_with_result(
    remote: ExportRemote,
    repo_root: Path,
    publish: Callable[[], None],
    *,
    submission_id: str | None = None,
    generated_at: str | None = None,
    image_converter: Callable[[bytes, Path], None] = convert_cover_to_webp,
    acknowledge: bool = True,
) -> ExportCycleResult:
    rows = remote.fetch_pending(submission_id)
    fetch_suppressions = getattr(remote, "fetch_suppressions", None)
    suppressed = _validated_suppression_ids(fetch_suppressions() if fetch_suppressions else ())
    catalog_path = repo_root / "catalog" / "catalog.json"
    schema_path = repo_root / "catalog" / "schema.json"
    images_dir = repo_root / "catalog" / "images"
    timestamp = generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    with tempfile.TemporaryDirectory(prefix="boardlog-export-") as directory:
        staged_images = Path(directory) / "images"
        _prepare_validation_images(images_dir, staged_images)
        converted_names: set[str] = set()
        for row in rows:
            if row.submission_id in suppressed:
                continue
            image_object_path = _validated_image_object_path(row)
            if image_object_path:
                game = _normalized_submission(row)
                file_name = f"{game['key']}.webp"
                if file_name in converted_names:
                    raise ExportError(f"Multiple submissions write the same cover: {file_name}")
                image_converter(remote.download_image(image_object_path), staged_images / file_name)
                converted_names.add(file_name)

        current = _read_json(catalog_path)
        updated, exported_ids = apply_approved_submissions(
            current,
            rows,
            generated_at=timestamp,
            suppressed_origin_ids=suppressed,
            schema_path=schema_path,
            images_dir=staged_images,
        )
        changed = canonical_catalog_bytes(current) != canonical_catalog_bytes(updated)
        if changed:
            _atomic_write(catalog_path, canonical_catalog_bytes(updated))
        for name in converted_names:
            if not (images_dir / name).is_file() or (images_dir / name).read_bytes() != (staged_images / name).read_bytes():
                _atomic_write(images_dir / name, (staged_images / name).read_bytes())
        if changed or exported_ids:
            publish()

    if acknowledge and exported_ids:
        exported_set = set(exported_ids)
        _acknowledge_rows(remote, [row for row in rows if row.submission_id in exported_set])
    return ExportCycleResult(exported_ids=exported_ids, catalog_changed=changed)


def export_cycle(
    remote: ExportRemote,
    repo_root: Path,
    publish: Callable[[], None],
    *,
    submission_id: str | None = None,
    generated_at: str | None = None,
    image_converter: Callable[[bytes, Path], None] = convert_cover_to_webp,
    acknowledge: bool = True,
) -> list[str]:
    """Compatibility wrapper for callers that only need exported IDs."""
    return export_cycle_with_result(
        remote,
        repo_root,
        publish,
        submission_id=submission_id,
        generated_at=generated_at,
        image_converter=image_converter,
        acknowledge=acknowledge,
    ).exported_ids


def _acknowledge_rows(remote: ExportRemote, rows: Sequence[ApprovedSubmission]) -> None:
    exported_ids = [row.submission_id for row in rows]
    remote.mark_exported(exported_ids)
    image_paths = sorted({path for row in rows if (path := _validated_image_object_path(row))})
    if image_paths:
        try:
            remote.delete_images(image_paths)
        except ExportError as error:
            print(f"warning: temporary image cleanup deferred: {error}", file=sys.stderr)


def acknowledge_exports(remote: ExportRemote, submission_ids: Sequence[str]) -> None:
    if not submission_ids or len(submission_ids) > MAX_EXPORT_BATCH:
        raise ExportError("Acknowledgement IDs must contain one bounded export batch")
    if len(set(submission_ids)) != len(submission_ids):
        raise ExportError("Acknowledgement IDs must be unique")
    rows: list[ApprovedSubmission] = []
    for submission_id in submission_ids:
        fetched = remote.fetch_pending(submission_id)
        if len(fetched) != 1 or fetched[0].submission_id != submission_id:
            raise ExportError(f"Cannot safely acknowledge exported submission {submission_id}")
        rows.append(fetched[0])
    _acknowledge_rows(remote, rows)


class SupabaseExportRemote:
    def __init__(self, url: str, secret_key: str):
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in ("", "/")
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise ExportError("SUPABASE_URL must be an HTTPS project origin")
        if not secret_key.startswith("sb_secret_"):
            raise ExportError("SUPABASE_SECRET_KEY must use a new secret API key")
        self.url = url.rstrip("/")
        self.headers = {"apikey": secret_key, "User-Agent": "BoardLog-catalog-exporter/1.0"}
        self.opener = build_opener(NoRedirectHandler())

    def _request(
        self,
        path: str,
        *,
        method: str = "GET",
        body: bytes | None = None,
        max_bytes: int = MAX_JSON_BYTES,
        extra_headers: Mapping[str, str] | None = None,
    ) -> bytes:
        headers = dict(self.headers)
        headers.update(extra_headers or {})
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = Request(self.url + path, data=body, method=method, headers=headers)
        try:
            with self.opener.open(request, timeout=30) as response:
                data = response.read(max_bytes + 1)
        except (HTTPError, URLError, TimeoutError) as error:
            raise ExportError(f"Supabase request failed: {getattr(error, 'code', 'network error')}") from error
        if len(data) > max_bytes:
            raise ExportError("Supabase response exceeded its safety limit")
        return data

    def fetch_pending(self, submission_id: str | None) -> list[ApprovedSubmission]:
        filters = [
            ("select", "id,status,public_game,image_object_path,reviewed_at"),
            ("status", "in.(APPROVED,MERGED)"),
            ("exported_at", "is.null"),
            ("order", "reviewed_at.asc"),
            ("limit", str(MAX_EXPORT_BATCH)),
        ]
        if submission_id:
            try:
                uuid.UUID(submission_id)
            except ValueError as error:
                raise ExportError("submission_id must be a UUID") from error
            filters.append(("id", f"eq.{submission_id}"))
        try:
            raw = json.loads(self._request("/rest/v1/game_submissions?" + urlencode(filters)).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ExportError("Supabase returned invalid JSON") from error
        if not isinstance(raw, list):
            raise ExportError("Supabase returned an invalid submission list")
        if len(raw) > MAX_EXPORT_BATCH:
            raise ExportError("Supabase returned more than one bounded export batch")
        rows: list[ApprovedSubmission] = []
        for value in raw:
            if (
                not isinstance(value, Mapping)
                or value.get("status") not in ("APPROVED", "MERGED")
                or not isinstance(value.get("id"), str)
                or not isinstance(value.get("public_game"), Mapping)
                or value.get("image_object_path") is not None and not isinstance(value.get("image_object_path"), str)
                or not isinstance(value.get("reviewed_at"), str)
            ):
                raise ExportError("Supabase returned an invalid approved row")
            rows.append(ApprovedSubmission(
                submission_id=value.get("id"),
                status=value.get("status"),
                public_game=value["public_game"],
                image_object_path=value.get("image_object_path"),
                reviewed_at=value.get("reviewed_at"),
            ))
        return rows

    def fetch_suppressions(self) -> set[str]:
        suppression_ids: list[str] = []
        offset = 0
        while True:
            remaining = MAX_SUPPRESSION_IDS + 1 - len(suppression_ids)
            limit = min(SUPPRESSION_PAGE_SIZE, remaining)
            filters = [
                ("select", "origin_submission_id"),
                ("order", "origin_submission_id.asc"),
                ("limit", str(limit)),
                ("offset", str(offset)),
            ]
            try:
                raw = json.loads(self._request(
                    "/rest/v1/public_catalog_suppressions?" + urlencode(filters)
                ).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ExportError("Supabase returned invalid suppression JSON") from error
            if not isinstance(raw, list):
                raise ExportError("Supabase returned an invalid suppression list")
            for value in raw:
                if not isinstance(value, Mapping) or set(value) != {"origin_submission_id"}:
                    raise ExportError("Supabase returned an invalid suppression row")
                suppression_ids.append(value["origin_submission_id"])
            if len(suppression_ids) > MAX_SUPPRESSION_IDS:
                raise ExportError("Supabase returned too many suppression identities")
            if len(raw) < limit:
                return _validated_suppression_ids(suppression_ids)
            offset += len(raw)

    def download_image(self, path: str) -> bytes:
        safe_path = "/".join(quote(part, safe="") for part in path.split("/"))
        return self._request(
            f"/storage/v1/object/submission-images/{safe_path}",
            max_bytes=MAX_IMAGE_BYTES,
        )

    def mark_exported(self, submission_ids: Sequence[str]) -> None:
        if not submission_ids:
            return
        ids = ",".join(submission_ids)
        body = json.dumps({
            "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "image_object_path": None,
        }).encode("utf-8")
        response = self._request(
            "/rest/v1/game_submissions?" + urlencode({
                "id": f"in.({ids})",
                "status": "in.(APPROVED,MERGED)",
                "exported_at": "is.null",
                "select": "id",
            }),
            method="PATCH",
            body=body,
            extra_headers={"Prefer": "return=representation"},
        )
        try:
            decoded = json.loads(response)
            if not isinstance(decoded, list):
                raise TypeError("acknowledgement is not an array")
            acknowledged = {row["id"] for row in decoded if isinstance(row, Mapping)}
        except (json.JSONDecodeError, KeyError, TypeError) as error:
            raise ExportError("Supabase returned an invalid export acknowledgement") from error
        if acknowledged != set(submission_ids):
            raise ExportError("Supabase did not acknowledge every exported submission")

    def delete_images(self, paths: Sequence[str]) -> None:
        if paths:
            self._request(
                "/storage/v1/object/submission-images",
                method="DELETE",
                body=json.dumps({"prefixes": list(paths)}).encode("utf-8"),
            )


def publish_with_git(repo_root: Path) -> None:
    status = subprocess.run(
        ["git", "status", "--porcelain", "--", "catalog"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if status.strip():
        subprocess.run(["git", "add", "catalog/catalog.json", "catalog/images"], cwd=repo_root, check=True)
        subprocess.run(["git", "diff", "--cached", "--check"], cwd=repo_root, check=True)
        subprocess.run(["git", "commit", "-m", "data: publish approved BoardLog games"], cwd=repo_root, check=True)
    subprocess.run(["git", "push", "origin", "HEAD:main"], cwd=repo_root, check=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--submission-id")
    parser.add_argument("--defer-acknowledgement", action="store_true")
    parser.add_argument("--acknowledge-ids", nargs="+")
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    args = parser.parse_args(argv)
    if args.acknowledge_ids and (args.submission_id or args.defer_acknowledgement):
        parser.error("--acknowledge-ids cannot be combined with export options")
    url = os.environ.get("SUPABASE_URL", "").strip()
    secret = os.environ.get("SUPABASE_SECRET_KEY", "").strip()
    if not url or not secret:
        print("SUPABASE_URL and SUPABASE_SECRET_KEY are required", file=sys.stderr)
        return 2
    try:
        remote = SupabaseExportRemote(url, secret)
        if args.acknowledge_ids:
            acknowledge_exports(remote, args.acknowledge_ids)
            result = ExportCycleResult(exported_ids=list(args.acknowledge_ids), catalog_changed=False)
        else:
            result = export_cycle_with_result(
                remote,
                args.repo_root,
                lambda: publish_with_git(args.repo_root),
                submission_id=args.submission_id,
                acknowledge=not args.defer_acknowledgement,
            )
    except (ExportError, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        return 1
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        try:
            with Path(github_output).open("a", encoding="utf-8") as stream:
                stream.write(f"exported_count={len(result.exported_ids)}\n")
                stream.write("exported_ids=" + ",".join(result.exported_ids) + "\n")
                stream.write(f"catalog_changed={str(result.catalog_changed).lower()}\n")
        except OSError as error:
            print(f"Cannot report exporter result to GitHub Actions: {error}", file=sys.stderr)
            return 1
    print(
        f"Exported {len(result.exported_ids)} reviewed submission(s); "
        f"catalog_changed={str(result.catalog_changed).lower()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
