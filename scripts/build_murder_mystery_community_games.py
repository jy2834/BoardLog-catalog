#!/usr/bin/env python3
"""Build an idempotent seed migration for source-backed MURMYLAB scenarios."""

from __future__ import annotations

import argparse
import json
import math
import re
import unicodedata
import uuid
from pathlib import Path
from typing import Any, Iterable


EXPECTED_GAME_COUNT = 100
EXPECTED_BUNDLED_EXCLUDED_IDS = 50
EXPECTED_BUNDLED_EXCLUDED_NAMES = 50
EXPECTED_LIVE_EXCLUDED_IDS = 19
EXPECTED_LIVE_EXCLUDED_NAMES = 21

MURMYLAB_SCENARIO_BASE_URL = "https://murmylab.com/scenarios/"
SEED_NAMESPACE = uuid.uuid5(
    uuid.NAMESPACE_URL,
    "https://boardlog.app/seeds/murder-mystery-community-games/2026-09-29",
)

ALLOWED_SUBMISSION_FIELDS = {
    "name",
    "englishName",
    "aliases",
    "minPlayers",
    "maxPlayers",
    "minPlayMinutes",
    "maxPlayMinutes",
    "tags",
    "weight",
    "yearPublished",
    "koreanEditionYear",
    "entryType",
    "sourceUrls",
    "bggId",
    "publicRating",
}
ALLOWED_TAGS = {
    "STRATEGY",
    "PARTY",
    "FAMILY",
    "COOPERATIVE",
    "DEDUCTION",
    "SOCIAL_DEDUCTION",
    "MURDER_MYSTERY",
    "BLUFFING",
    "TWO_PLAYER",
    "CARD",
    "DECK_BUILDING",
    "TILE_PLACEMENT",
    "WORKER_PLACEMENT",
    "ENGINE_BUILDING",
    "ECONOMIC",
    "DICE",
    "WORD",
    "TEAM",
    "NEGOTIATION",
    "ASYMMETRIC",
    "ADVENTURE",
    "CIVILIZATION",
    "ROUTE_BUILDING",
    "TRICK_TAKING",
}

FORBIDDEN_KEYS = {
    "purchasePrice",
    "basePrice",
    "componentPrice",
    "extraComponentsPrice",
    "organizerPrice",
    "memo",
    "reviewMemo",
    "localPath",
    "localFilePath",
    "imageRef",
    "ownerId",
    "ownerUserId",
    "anonymousUserId",
    "personalRating",
    "listPriceWon",
    "priceKind",
    "localDbId",
    "adminEmail",
    "photoExif",
}


def _normalized_key(value: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", unicodedata.normalize("NFKC", value).casefold())


FORBIDDEN_NORMALIZED_KEYS = {_normalized_key(key) for key in FORBIDDEN_KEYS}


def normalize_name(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("game names must be strings")
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = normalized.replace("[머미]", "")
    return re.sub(r"[^0-9a-z가-힣]+", "", normalized)


def _canonical_scenario_id(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a UUID string")
    try:
        canonical = str(uuid.UUID(value))
    except (ValueError, AttributeError) as error:
        raise ValueError(f"{field} must be a UUID string") from error
    if value != canonical:
        raise ValueError(f"{field} must use canonical lowercase UUID form")
    return canonical


def submission_id_for(scenario_id: str) -> str:
    try:
        canonical = str(uuid.UUID(scenario_id))
    except (ValueError, AttributeError) as error:
        raise ValueError("scenarioId must be a UUID string") from error
    return str(uuid.uuid5(SEED_NAMESPACE, canonical))


def _walk_keys(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key)
            yield from _walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_keys(child)


def _forbidden_keys(value: Any) -> set[str]:
    return {
        key
        for key in _walk_keys(value)
        if _normalized_key(key) in FORBIDDEN_NORMALIZED_KEYS
    }


def _load_exact_string_array(
    document: dict[str, Any],
    field: str,
    expected_count: int,
) -> list[str]:
    values = document.get(field)
    if not isinstance(values, list) or len(values) != expected_count:
        raise ValueError(f"{field} must contain exactly {expected_count} values")
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError(f"{field} must contain non-blank strings")
    if len(values) != len(set(values)):
        raise ValueError(f"{field} values must be unique")
    return values


def _load_excluded_ids(document: dict[str, Any]) -> set[str]:
    bundled = _load_exact_string_array(
        document,
        "existingBundledScenarioIds",
        EXPECTED_BUNDLED_EXCLUDED_IDS,
    )
    live = _load_exact_string_array(
        document,
        "existingLiveScenarioIds",
        EXPECTED_LIVE_EXCLUDED_IDS,
    )
    bundled_ids = {
        _canonical_scenario_id(value, "existingBundledScenarioIds") for value in bundled
    }
    live_ids = {
        _canonical_scenario_id(value, "existingLiveScenarioIds") for value in live
    }
    overlap = bundled_ids.intersection(live_ids)
    if overlap:
        raise ValueError(f"bundled/live scenario exclusions overlap: {sorted(overlap)}")
    return bundled_ids.union(live_ids)


def _load_excluded_names(document: dict[str, Any]) -> set[str]:
    bundled = _load_exact_string_array(
        document,
        "existingBundledNames",
        EXPECTED_BUNDLED_EXCLUDED_NAMES,
    )
    live = _load_exact_string_array(
        document,
        "existingLiveNames",
        EXPECTED_LIVE_EXCLUDED_NAMES,
    )
    bundled_names = {normalize_name(value) for value in bundled}
    live_names = {normalize_name(value) for value in live}
    if "" in bundled_names or "" in live_names:
        raise ValueError("normalized exclusion names must not be blank")
    if len(bundled_names) != len(bundled):
        raise ValueError("existingBundledNames must be unique after normalization")
    if len(live_names) != len(live):
        raise ValueError("existingLiveNames must be unique after normalization")
    overlap = bundled_names.intersection(live_names)
    if overlap:
        raise ValueError(f"bundled/live normalized name exclusions overlap: {sorted(overlap)}")
    return bundled_names.union(live_names)


def _require_positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _require_int_in_range(value: Any, field: str, minimum: int, maximum: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < minimum
        or value > maximum
    ):
        raise ValueError(f"{field} must be an integer from {minimum} through {maximum}")
    return value


def _validate_optional_number(
    submission: dict[str, Any],
    field: str,
    minimum: float,
    maximum: float,
) -> None:
    value = submission.get(field)
    if value is None:
        return
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) < minimum
        or float(value) > maximum
    ):
        raise ValueError(f"{field} must be null or a number from {minimum:g} through {maximum:g}")


def _validate_optional_year(submission: dict[str, Any], field: str) -> None:
    value = submission.get(field)
    if value is None:
        return
    _require_int_in_range(value, field, 1900, 2100)


def _validate_submission(submission: Any, scenario_id: str) -> set[str]:
    if not isinstance(submission, dict):
        raise ValueError("each game requires a submission object")

    unknown = sorted(set(submission).difference(ALLOWED_SUBMISSION_FIELDS))
    if unknown:
        raise ValueError(f"unknown submission fields: {unknown}")

    required = {
        "name",
        "aliases",
        "minPlayers",
        "maxPlayers",
        "minPlayMinutes",
        "maxPlayMinutes",
        "tags",
        "entryType",
        "sourceUrls",
    }
    missing = sorted(required.difference(submission))
    if missing:
        raise ValueError(f"submission is missing required fields: {missing}")

    name = submission["name"]
    if (
        not isinstance(name, str)
        or not 1 <= len(name) <= 200
        or name != name.strip()
    ):
        raise ValueError("submission.name must be a trimmed string from 1 through 200 characters")
    if "englishName" in submission:
        english_name = submission["englishName"]
        if (
            not isinstance(english_name, str)
            or len(english_name) > 200
            or english_name != english_name.strip()
        ):
            raise ValueError("englishName must be a trimmed string no longer than 200 characters")
    aliases = submission["aliases"]
    if not isinstance(aliases, list) or len(aliases) > 20 or any(
        not isinstance(alias, str)
        or not 1 <= len(alias) <= 200
        or alias != alias.strip()
        for alias in aliases
    ):
        raise ValueError("submission.aliases must contain at most 20 trimmed strings")

    min_players = _require_int_in_range(submission["minPlayers"], "minPlayers", 1, 100)
    max_players = _require_int_in_range(submission["maxPlayers"], "maxPlayers", 1, 100)
    min_minutes = _require_int_in_range(
        submission["minPlayMinutes"],
        "minPlayMinutes",
        1,
        10_080,
    )
    max_minutes = _require_int_in_range(
        submission["maxPlayMinutes"],
        "maxPlayMinutes",
        1,
        10_080,
    )
    if min_players > max_players:
        raise ValueError("minPlayers must not exceed maxPlayers")
    if min_minutes > max_minutes:
        raise ValueError("minPlayMinutes must not exceed maxPlayMinutes")

    tags = submission["tags"]
    if (
        not isinstance(tags, list)
        or not 1 <= len(tags) <= 12
        or any(not isinstance(tag, str) or tag not in ALLOWED_TAGS for tag in tags)
        or len(tags) != len(set(tags))
    ):
        raise ValueError("submission.tags must contain 1 through 12 unique allowed tags")
    if "MURDER_MYSTERY" not in tags:
        raise ValueError("each submission must have the MURDER_MYSTERY tag")
    if submission["entryType"] not in {"BASE_GAME", "EXPANSION"}:
        raise ValueError("entryType must be BASE_GAME or EXPANSION")

    _validate_optional_number(submission, "weight", 0.5, 5.0)
    _validate_optional_number(submission, "publicRating", 0.0, 5.0)
    _validate_optional_year(submission, "yearPublished")
    _validate_optional_year(submission, "koreanEditionYear")
    bgg_id = submission.get("bggId")
    if bgg_id is not None:
        _require_int_in_range(bgg_id, "bggId", 1, 2_147_483_647)

    exact_url = f"{MURMYLAB_SCENARIO_BASE_URL}{scenario_id}"
    if submission["sourceUrls"] != [exact_url]:
        raise ValueError("submission must use the exact MURMYLAB scenario URL for scenarioId")

    searchable_names = {normalize_name(value) for value in [name, *aliases]}
    if "" in searchable_names:
        raise ValueError("normalized selected names must not be blank")
    return searchable_names


def load_murder_mystery_games(path: Path) -> list[dict[str, Any]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("fixture must be a JSON object")

    forbidden = _forbidden_keys(document)
    if forbidden:
        raise ValueError(f"forbidden public fields: {sorted(forbidden)}")

    excluded_ids = _load_excluded_ids(document)
    excluded_names = _load_excluded_names(document)
    rows = document.get("games")
    if not isinstance(rows, list) or len(rows) != EXPECTED_GAME_COUNT:
        raise ValueError(f"games must contain exactly {EXPECTED_GAME_COUNT} entries")

    loaded: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    selected_name_owners: dict[str, str] = {}
    ranks: set[int] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("games entries must be objects")
        rank = _require_positive_int(row.get("selectionRank"), "selectionRank")
        if rank in ranks:
            raise ValueError(f"selectionRank values must be unique: {rank}")
        ranks.add(rank)

        scenario_id = _canonical_scenario_id(row.get("scenarioId"), "scenarioId")
        if scenario_id in selected_ids:
            raise ValueError(f"selected scenarioId values must be unique: {scenario_id}")
        if scenario_id in excluded_ids:
            raise ValueError(f"selected scenarioId already exists: {scenario_id}")
        selected_ids.add(scenario_id)

        source_evidence = row.get("sourceEvidence")
        if not isinstance(source_evidence, dict):
            raise ValueError("each game requires a sourceEvidence object")
        exact_url = f"{MURMYLAB_SCENARIO_BASE_URL}{scenario_id}"
        if source_evidence.get("officialScenarioUrl") != exact_url:
            raise ValueError("sourceEvidence must identify the exact MURMYLAB scenario URL")

        searchable_names = _validate_submission(row.get("submission"), scenario_id)
        collisions = searchable_names.intersection(excluded_names)
        if collisions:
            raise ValueError(f"selected normalized name already exists: {sorted(collisions)}")
        for normalized_name in searchable_names:
            prior_owner = selected_name_owners.get(normalized_name)
            if prior_owner is not None and prior_owner != scenario_id:
                raise ValueError(
                    "selected normalized name is shared by multiple scenarios: "
                    f"{normalized_name}"
                )
            selected_name_owners[normalized_name] = scenario_id

        loaded.append(
            {
                "selectionRank": rank,
                "scenarioId": scenario_id,
                "submissionId": submission_id_for(scenario_id),
                "publicGame": row["submission"],
            }
        )

    if ranks != set(range(1, EXPECTED_GAME_COUNT + 1)):
        raise ValueError(f"selectionRank must contain every value from 1 to {EXPECTED_GAME_COUNT}")
    submission_ids = [game["submissionId"] for game in loaded]
    if len(submission_ids) != len(set(submission_ids)):
        raise ValueError("generated submission IDs must be unique")
    return sorted(loaded, key=lambda game: game["selectionRank"])


def _json(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if "$game$" in encoded:
        raise ValueError("payload contains the SQL delimiter")
    return encoded


def build_migration_sql(path: Path) -> str:
    games = load_murder_mystery_games(path)
    submission_values = ",\n".join(
        "      ("
        f"'{game['submissionId']}'::uuid, v_admin_id, "
        f"$game${_json(game['publicGame'])}$game$::jsonb, 'PENDING', 'PUBLIC'"
        ")"
        for game in games
    )
    audit_values = ",\n".join(
        f"      ('{game['submissionId']}'::uuid)"
        for game in games
    )

    return f"""-- Generated by scripts/build_murder_mystery_community_games.py. Do not edit by hand.
do $$
declare
  v_admin_id uuid;
  v_admin_count integer;
begin
  select count(*) into v_admin_count from public.admin_users;
  if v_admin_count <> 1 then
    raise exception 'Expected exactly one BoardLog administrator, found %', v_admin_count;
  end if;
  select user_id into strict v_admin_id from public.admin_users;

  insert into public.game_submissions (
    id, owner_user_id, public_game, status, visibility
  )
  values
{submission_values}
  on conflict (id) do nothing;

  insert into public.moderation_events (
    submission_id, owner_user_id, actor_user_id, action, detail
  )
  select seeded.id, v_admin_id, v_admin_id, 'SUBMITTED',
         jsonb_build_object('source', 'murder-mystery-community-games-2026-09-29')
  from (values
{audit_values}
  ) as seeded(id)
  where exists (
    select 1 from public.game_submissions submission where submission.id = seeded.id
  )
  and not exists (
    select 1
    from public.moderation_events event
    where event.submission_id = seeded.id and event.action = 'SUBMITTED'
  );
end
$$;
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(build_migration_sql(args.fixture), encoding="utf-8")
    print(f"Generated {args.output}")


if __name__ == "__main__":
    main()
