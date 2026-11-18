"""The spare session takes over once when the main one is logged out.
Run: python -m unittest discover -s tests (from the repo root)."""

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import reddit_client  # noqa: E402


class _Resp:
    def __init__(self, url, status=200, text="<html>ok</html>", headers=None):
        self.url = url
        self.status_code = status
        self.text = text
        self.headers = headers or {}


class _Http:
    def __init__(self, responses):
        self.responses = list(responses)
        self.cookies = []

    async def get(self, url, params=None, allow_redirects=True, max_redirects=3, cookies=None, **_):
        self.cookies.append((cookies or {}).get("reddit_session"))
        return self.responses.pop(0)

    async def close(self):
        pass


class SpareSessionTest(unittest.TestCase):
    def setUp(self):
        self._saved = (config.REDDIT_SESSION_COOKIE, config.REDDIT_SESSION_COOKIE_SPARE)

    def tearDown(self):
        config.REDDIT_SESSION_COOKIE, config.REDDIT_SESSION_COOKIE_SPARE = self._saved

    def _client(self, responses):
        client = reddit_client.RedditClient()
        client._http = _Http(responses)
        return client

    def test_login_redirect_switches_to_the_spare_once(self):
        config.REDDIT_SESSION_COOKIE, config.REDDIT_SESSION_COOKIE_SPARE = "main-cookie", "spare-cookie"
        client = self._client([_Resp("https://www.reddit.com/login/?dest=x"), _Resp("https://www.reddit.com/r/x/")])
        text = asyncio.run(client._web_get("https://www.reddit.com/r/x/"))
        self.assertEqual(text, "<html>ok</html>")
        self.assertEqual(client._http.cookies, ["main-cookie", "spare-cookie"])
        self.assertEqual(client.status()["session"], "spare")
        # A second logout has nowhere to go: it is reported as before.
        client._http = _Http([_Resp("https://www.reddit.com/login/")])
        with self.assertRaises(reddit_client.RedditAPIError):
            asyncio.run(client._web_get("https://www.reddit.com/r/x/"))

    def test_without_a_spare_a_login_redirect_is_an_error(self):
        config.REDDIT_SESSION_COOKIE, config.REDDIT_SESSION_COOKIE_SPARE = "main-cookie", ""
        client = self._client([_Resp("https://www.reddit.com/login/")])
        with self.assertRaises(reddit_client.RedditAPIError) as caught:
            asyncio.run(client._web_get("https://www.reddit.com/r/x/"))
        self.assertIn("login redirect", str(caught.exception))
        self.assertEqual(client.status()["session"], "main")


if __name__ == "__main__":
    unittest.main()
