"""The API port: API only, every call needs the key, and settings/keys can't be touched through it."""

import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402


class ApiPortTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import server
        cls.server = server
        cls.tmp = tempfile.TemporaryDirectory()
        db._conn = None
        db.DB_PATH = Path(cls.tmp.name) / "t.db"
        cls.api = ThreadingHTTPServer(("127.0.0.1", 0), server.ApiHandler)
        cls.ui = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        for s in (cls.api, cls.ui):
            threading.Thread(target=s.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.api.shutdown()
        cls.ui.shutdown()
        db._conn.close()
        db._conn = None
        cls.tmp.cleanup()

    def call(self, srv, method, path, body=None, key=None, header="Authorization"):
        headers = {"Content-Type": "application/json"}
        if key:
            headers[header] = f"Bearer {key}" if header == "Authorization" else key
        req = urllib.request.Request(f"http://127.0.0.1:{srv.server_port}{path}",
                                     None if body is None else json.dumps(body).encode(), headers, method=method)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_flow(self):
        # No key made yet: the API is off.
        status, body = self.call(self.api, "GET", "/api/state")
        self.assertEqual(status, 401)
        self.assertIn("create an API key", body["error"])

        # The web page's port makes a key (the API port can't).
        self.assertEqual(self.call(self.api, "POST", "/api/api-key", {}, key="x")[0], 403)
        status, body = self.call(self.ui, "POST", "/api/api-key", {})
        key = body["api_key"]
        self.assertTrue(key.startswith("dh_") and len(key) > 30)
        self.assertIs(self.call(self.ui, "GET", "/api/state")[1]["settings"]["api_key"], True)  # never echoed

        self.assertEqual(self.call(self.api, "GET", "/api/state")[0], 401)               # no key
        self.assertEqual(self.call(self.api, "GET", "/api/state", key="dh_wrong")[0], 401)
        self.assertEqual(self.call(self.api, "GET", "/api/state", key=key)[0], 200)
        self.assertEqual(self.call(self.api, "GET", "/api/state", key=key, header="X-API-Key")[0], 200)

        status, body = self.call(self.api, "POST", "/api/watches", {"name": "n", "query": "q", "check": False}, key=key)
        self.assertEqual(status, 200)
        self.assertEqual(self.call(self.api, "GET", "/api/listings?status=all", key=key)[0], 200)

        # Settings and the page itself aren't reachable through the API port.
        self.assertEqual(self.call(self.api, "PUT", "/api/settings", {"poll_minutes": 5}, key=key)[0], 403)
        self.assertEqual(self.call(self.api, "GET", "/", key=key)[0], 404)

        # The web page's own port is unchanged (no key needed there).
        self.assertEqual(self.call(self.ui, "GET", "/api/state")[0], 200)


if __name__ == "__main__":
    unittest.main()
