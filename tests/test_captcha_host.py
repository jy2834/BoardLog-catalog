import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "catalog" / "captcha.html"
JS = ROOT / "catalog" / "captcha.js"


class CaptchaHostContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = HTML.read_text(encoding="utf-8")
        cls.js = JS.read_text(encoding="utf-8")
        cls.combined = cls.html + "\n" + cls.js

    def test_loads_turnstile_from_cloudflare_with_an_explicit_action(self):
        self.assertIn("https://challenges.cloudflare.com/turnstile/v0/api.js", self.html)
        self.assertIn('action: "boardlog_submit"', self.js)
        self.assertIn("BOARDLOG_TURNSTILE_SITE_KEY", self.html)

    def test_only_sends_the_short_lived_challenge_result_to_android(self):
        self.assertIn("BoardLogTurnstile", self.js)
        self.assertIn("postMessage", self.js)
        self.assertNotIn("localStorage", self.combined)
        self.assertNotIn("sessionStorage", self.combined)
        for private_name in ("memo", "purchasePrice", "ownerId", "gameName", "payload"):
            with self.subTest(private_name=private_name):
                self.assertNotIn(private_name, self.combined)

    def test_page_has_a_restrictive_content_security_policy(self):
        self.assertIn("Content-Security-Policy", self.html)
        self.assertIn("frame-src https://challenges.cloudflare.com", self.html)
        self.assertIn("connect-src https://challenges.cloudflare.com", self.html)


if __name__ == "__main__":
    unittest.main()
