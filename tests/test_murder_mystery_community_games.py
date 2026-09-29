import copy
import json
import re
import tempfile
import unittest
import uuid
from pathlib import Path

from scripts.build_murder_mystery_community_games import (
    EXPECTED_BUNDLED_EXCLUDED_IDS,
    EXPECTED_BUNDLED_EXCLUDED_NAMES,
    EXPECTED_GAME_COUNT,
    EXPECTED_LIVE_EXCLUDED_IDS,
    EXPECTED_LIVE_EXCLUDED_NAMES,
    build_migration_sql,
    load_murder_mystery_games,
    normalize_name,
    submission_id_for,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "data" / "murder-mystery-community-games-2026-09-29.json"
REVIEW_DOC = ROOT / "docs" / "murder-mystery-community-games-2026-09-29.md"
MIGRATIONS = ROOT / "supabase" / "migrations"


def scenario_id(number: int) -> str:
    return str(uuid.UUID(int=number))


def valid_document() -> dict:
    bundled_ids = [scenario_id(number) for number in range(1, 51)]
    live_ids = [scenario_id(number) for number in range(51, 70)]
    games = []
    for rank in range(1, EXPECTED_GAME_COUNT + 1):
        selected_id = scenario_id(1_000 + rank)
        official_url = f"https://murmylab.com/scenarios/{selected_id}"
        games.append(
            {
                "selectionRank": rank,
                "scenarioId": selected_id,
                "sourceEvidence": {
                    "officialScenarioUrl": official_url,
                    "capturedAt": "2026-09-29",
                    "players": 4,
                    "playTimeMinutes": 120,
                    "likes": rank,
                    "playCount": rank * 2,
                    "reviews": rank % 7,
                    "rating": 4.0,
                },
                "submission": {
                    "name": f"신규 머더미스터리 {rank:03d}",
                    "aliases": [f"머미 신규 {rank:03d}"],
                    "minPlayers": 4,
                    "maxPlayers": 4,
                    "minPlayMinutes": 120,
                    "maxPlayMinutes": 120,
                    "tags": ["DEDUCTION", "MURDER_MYSTERY"],
                    "entryType": "BASE_GAME",
                    "sourceUrls": [official_url],
                },
            }
        )
    return {
        "schemaVersion": 1,
        "source": "MURMYLAB official scenario pages",
        "capturedAt": "2026-09-29",
        "existingBundledScenarioIds": bundled_ids,
        "existingBundledNames": [f"내장 머더미스터리 {number:03d}" for number in range(1, 51)],
        "existingLiveScenarioIds": live_ids,
        "existingLiveNames": [f"공개 머더미스터리 {number:03d}" for number in range(1, 22)],
        "games": games,
    }


def write_document(document: dict, directory: str) -> Path:
    path = Path(directory) / "fixture.json"
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return path


class MurderMysteryCommunityGamesGeneratorTest(unittest.TestCase):
    def test_loads_exactly_100_games_and_keeps_audit_fields_out_of_public_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            games = load_murder_mystery_games(write_document(valid_document(), directory))

        self.assertEqual(100, len(games))
        self.assertEqual(list(range(1, 101)), [game["selectionRank"] for game in games])
        self.assertEqual(100, len({game["scenarioId"] for game in games}))
        self.assertEqual(100, len({game["submissionId"] for game in games}))
        self.assertNotIn("sourceEvidence", games[0]["publicGame"])
        self.assertNotIn("selectionRank", games[0]["publicGame"])

    def test_submission_ids_are_stable_uuid_v5_values_based_on_scenario_id(self):
        selected_id = scenario_id(1_234)
        first = submission_id_for(selected_id)
        self.assertEqual(first, submission_id_for(selected_id.upper()))
        self.assertRegex(
            first,
            r"^[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
        )

    def test_generated_sql_is_deterministic_idempotent_and_fail_closed(self):
        document = valid_document()
        document["games"][0]["sourceEvidence"]["researchNote"] = "AUDIT_ONLY_SENTINEL"
        with tempfile.TemporaryDirectory() as directory:
            path = write_document(document, directory)
            first = build_migration_sql(path)
            document["games"].reverse()
            path = write_document(document, directory)
            second = build_migration_sql(path)

        self.assertEqual(first, second)
        self.assertIn("count(*) into v_admin_count", first)
        self.assertIn("if v_admin_count <> 1 then", first)
        self.assertIn("status, visibility", first)
        self.assertIn("'PENDING', 'PUBLIC'", first)
        self.assertEqual(100, first.count("::uuid, v_admin_id,"))
        self.assertIn("on conflict (id) do nothing", first.lower())
        self.assertIn("action = 'SUBMITTED'", first)
        self.assertIn("murder-mystery-community-games-2026-09-29", first)
        self.assertNotIn("AUDIT_ONLY_SENTINEL", first)
        self.assertNotIn("sourceEvidence", first)
        self.assertNotIn("selectionRank", first)
        self.assertNotRegex(first, r"(?im)^\s*(delete|update)\s")
        self.assertNotIn("service_role", first.lower())

    def test_rejects_any_count_other_than_100(self):
        document = valid_document()
        document["games"].pop()
        with tempfile.TemporaryDirectory() as directory:
            path = write_document(document, directory)
            with self.assertRaisesRegex(ValueError, "exactly 100"):
                load_murder_mystery_games(path)

    def test_rejects_wrong_exclusion_snapshot_counts(self):
        expected = {
            "existingBundledScenarioIds": EXPECTED_BUNDLED_EXCLUDED_IDS,
            "existingBundledNames": EXPECTED_BUNDLED_EXCLUDED_NAMES,
            "existingLiveScenarioIds": EXPECTED_LIVE_EXCLUDED_IDS,
            "existingLiveNames": EXPECTED_LIVE_EXCLUDED_NAMES,
        }
        for field, count in expected.items():
            document = valid_document()
            self.assertEqual(count, len(document[field]))
            document[field].pop()
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                path = write_document(document, directory)
                with self.assertRaisesRegex(ValueError, re.escape(field)):
                    load_murder_mystery_games(path)

    def test_rejects_scenario_ids_already_in_bundled_or_live_snapshots(self):
        for field in ("existingBundledScenarioIds", "existingLiveScenarioIds"):
            document = valid_document()
            document["games"][0]["scenarioId"] = document[field][0]
            exact_url = f"https://murmylab.com/scenarios/{document[field][0]}"
            document["games"][0]["submission"]["sourceUrls"] = [exact_url]
            document["games"][0]["sourceEvidence"]["officialScenarioUrl"] = exact_url
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                path = write_document(document, directory)
                with self.assertRaisesRegex(ValueError, "already exists"):
                    load_murder_mystery_games(path)

    def test_rejects_normalized_names_or_aliases_in_bundled_or_live_snapshots(self):
        for field in ("existingBundledNames", "existingLiveNames"):
            document = valid_document()
            document["games"][0]["submission"]["aliases"].append(f"[머미] {document[field][0]}")
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                path = write_document(document, directory)
                with self.assertRaisesRegex(ValueError, "normalized name"):
                    load_murder_mystery_games(path)

    def test_rejects_mismatched_or_noncanonical_murmylab_sources(self):
        document = valid_document()
        different_id = scenario_id(99_999)
        document["games"][0]["submission"]["sourceUrls"] = [
            f"https://murmylab.com/scenarios/{different_id}"
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = write_document(document, directory)
            with self.assertRaisesRegex(ValueError, "exact MURMYLAB scenario URL"):
                load_murder_mystery_games(path)

    def test_rejects_private_fields_even_when_nested_in_audit_evidence(self):
        for container in ("submission", "sourceEvidence"):
            document = valid_document()
            document["games"][0][container]["nested"] = {"ownerUserId": "private"}
            with self.subTest(container=container), tempfile.TemporaryDirectory() as directory:
                path = write_document(document, directory)
                with self.assertRaisesRegex(ValueError, "forbidden public fields"):
                    load_murder_mystery_games(path)

    def test_rejects_submission_fields_the_edge_validator_would_reject(self):
        cases = []

        unknown_field = valid_document()
        unknown_field["games"][0]["submission"]["catalogSource"] = "COMMUNITY"
        cases.append((unknown_field, "unknown submission fields"))

        too_many_aliases = valid_document()
        too_many_aliases["games"][0]["submission"]["aliases"] = [
            f"별칭 {number}" for number in range(21)
        ]
        cases.append((too_many_aliases, "aliases"))

        invalid_english_name = valid_document()
        invalid_english_name["games"][0]["submission"]["englishName"] = None
        cases.append((invalid_english_name, "englishName"))

        too_many_players = valid_document()
        too_many_players["games"][0]["submission"]["minPlayers"] = 101
        too_many_players["games"][0]["submission"]["maxPlayers"] = 101
        cases.append((too_many_players, "minPlayers"))

        too_many_minutes = valid_document()
        too_many_minutes["games"][0]["submission"]["maxPlayMinutes"] = 10_081
        cases.append((too_many_minutes, "maxPlayMinutes"))

        duplicate_tags = valid_document()
        duplicate_tags["games"][0]["submission"]["tags"] = [
            "MURDER_MYSTERY",
            "MURDER_MYSTERY",
        ]
        cases.append((duplicate_tags, "tags"))

        unknown_tag = valid_document()
        unknown_tag["games"][0]["submission"]["tags"] = [
            "MURDER_MYSTERY",
            "NOT_A_REAL_TAG",
        ]
        cases.append((unknown_tag, "tags"))

        invalid_public_rating = valid_document()
        invalid_public_rating["games"][0]["submission"]["publicRating"] = 5.1
        cases.append((invalid_public_rating, "publicRating"))

        invalid_weight = valid_document()
        invalid_weight["games"][0]["submission"]["weight"] = 0.4
        cases.append((invalid_weight, "weight"))

        invalid_year = valid_document()
        invalid_year["games"][0]["submission"]["yearPublished"] = 1899
        cases.append((invalid_year, "yearPublished"))

        invalid_korean_year = valid_document()
        invalid_korean_year["games"][0]["submission"]["koreanEditionYear"] = 2101
        cases.append((invalid_korean_year, "koreanEditionYear"))

        invalid_bgg_id = valid_document()
        invalid_bgg_id["games"][0]["submission"]["bggId"] = 0
        cases.append((invalid_bgg_id, "bggId"))

        for document, message in cases:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as directory:
                path = write_document(document, directory)
                with self.assertRaisesRegex(ValueError, message):
                    load_murder_mystery_games(path)

    def test_accepts_all_edge_validated_optional_public_fields(self):
        document = valid_document()
        document["games"][0]["submission"].update(
            {
                "englishName": "Verified Mystery",
                "weight": 2.5,
                "yearPublished": 2025,
                "koreanEditionYear": 2026,
                "bggId": 2_147_483_647,
                "publicRating": 4.5,
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            games = load_murder_mystery_games(write_document(document, directory))

        self.assertEqual("Verified Mystery", games[0]["publicGame"]["englishName"])

    def test_rejects_duplicate_selection_ranks_and_normalized_selected_names(self):
        mutations = []
        duplicate_rank = valid_document()
        duplicate_rank["games"][1]["selectionRank"] = 1
        mutations.append((duplicate_rank, "selectionRank"))
        duplicate_name = valid_document()
        duplicate_name["games"][1]["submission"]["name"] = "[머미] 신규 머더미스터리 001"
        mutations.append((duplicate_name, "normalized name"))

        for document, message in mutations:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as directory:
                path = write_document(document, directory)
                with self.assertRaisesRegex(ValueError, message):
                    load_murder_mystery_games(path)

    def test_normalize_name_ignores_spacing_punctuation_case_and_murmi_prefix(self):
        self.assertEqual(normalize_name("[머미] Red X 리그렛"), normalize_name("red-x 리그렛"))


class MurderMysteryCommunityGamesCheckedInContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.document = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_checked_in_fixture_contains_the_exact_source_backed_snapshot(self):
        games = load_murder_mystery_games(FIXTURE)
        self.assertEqual(EXPECTED_GAME_COUNT, len(games))

    def test_checked_in_selection_evidence_is_complete_and_self_consistent(self):
        self.assertEqual(493, self.document["sourceScenarioCount"])
        self.assertRegex(self.document["sourceSnapshotSha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(1_500, self.document["identityAudit"]["bundledCatalogGameCount"])
        self.assertEqual(44, self.document["identityAudit"]["liveCommunityGameCount"])
        self.assertEqual(141, self.document["identityAudit"]["eligibleAfterIdentityExclusions"])
        self.assertEqual(27, self.document["identityAudit"]["manualReviewHoldoutCount"])
        self.assertEqual(27, len(self.document["reviewHoldouts"]))

        selected_ids = {row["scenarioId"] for row in self.document["games"]}
        holdout_ids = {row["scenarioId"] for row in self.document["reviewHoldouts"]}
        self.assertEqual(27, len(holdout_ids))
        self.assertFalse(selected_ids.intersection(holdout_ids))

        total_plays = total_reviews = total_likes = reviewed_games = 0
        for row in self.document["games"]:
            evidence = row["sourceEvidence"]
            submission = row["submission"]
            with self.subTest(name=submission["name"]):
                self.assertEqual("2026-09-29", evidence["capturedAt"])
                self.assertTrue(evidence["isDomestic"])
                self.assertEqual("오프라인", evidence["playType"])
                self.assertIn(evidence["category"], {"추천", "신작소개"})
                source_players = [int(value) for value in re.findall(r"\d+", evidence["playersText"])]
                source_minutes = [int(value) for value in re.findall(r"\d+", evidence["playTimeText"])]
                self.assertEqual(submission["minPlayers"], min(source_players))
                self.assertEqual(submission["maxPlayers"], max(source_players))
                self.assertEqual(submission["minPlayMinutes"], min(source_minutes))
                self.assertEqual(submission["maxPlayMinutes"], max(source_minutes))
                self.assertEqual(
                    3 * evidence["playCount"] + 2 * evidence["reviews"] + evidence["likes"],
                    evidence["popularityScore"],
                )
                if evidence["reviews"] > 0:
                    self.assertEqual(float(evidence["rating"]), submission["publicRating"])
                else:
                    self.assertNotIn("publicRating", submission)
            total_plays += evidence["playCount"]
            total_reviews += evidence["reviews"]
            total_likes += evidence["likes"]
            reviewed_games += evidence["reviews"] > 0

        self.assertEqual(400, total_plays)
        self.assertEqual(155, total_reviews)
        self.assertEqual(159, total_likes)
        self.assertEqual(74, reviewed_games)

    def test_generated_migration_matches_the_checked_in_file(self):
        matches = sorted(MIGRATIONS.glob("*_seed_murder_mystery_community_games.sql"))
        self.assertEqual(1, len(matches))
        self.assertEqual(build_migration_sql(FIXTURE), matches[0].read_text(encoding="utf-8"))

    def test_generated_migration_has_one_submission_and_one_audit_id_per_game(self):
        migration = build_migration_sql(FIXTURE)
        submission_section, audit_section = migration.split(
            "insert into public.moderation_events",
            maxsplit=1,
        )
        uuid_pattern = r"'([0-9a-f-]{36})'::uuid"
        submission_ids = re.findall(uuid_pattern, submission_section)
        audit_ids = re.findall(uuid_pattern, audit_section)

        self.assertEqual(EXPECTED_GAME_COUNT, len(submission_ids))
        self.assertEqual(EXPECTED_GAME_COUNT, len(set(submission_ids)))
        self.assertEqual(EXPECTED_GAME_COUNT, len(audit_ids))
        self.assertEqual(EXPECTED_GAME_COUNT, len(set(audit_ids)))
        self.assertEqual(set(submission_ids), set(audit_ids))

    def test_review_document_links_every_selected_and_held_off_scenario(self):
        review = REVIEW_DOC.read_text(encoding="utf-8")
        scenario_ids = [row["scenarioId"] for row in self.document["games"]]
        scenario_ids.extend(row["scenarioId"] for row in self.document["reviewHoldouts"])
        self.assertEqual(127, len(scenario_ids))
        self.assertEqual(127, len(set(scenario_ids)))
        for scenario_id in scenario_ids:
            with self.subTest(scenario_id=scenario_id):
                self.assertEqual(
                    1,
                    review.count(f"https://murmylab.com/scenarios/{scenario_id}"),
                )


if __name__ == "__main__":
    unittest.main()
