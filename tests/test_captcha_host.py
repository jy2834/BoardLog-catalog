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

    def test_loads_turnstile_from_cloudflare_without_an_explicit_render_race(self):
        self.assertIn('class="cf-turnstile"', self.html)
        self.assertNotIn('id="turnstile"', self.html)
        self.assertIn('data-action="boardlog_submit"', self.html)
        self.assertNotIn("render=explicit", self.html)
        self.assertNotIn("turnstile.render", self.js)
        self.assertNotIn("BOARDLOG_TURNSTILE_SITE_KEY", self.html)
        self.assertRegex(
            self.html,
            r'<meta name="boardlog-turnstile-site-key" content="[A-Za-z0-9_-]{20,}">',
        )

    def test_only_sends_the_short_lived_challenge_result_to_android(self):
        self.assertIn("BoardLogTurnstile", self.js)
        self.assertIn("postToken", self.js)
        self.assertNotIn("postMessage", self.js)
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
