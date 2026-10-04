"""Run with:  python -m unittest discover tests"""

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
import matching  # noqa: E402


def ok(title, query, exclude=()):
    return matching.check(title, {"query": query, "exclude": list(exclude)}, [], None)[0]


class MatchingTest(unittest.TestCase):
    def test_model_numbers_ignore_spacing(self):
        self.assertTrue(ok("Intel i9-10980 XE 18 core", "10980xe"))
        self.assertTrue(ok("Intel Core i9-10980XE", "10980xe"))
        self.assertTrue(ok("Corsair 128GB (4 x 32GB) DDR4-3200", "ddr4 4x32gb"))

    def test_cpu_query_does_not_match_gpu(self):
        self.assertFalse(ok("AMD Radeon RX 5700 XT 8GB", "5700x"))
        self.assertFalse(ok("XFX Speedster RX 7900 XTX", "7900x"))
        self.assertFalse(ok("ASRock RX 6950 XT OC", "6950x"))
        self.assertTrue(ok("AMD Ryzen 7 5700X 8-core", "5700x"))
        self.assertTrue(ok("Intel i9 7900X", "7900x"))

    def test_short_words_need_word_edges(self):
        self.assertFalse(ok("Vertx mouse", "rtx"))
        self.assertTrue(ok("RTX 4090 FE", "rtx 4090"))

    def test_alternatives_and_excludes(self):
        self.assertTrue(ok("Beelink N150 mini pc", 'n100|n150 "mini pc"'))
        self.assertFalse(ok("Ryzen 7 5700X3D", "5700x -5700x3d"))
        self.assertFalse(ok("Ryzen 7 5700X tray", "5700x", exclude=["tray"]))

    def test_junk_and_price(self):
        w = {"query": "10980xe", "exclude": [], "max_price": 300}
        self.assertFalse(matching.check("10980XE for parts", w, ["for parts"], 200)[0])
        self.assertFalse(matching.check("10980XE", w, [], 350)[0])
        self.assertTrue(matching.check("10980XE", w, [], 299)[0])

    def test_deal_pct(self):
        self.assertIsNone(matching.deal_pct(100, [200, 200, 200]))
        self.assertEqual(matching.deal_label(matching.deal_pct(150, [200] * 6)), "great")


class DbTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db._conn = None
        db.DB_PATH = Path(self.tmp.name) / "t.db"

    def tearDown(self):
        db._conn.close()
        db._conn = None
        self.tmp.cleanup()

    def test_changing_query_resets_backlog_flag(self):
        wid = db.create_watch({"name": "x", "query": "10980xe", "sources": ["ebay"]})
        db.execute("UPDATE watches SET polled_sources = '[\"ebay\"]' WHERE id = ?", (wid,))
        db.update_watch(wid, {"enabled": False, "query": "10980xe", "exclude": []})
        self.assertEqual(db.get_watch(wid)["polled_sources"], ["ebay"])
        db.update_watch(wid, {"query": "10940x"})
        self.assertEqual(db.get_watch(wid)["polled_sources"], [])


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import server
        cls.tmp = tempfile.TemporaryDirectory()
        db._conn = None
        db.DB_PATH = Path(cls.tmp.name) / "t.db"
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_port}"
        # Sign in like a browser: first-time setup sets the admin's password and a session cookie.
        import http.cookiejar
        cls.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        cls.opener.open(urllib.request.Request(cls.base + "/api/auth/setup", method="POST",
                        data=b'{"email": "admin@example.com", "password": "correct horse 1"}',
                        headers={"Content-Type": "application/json"}))

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        if db._conn:
            db._conn.close()
        db._conn = None
        cls.tmp.cleanup()

    def call(self, method, path, body=b"{}", ctype="application/json"):
        req = urllib.request.Request(self.base + path, data=body, method=method,
                                     headers={"Content-Type": ctype} if ctype else {})
        try:
            with self.opener.open(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_rejects_non_json_writes(self):
        status, _ = self.call("POST", "/api/machines", b'{"name": "x"}', "text/plain")
        self.assertEqual(status, 415)
        status, _ = self.call("POST", "/api/machines", b'{"name": "x"}')
        self.assertEqual(status, 200)

    def test_bad_number_is_400(self):
        status, _ = self.call("GET", "/api/listings?watch=abc", None, None)
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
