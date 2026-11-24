"""Every route but the plain /health needs the key; with no key configured nothing but /health is served.
Run: python -m unittest discover -s tests (from the repo root)."""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from fastapi.testclient import TestClient

    import main  # noqa: E402
except Exception as exc:  # pragma: no cover - the web stack isn't installed
    raise unittest.SkipTest(f"fastapi test client unavailable: {exc}")


class KeyGate(unittest.TestCase):
    def setUp(self):
        self.calls = []

        async def fake_get(path, params=None):
            self.calls.append(path)
            return {"kind": "Listing", "data": {"children": []}}

        self._get = main.reddit_client.get
        main.reddit_client.get = fake_get
        self._status = main.reddit_client.status
        main.reddit_client.status = lambda: {}
        self._key = os.environ.get("REDAPI_KEY")
        self.client = TestClient(main.app)

    def tearDown(self):
        main.reddit_client.get = self._get
        main.reddit_client.status = self._status
        if self._key is None:
            os.environ.pop("REDAPI_KEY", None)
        else:
            os.environ["REDAPI_KEY"] = self._key

    def test_plain_health_is_open_and_reads_nothing(self):
        os.environ["REDAPI_KEY"] = "k" * 32
        self.assertEqual(self.client.get("/health").status_code, 200)
        self.assertEqual(self.calls, [])

    def test_deep_health_needs_the_key(self):
        os.environ["REDAPI_KEY"] = "k" * 32
        for query in ("deep=1", "deep=true", "deep=yes"):
            self.assertEqual(self.client.get(f"/health?{query}").status_code, 401, query)
        self.assertEqual(self.calls, [], "no Reddit read without the key")
        ok = self.client.get("/health?deep=1", headers={"x-api-key": "k" * 32})
        self.assertEqual(ok.status_code, 200)
        self.assertTrue(ok.json()["redditReadable"])

    def test_routes_need_the_key(self):
        os.environ["REDAPI_KEY"] = "k" * 32
        self.assertEqual(self.client.get("/r/SaaS/new").status_code, 401)
        self.assertEqual(self.client.get("/r/SaaS/new", headers={"x-api-key": "wrong"}).status_code, 401)
        self.assertEqual(self.calls, [])

    def test_without_a_key_nothing_but_health_is_served(self):
        os.environ.pop("REDAPI_KEY", None)
        self.assertEqual(self.client.get("/health").status_code, 200)
        self.assertEqual(self.client.get("/health?deep=1").status_code, 503)
        self.assertEqual(self.client.get("/r/SaaS/new").status_code, 503)
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
