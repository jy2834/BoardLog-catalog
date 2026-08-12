import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "supabase" / "functions" / "submit-game" / "index.ts"
HANDLER = ROOT / "supabase" / "functions" / "submit-game" / "handler.ts"
CONFIG = ROOT / "supabase" / "config.toml"


class EdgeFunctionContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = INDEX.read_text(encoding="utf-8")
        cls.handler = HANDLER.read_text(encoding="utf-8")
        cls.config = CONFIG.read_text(encoding="utf-8")

    def test_platform_jwt_verification_remains_enabled(self):
        function_config = self.config.split("[functions.submit-game]", 1)[1].split("\n[", 1)[0]
        self.assertIn("verify_jwt = true", function_config)

    def test_authenticates_user_bearer_before_using_edge_only_rpc(self):
        self.assertIn("authClient.auth.getUser(token)", self.index)
        self.assertIn('Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")', self.index)
        self.assertIn('client.rpc("submit_game_from_edge"', self.index)
        self.assertIn("p_owner_user_id", self.index)
        self.assertNotIn('client.rpc("submit_game_with_id"', self.index)

    def test_does_not_log_request_auth_captcha_or_public_game_content(self):
        combined = self.index + "\n" + self.handler
        self.assertNotIn("console.log", combined)
        self.assertNotIn("console.error", combined)
        self.assertNotIn("SUPABASE_SERVICE_ROLE_KEY=", combined)
        self.assertNotIn("TURNSTILE_SECRET_KEY=", combined)

    def test_handler_has_request_and_operation_bounds(self):
        self.assertIn("MAX_MULTIPART_BYTES", self.handler)
        self.assertIn("deps.timeoutMs", self.handler)
        self.assertIn("auth.removeCover(imagePath)", self.handler)


if __name__ == "__main__":
    unittest.main()
