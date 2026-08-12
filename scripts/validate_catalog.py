#!/usr/bin/env python3
"""Validate BoardLog's reviewed public catalog without third-party packages."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import unicodedata
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, List, Mapping, MutableSequence, Sequence
from urllib.parse import unquote, urlparse


ENVELOPE_FIELDS = {"schemaVersion", "revision", "generatedAt", "games"}
GAME_FIELDS = {
    "key",
    "name",
    "englishName",
    "aliases",
    "yearPublished",
    "koreanEditionYear",
    "catalogSource",
    "entryType",
    "minPlayers",
    "maxPlayers",
    "minPlayMinutes",
    "maxPlayMinutes",
    "tags",
    "bggId",
    "imageUrl",
    "publicRating",
    "weight",
    "listPriceWon",
    "priceKind",
    "sourceUrls",
    "originSubmissionId",
    "publishedAt",
}
OPTIONAL_GAME_FIELDS = {"updateTargetKey"}
PRICE_KINDS = {"DOMESTIC_LIST_PRICE", "USD_MSRP_CONVERTED", "UNAVAILABLE"}
ENTRY_TYPES = {"BASE_GAME", "EXPANSION"}
STABLE_KEY = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
PAGES_IMAGE_PREFIX = "https://jy2834.github.io/BoardLog-catalog/catalog/images/"


def _is_int(value: Any) -> bool:
    return type(value) is int


def _is_number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(float(value))


def _normalized(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _is_utc_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value.endswith("Z") or "T" not in value:
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.utcoffset() is not None


def _is_https_url(value: Any) -> bool:
    if not isinstance(value, str) or value != value.strip():
        return False
    parsed = urlparse(value)
    return parsed.scheme == "https" and bool(parsed.netloc) and not parsed.username and not parsed.password


def _walk_private_fields(value: Any, private_fields: set[str], path: str) -> Iterable[str]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key in private_fields:
                yield child_path
            yield from _walk_private_fields(child, private_fields, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_private_fields(child, private_fields, f"{path}[{index}]")


def _validate_string(
    game: Mapping[str, Any],
    field: str,
    path: str,
    errors: MutableSequence[str],
    *,
    allow_empty: bool = False,
) -> None:
    value = game.get(field)
    if not isinstance(value, str):
        errors.append(f"{path}.{field}: must be a string")
    elif value != value.strip() or (not allow_empty and not value):
        errors.append(f"{path}.{field}: must be non-empty and trimmed")


def _validate_optional_year(game: Mapping[str, Any], field: str, path: str, errors: MutableSequence[str]) -> None:
    value = game.get(field)
    if value is not None and (not _is_int(value) or value < 1900 or value > 2100):
        errors.append(f"{path}.{field}: must be null or an integer from 1900 through 2100")


def _validate_game(
    game: Any,
    index: int,
    allowed_tags: set[str],
    private_fields: set[str],
    images_dir: Path,
) -> List[str]:
    path = f"games[{index}]"
    errors: List[str] = []
    if not isinstance(game, Mapping):
        return [f"{path}: must be an object"]

    unknown = sorted(set(game) - GAME_FIELDS - OPTIONAL_GAME_FIELDS)
    for field in unknown:
        errors.append(f"{path}.{field}: unknown public catalog field")
    for private_path in _walk_private_fields(game, private_fields, path):
        errors.append(f"{private_path}: private field is forbidden")
    for field in sorted(GAME_FIELDS - set(game)):
        errors.append(f"{path}.{field}: missing required field")

    _validate_string(game, "key", path, errors)
    key = game.get("key")
    if isinstance(key, str) and not STABLE_KEY.fullmatch(key):
        errors.append(f"{path}.key: must be a lowercase stable key")
    _validate_string(game, "name", path, errors)
    _validate_string(game, "englishName", path, errors, allow_empty=True)

    aliases = game.get("aliases")
    if not isinstance(aliases, list):
        errors.append(f"{path}.aliases: must be an array of strings")
    else:
        normalized_aliases: set[str] = set()
        for alias_index, alias in enumerate(aliases):
            alias_path = f"{path}.aliases[{alias_index}]"
            if not isinstance(alias, str) or not alias or alias != alias.strip():
                errors.append(f"{alias_path}: must be a non-empty trimmed string")
                continue
            normalized = _normalized(alias)
            if normalized in normalized_aliases:
                errors.append(f"{path}.aliases: duplicate normalized alias {alias!r}")
            normalized_aliases.add(normalized)

    _validate_optional_year(game, "yearPublished", path, errors)
    _validate_optional_year(game, "koreanEditionYear", path, errors)
    if game.get("catalogSource") != "COMMUNITY":
        errors.append(f"{path}.catalogSource: must be COMMUNITY")
    if game.get("entryType") not in ENTRY_TYPES:
        errors.append(f"{path}.entryType: must be BASE_GAME or EXPANSION")

    for minimum, maximum, upper_bound in (
        ("minPlayers", "maxPlayers", 100),
        ("minPlayMinutes", "maxPlayMinutes", 10_080),
    ):
        minimum_value = game.get(minimum)
        maximum_value = game.get(maximum)
        if not _is_int(minimum_value) or minimum_value < 1 or minimum_value > upper_bound:
            errors.append(f"{path}.{minimum}: must be an integer from 1 through {upper_bound}")
        if not _is_int(maximum_value) or maximum_value < 1 or maximum_value > upper_bound:
            errors.append(f"{path}.{maximum}: must be an integer from 1 through {upper_bound}")
        if _is_int(minimum_value) and _is_int(maximum_value) and maximum_value < minimum_value:
            errors.append(f"{path}.{maximum}: must be greater than or equal to {minimum}")

    tags = game.get("tags")
    if not isinstance(tags, list) or not tags:
        errors.append(f"{path}.tags: must be a non-empty array")
    else:
        seen_tags: set[str] = set()
        for tag_index, tag in enumerate(tags):
            if not isinstance(tag, str) or tag not in allowed_tags:
                errors.append(f"{path}.tags[{tag_index}]: unknown tag {tag!r}")
            elif tag in seen_tags:
                errors.append(f"{path}.tags[{tag_index}]: duplicate tag {tag}")
            seen_tags.add(tag)

    bgg_id = game.get("bggId")
    if bgg_id is not None and (not _is_int(bgg_id) or bgg_id <= 0):
        errors.append(f"{path}.bggId: must be null or a positive integer")

    image_url = game.get("imageUrl")
    if not _is_https_url(image_url):
        errors.append(f"{path}.imageUrl: must be an HTTPS URL")
    elif image_url.startswith(PAGES_IMAGE_PREFIX):
        file_name = unquote(image_url[len(PAGES_IMAGE_PREFIX) :])
        if not file_name or "/" in file_name or "\\" in file_name:
            errors.append(f"{path}.imageUrl: invalid local image name")
        elif not (images_dir / file_name).is_file():
            errors.append(f"{path}.imageUrl: referenced catalog image is missing")

    for field, low, high in (("publicRating", 0.0, 5.0), ("weight", 0.5, 5.0)):
        value = game.get(field)
        if value is not None and (not _is_number(value) or not low <= float(value) <= high):
            errors.append(f"{path}.{field}: must be null or a number from {low:g} through {high:g}")

    price_kind = game.get("priceKind")
    list_price = game.get("listPriceWon")
    if price_kind not in PRICE_KINDS:
        errors.append(f"{path}.priceKind: unknown price kind")
    if list_price is not None and (not _is_int(list_price) or list_price <= 0):
        errors.append(f"{path}.listPriceWon: must be null or a positive integer")
    if (price_kind == "UNAVAILABLE") != (list_price is None):
        errors.append(f"{path}.listPriceWon: price and priceKind must agree")

    source_urls = game.get("sourceUrls")
    if not isinstance(source_urls, list) or not source_urls:
        errors.append(f"{path}.sourceUrls: must be a non-empty array")
    else:
        normalized_urls: set[str] = set()
        for source_index, source_url in enumerate(source_urls):
            source_path = f"{path}.sourceUrls[{source_index}]"
            if not _is_https_url(source_url):
                errors.append(f"{source_path}: must be an HTTPS URL")
            elif source_url in normalized_urls:
                errors.append(f"{source_path}: duplicate source URL")
            normalized_urls.add(source_url) if isinstance(source_url, str) else None

    submission_id = game.get("originSubmissionId")
    try:
        uuid.UUID(submission_id) if isinstance(submission_id, str) else (_ for _ in ()).throw(ValueError())
    except (ValueError, AttributeError):
        errors.append(f"{path}.originSubmissionId: must be a UUID string")
    if not _is_utc_timestamp(game.get("publishedAt")):
        errors.append(f"{path}.publishedAt: must be an RFC 3339 UTC timestamp")
    if "updateTargetKey" in game:
        update_target = game.get("updateTargetKey")
        if not isinstance(update_target, str) or not STABLE_KEY.fullmatch(update_target):
            errors.append(f"{path}.updateTargetKey: must be a lowercase stable key when present")
    return errors


def validate_catalog(document: Any, schema: Any, images_dir: Path) -> List[str]:
    errors: List[str] = []
    if not isinstance(schema, Mapping):
        return ["schema: must be an object"]
    allowed_tags_raw = schema.get("allowedTags")
    private_fields_raw = schema.get("privateFields")
    if schema.get("schemaVersion") != 2:
        errors.append("schema.schemaVersion: must equal 2")
    if not isinstance(allowed_tags_raw, list) or not all(isinstance(tag, str) for tag in allowed_tags_raw):
        errors.append("schema.allowedTags: must be an array of strings")
        allowed_tags: set[str] = set()
    else:
        allowed_tags = set(allowed_tags_raw)
    if not isinstance(private_fields_raw, list) or not all(isinstance(field, str) for field in private_fields_raw):
        errors.append("schema.privateFields: must be an array of strings")
        private_fields: set[str] = set()
    else:
        private_fields = set(private_fields_raw)

    if not isinstance(document, Mapping):
        return errors + ["catalog: must be an object"]
    for field in sorted(set(document) - ENVELOPE_FIELDS):
        errors.append(f"{field}: unexpected envelope field")
    for field in sorted(ENVELOPE_FIELDS - set(document)):
        errors.append(f"{field}: missing required field")
    if not _is_int(document.get("schemaVersion")) or document.get("schemaVersion") != 2:
        errors.append("schemaVersion: must be integer 2")
    if not _is_int(document.get("revision")) or document.get("revision") < 1:
        errors.append("revision: must be a positive integer")
    if not _is_utc_timestamp(document.get("generatedAt")):
        errors.append("generatedAt: must be an RFC 3339 UTC timestamp")

    games = document.get("games")
    if not isinstance(games, list):
        return errors + ["games: must be an array"]
    keys: dict[str, int] = {}
    bgg_ids: dict[int, int] = {}
    for index, game in enumerate(games):
        errors.extend(_validate_game(game, index, allowed_tags, private_fields, Path(images_dir)))
        if isinstance(game, Mapping):
            key = game.get("key")
            if isinstance(key, str):
                if key in keys:
                    errors.append(f"games[{index}].key: duplicate key also used by games[{keys[key]}]")
                keys.setdefault(key, index)
            bgg_id = game.get("bggId")
            if _is_int(bgg_id):
                if bgg_id in bgg_ids:
                    errors.append(f"games[{index}].bggId: duplicate bggId also used by games[{bgg_ids[bgg_id]}]")
                bgg_ids.setdefault(bgg_id, index)
    game_keys = [game.get("key") for game in games if isinstance(game, Mapping) and isinstance(game.get("key"), str)]
    if game_keys != sorted(game_keys):
        errors.append("games: entries must be sorted by key")
    return errors


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{path}: cannot read valid UTF-8 JSON: {error}") from error


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--schema", type=Path, required=True)
    parser.add_argument("--images-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        document = _read_json(args.catalog)
        schema = _read_json(args.schema)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    errors = validate_catalog(document, schema, args.images_dir)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print(f"Validated public catalog: revision={document['revision']} games={len(document['games'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
