import json
import tempfile
import unittest
from pathlib import Path

from scripts.validate_catalog import validate_catalog


ALLOWED_TAGS = [
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
]


def valid_game(**overrides):
    game = {
        "key": "community-azul-festival",
        "name": "아줄 페스티벌",
        "englishName": "Azul Festival",
        "aliases": ["아줄: 페스티벌"],
        "yearPublished": 2026,
        "koreanEditionYear": 2026,
        "catalogSource": "COMMUNITY",
        "entryType": "BASE_GAME",
        "minPlayers": 2,
        "maxPlayers": 4,
        "minPlayMinutes": 30,
        "maxPlayMinutes": 45,
        "tags": ["FAMILY", "TILE_PLACEMENT"],
        "bggId": 987654,
        "imageUrl": "https://jy2834.github.io/BoardLog-catalog/catalog/images/community-azul-festival.webp",
        "publicRating": 4.5,
        "weight": 2.0,
        "listPriceWon": 59000,
        "priceKind": "DOMESTIC_LIST_PRICE",
        "sourceUrls": ["https://example.com/official-game"],
        "originSubmissionId": "11111111-2222-4333-8444-555555555555",
        "publishedAt": "2026-08-12T00:00:00Z",
    }
    game.update(overrides)
    return game


def valid_catalog(*games):
    return {
        "schemaVersion": 2,
        "revision": 1,
        "generatedAt": "2026-08-12T00:00:00Z",
        "games": list(games),
    }


def schema_document():
    return {
        "schemaVersion": 2,
        "allowedTags": ALLOWED_TAGS,
        "privateFields": [
            "purchasePrice",
            "basePrice",
            "componentPrice",
            "extraComponentsPrice",
            "organizerPrice",
            "memo",
            "reviewMemo",
            "localPath",
            "imageRef",
            "ownerId",
            "ownerUserId",
            "personalRating",
        ],
    }


class PublicCatalogValidationTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.images_dir = Path(self.temp_dir.name)
        (self.images_dir / "community-azul-festival.webp").write_bytes(b"RIFF0000WEBP")

    def tearDown(self):
        self.temp_dir.cleanup()

    def errors_for(self, document, schema=None):
        return validate_catalog(document, schema or schema_document(), self.images_dir)

    def test_accepts_empty_seed_and_complete_public_game(self):
        self.assertEqual([], self.errors_for(valid_catalog()))
        self.assertEqual([], self.errors_for(valid_catalog(valid_game())))

    def test_accepts_stable_update_target_and_rejects_invalid_marker(self):
        self.assertEqual(
            [],
            self.errors_for(valid_catalog(valid_game(updateTargetKey="azul"))),
        )
        for marker in (None, "Azul", "azul/other", 123):
            with self.subTest(marker=marker):
                errors = self.errors_for(valid_catalog(valid_game(updateTargetKey=marker)))
                self.assertTrue(any("updateTargetKey" in error for error in errors))

    def test_rejects_invalid_envelope_types_and_unknown_fields(self):
        cases = [
            ({**valid_catalog(), "schemaVersion": "2"}, "schemaVersion"),
            ({**valid_catalog(), "revision": 0}, "revision"),
            ({**valid_catalog(), "generatedAt": "2026-08-12"}, "generatedAt"),
            ({**valid_catalog(), "games": {}}, "games"),
            ({**valid_catalog(), "unexpected": True}, "unexpected"),
        ]
        for document, marker in cases:
            with self.subTest(marker=marker):
                self.assertTrue(any(marker in error for error in self.errors_for(document)))

    def test_rejects_duplicate_keys_and_non_null_bgg_ids(self):
        duplicate_key = valid_catalog(valid_game(), valid_game(bggId=123456))
        duplicate_bgg = valid_catalog(
            valid_game(),
            valid_game(key="community-other", name="다른 게임", bggId=987654),
        )

        self.assertTrue(any("duplicate key" in error for error in self.errors_for(duplicate_key)))
        self.assertTrue(any("duplicate bggId" in error for error in self.errors_for(duplicate_bgg)))

    def test_rejects_unsorted_games_and_aliases(self):
        unsorted_games = valid_catalog(
            valid_game(key="community-z"),
            valid_game(key="community-a", name="에이", bggId=123456),
        )
        duplicate_alias = valid_catalog(valid_game(aliases=["Ａｚｕｌ", "azul"]))
        untrimmed_alias = valid_catalog(valid_game(aliases=["  별칭  "]))

        self.assertTrue(any("sorted" in error for error in self.errors_for(unsorted_games)))
        self.assertTrue(any("aliases" in error for error in self.errors_for(duplicate_alias)))
        self.assertTrue(any("aliases" in error for error in self.errors_for(untrimmed_alias)))

    def test_rejects_invalid_urls_tags_metrics_and_ranges(self):
        mutations = [
            ("imageUrl", "http://example.com/cover.webp"),
            ("sourceUrls", ["file:///private/source"]),
            ("tags", ["UNKNOWN"]),
            ("publicRating", 5.1),
            ("weight", 0.4),
            ("minPlayers", 0),
            ("maxPlayers", 1),
            ("minPlayMinutes", 0),
            ("maxPlayMinutes", 1),
            ("yearPublished", 1899),
            ("koreanEditionYear", 2101),
        ]
        for field, value in mutations:
            game = valid_game(**{field: value})
            if field == "maxPlayers":
                game["minPlayers"] = 2
            if field == "maxPlayMinutes":
                game["minPlayMinutes"] = 30
            with self.subTest(field=field):
                self.assertTrue(any(field in error for error in self.errors_for(valid_catalog(game))))

    def test_price_kind_and_price_are_consistent_but_list_price_is_public(self):
        self.assertEqual([], self.errors_for(valid_catalog(valid_game(listPriceWon=59000))))
        unavailable_with_price = valid_game(priceKind="UNAVAILABLE", listPriceWon=59000)
        domestic_without_price = valid_game(priceKind="DOMESTIC_LIST_PRICE", listPriceWon=None)

        self.assertTrue(any("listPriceWon" in error for error in self.errors_for(valid_catalog(unavailable_with_price))))
        self.assertTrue(any("listPriceWon" in error for error in self.errors_for(valid_catalog(domestic_without_price))))

    def test_rejects_private_fields_even_when_nested(self):
        for private_field in schema_document()["privateFields"]:
            game = valid_game()
            game[private_field] = "PRIVATE_SENTINEL"
            with self.subTest(field=private_field):
                errors = self.errors_for(valid_catalog(game))
                self.assertTrue(any(private_field in error for error in errors))

        nested = valid_game(sourceUrls=[{"memo": "PRIVATE_SENTINEL"}])
        self.assertTrue(any("memo" in error for error in self.errors_for(valid_catalog(nested))))

    def test_rejects_invalid_publication_identity_and_core_types(self):
        mutations = [
            ("key", "Not Stable"),
            ("name", ""),
            ("englishName", 123),
            ("bggId", "987654"),
            ("originSubmissionId", "not-a-uuid"),
            ("publishedAt", "2026-08-12"),
            ("catalogSource", "BUNDLED"),
            ("entryType", "UNKNOWN"),
        ]
        for field, value in mutations:
            with self.subTest(field=field):
                errors = self.errors_for(valid_catalog(valid_game(**{field: value})))
                self.assertTrue(any(field in error for error in errors))

    def test_cli_reports_path_specific_errors(self):
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog_path = root / "catalog.json"
            schema_path = root / "schema.json"
            catalog_path.write_text(json.dumps(valid_catalog(valid_game(name=""))), encoding="utf-8")
            schema_path.write_text(json.dumps(schema_document()), encoding="utf-8")
            completed = __import__("subprocess").run(
                [
                    "python3",
                    str(repo / "scripts" / "validate_catalog.py"),
                    "--catalog",
                    str(catalog_path),
                    "--schema",
                    str(schema_path),
                    "--images-dir",
                    str(self.images_dir),
                ],
                capture_output=True,
                check=False,
                text=True,
            )

        self.assertNotEqual(0, completed.returncode)
        self.assertIn("games[0].name", completed.stderr)


if __name__ == "__main__":
    unittest.main()
