import json
import re
import unittest
import unicodedata
from pathlib import Path
from urllib.parse import urlparse

from scripts.build_requested_community_games import (
    build_migration_sql,
    load_requested_games,
    submission_id_for,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "data" / "requested-community-games-2026-08-18.json"
MIGRATIONS = ROOT / "supabase" / "migrations"

REQUESTED_NAMES = [
    "마헤",
    "레디셋벳",
    "라스베가스",
    "7원더스",
    "오토배틀 챌린저스!",
    "SET",
    "카멜업",
    "시계탑에 흐른 피",
    "노 터치 크라켄 디럭스",
    "사보타지",
    "텔레스트레이션",
    "그랜드 호텔 오스트리아",
    "[머미] 우물에 깃든 소리",
    "[머미] 그날이 오면",
    "젝스님트",
    "타코 캣 고트 치즈 피자",
    "시크릿 히틀러",
    "[머미] 스쿨드",
    "[머미] 베르단디",
    "[머미] 우르드",
    "매직 캣츠",
    "지도제작자들",
    "[머미] 레드 X 리그렛",
    "냠냠냠",
    "[머미] 햇살고아원",
    "할리우드",
    "숲속의 음악대",
    "페이퍼 사파리: 피카츄와 친구들",
    "캔버스",
    "언더워터 항해기",
    "샤크",
    "가지각새",
    "Sounds Fishy",
    "[머미] 흰까마귀, 붉게 물들다",
    "보스크",
    "피기 옐로우",
    "보츠와나",
    "캘리코",
    "바퀴벌레 포커",
    "플립 7",
    "코요테",
    "[머미] 새장 속 제비는 꿈을 꾼다",
    "후지 플러시",
    "해녀",
    "마법의 미로",
    "다빈치 코드",
    "[머미] 양심의 기생",
    "요트 다이스",
    "[머미] 다크율에 속죄를",
    "[머미] 기상천외",
    "암네시아",
    "[머미] 게놈의 탑",
    "[머미] 탐욕의 저택",
    "핵클래드",
    "가문의 왕관",
    "맘마미아!",
    "피나 콜라다이스",
    "바탐",
    "[머미] J. 모리아티의 암약",
    "[머미] 절벽산장 살인사건",
    "[머미] 셜록: 주홍색 연구",
    "[머미] 사라진 속옷과 하늘을 나는 물고기",
    "[머미] 무령",
    "플레이 제주",
    "해녀 확장판",
    "죄악의 카니발",
    "다이스 챌린저",
    "찌리릿",
    "바보 타임",
    "[머미] 껍질 속 세상",
    "피드 더 크라켄",
    "[머미] 지구에서 사랑을 담아",
]

ALLOWED_TAGS = {
    "STRATEGY", "PARTY", "FAMILY", "COOPERATIVE", "DEDUCTION",
    "SOCIAL_DEDUCTION", "MURDER_MYSTERY", "BLUFFING", "TWO_PLAYER",
    "CARD", "DECK_BUILDING", "TILE_PLACEMENT", "WORKER_PLACEMENT",
    "ENGINE_BUILDING", "ECONOMIC", "DICE", "WORD", "TEAM",
    "NEGOTIATION", "ASYMMETRIC", "ADVENTURE", "CIVILIZATION",
    "ROUTE_BUILDING", "TRICK_TAKING",
}

FORBIDDEN_KEYS = {
    "purchasePrice", "basePrice", "componentPrice", "extraComponentsPrice",
    "organizerPrice", "memo", "reviewMemo", "localPath", "imageRef",
    "ownerId", "ownerUserId", "personalRating", "listPriceWon", "priceKind",
}


def normalized(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    value = value.replace("[머미]", "")
    return re.sub(r"[^0-9a-z가-힣]+", "", value)


def walk_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_keys(child)


class RequestedCommunityGamesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.document = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cls.requests = cls.document["requests"]

    def test_contains_the_exact_user_request_once(self):
        self.assertEqual(72, len(REQUESTED_NAMES))
        self.assertEqual(72, len(self.requests))
        self.assertEqual(REQUESTED_NAMES, [row["requestedName"] for row in self.requests])
        self.assertEqual(72, len({normalized(name) for name in REQUESTED_NAMES}))

    def test_each_request_has_one_exclusive_resolution(self):
        for row in self.requests:
            with self.subTest(name=row["requestedName"]):
                self.assertIn(row.get("resolution"), {"EXISTING", "ADD_PENDING", "ALIAS_OF_PENDING"})
                if row["resolution"] == "EXISTING":
                    self.assertRegex(row.get("existingKey", ""), r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
                    self.assertNotIn("submission", row)
                elif row["resolution"] == "ALIAS_OF_PENDING":
                    self.assertIn(row.get("targetRequestedName"), REQUESTED_NAMES)
                    self.assertNotIn("submission", row)
                    target = next(
                        candidate for candidate in self.requests
                        if candidate["requestedName"] == row["targetRequestedName"]
                    )
                    self.assertEqual("ADD_PENDING", target["resolution"])
                    searchable = [target["submission"]["name"], *target["submission"]["aliases"]]
                    self.assertIn(normalized(row["requestedName"]), {normalized(value) for value in searchable})
                else:
                    self.assertNotIn("existingKey", row)
                    self.assertIsInstance(row.get("submission"), dict)

    def test_pending_payloads_are_complete_public_and_source_backed(self):
        required = {
            "name", "aliases", "minPlayers", "maxPlayers", "minPlayMinutes",
            "maxPlayMinutes", "tags", "entryType", "sourceUrls",
        }
        for row in self.requests:
            if row["resolution"] != "ADD_PENDING":
                continue
            game = row["submission"]
            with self.subTest(name=row["requestedName"]):
                self.assertTrue(required.issubset(game))
                self.assertIsInstance(game["name"], str)
                self.assertEqual(game["name"].strip(), game["name"])
                searchable = [game["name"], *game["aliases"]]
                self.assertIn(normalized(row["requestedName"]), {normalized(value) for value in searchable})
                self.assertLessEqual(1, game["minPlayers"])
                self.assertLessEqual(game["minPlayers"], game["maxPlayers"])
                self.assertLessEqual(1, game["minPlayMinutes"])
                self.assertLessEqual(game["minPlayMinutes"], game["maxPlayMinutes"])
                self.assertIn(game["entryType"], {"BASE_GAME", "EXPANSION"})
                self.assertTrue(1 <= len(game["tags"]) <= 12)
                self.assertTrue(set(game["tags"]).issubset(ALLOWED_TAGS))
                self.assertTrue(game["sourceUrls"])
                for source in game["sourceUrls"]:
                    parsed = urlparse(source)
                    self.assertEqual("https", parsed.scheme)
                    self.assertTrue(parsed.netloc)
                    self.assertIsNone(parsed.username)
                    self.assertIsNone(parsed.password)
                self.assertFalse(FORBIDDEN_KEYS.intersection(walk_keys(game)))

    def test_murmylab_submissions_use_exact_official_scenario_sources(self):
        for row in self.requests:
            if row["resolution"] != "ADD_PENDING" or not row["requestedName"].startswith("[머미]"):
                continue
            game = row["submission"]
            with self.subTest(name=row["requestedName"]):
                self.assertIn("MURDER_MYSTERY", game["tags"])
                if any(url.startswith("https://murmylab.com/") for url in game["sourceUrls"]):
                    self.assertTrue(
                        any(re.fullmatch(r"https://murmylab\.com/scenarios/[0-9a-f-]{36}", url) for url in game["sourceUrls"]),
                        "a MURMYLAB scenario source must identify the exact game",
                    )

    def test_existing_and_pending_targets_do_not_overlap(self):
        existing = {row["existingKey"] for row in self.requests if row["resolution"] == "EXISTING"}
        pending_bgg_ids = {
            row["submission"].get("bggId")
            for row in self.requests
            if row["resolution"] == "ADD_PENDING" and row["submission"].get("bggId") is not None
        }
        self.assertEqual(len(pending_bgg_ids), len(set(pending_bgg_ids)))


class RequestedCommunityGamesMigrationTest(unittest.TestCase):
    def test_generator_loads_only_the_41_new_public_submissions(self):
        games = load_requested_games(FIXTURE)
        self.assertEqual(41, len(games))
        self.assertEqual(41, len({game["submissionId"] for game in games}))
        self.assertEqual(41, len({game["requestedName"] for game in games}))

    def test_submission_ids_are_stable_uuid_values(self):
        first = submission_id_for("마헤")
        self.assertEqual(first, submission_id_for("마헤"))
        self.assertRegex(first, r"^[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")

    def test_generated_sql_is_deterministic_idempotent_and_fail_closed(self):
        first = build_migration_sql(FIXTURE)
        second = build_migration_sql(FIXTURE)
        self.assertEqual(first, second)
        self.assertIn("count(*) into v_admin_count", first)
        self.assertIn("if v_admin_count <> 1 then", first)
        self.assertIn("status, visibility", first)
        self.assertIn("'PENDING', 'PUBLIC'", first)
        self.assertEqual(41, first.count("::uuid, v_admin_id,"))
        self.assertIn("on conflict (id) do nothing", first.lower())
        self.assertIn("action = 'SUBMITTED'", first)
        self.assertNotRegex(first, r"(?im)^\s*(delete|update)\s")
        self.assertNotIn("service_role", first.lower())

    def test_generated_migration_matches_the_checked_in_file(self):
        matches = sorted(MIGRATIONS.glob("*_seed_requested_community_games.sql"))
        self.assertEqual(1, len(matches))
        self.assertEqual(build_migration_sql(FIXTURE), matches[0].read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
