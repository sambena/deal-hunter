"""The Android app download: served from the downloads folder, with version info for the link.
Signed-in people and holders of a working invite link can get it; nobody else."""

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

import auth  # noqa: E402
import db  # noqa: E402


class AppDownloadTest(unittest.TestCase):
    def setUp(self):
        import server
        self.s = server
        self.tmp = tempfile.TemporaryDirectory()
        db._conn = None
        db.DB_PATH = Path(self.tmp.name) / "t.db"
        self.dl = Path(self.tmp.name) / "app"
        self.dl.mkdir()
        server.DOWNLOADS["dir"] = str(self.dl)
        self.token = auth.create_token(db.admin_id(), "device", "test")
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.s.DOWNLOADS["dir"] = None
        if db._conn:
            db._conn.close()
        db._conn = None
        self.tmp.cleanup()

    def get(self, path, signed_in=True):
        headers = {"Authorization": f"Bearer {self.token}"} if signed_in else {}
        req = urllib.request.Request(f"http://127.0.0.1:{self.httpd.server_port}{path}", headers=headers)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, r.read(), r.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read(), e.headers

    def publish(self):
        (self.dl / "deal-hunter.apk").write_bytes(b"PK fake apk")
        (self.dl / "android.json").write_text(json.dumps({"versionName": "1.2.0", "versionCode": 10200, "sha256": "ab"}))

    def test_nothing_published(self):
        self.assertEqual(json.loads(self.get("/api/app/android")[1]), {"available": False})
        self.assertEqual(self.get("/download/android")[0], 404)

    def test_published_build_with_version(self):
        self.publish()
        status, body, _ = self.get("/api/app/android")
        info = json.loads(body)
        self.assertEqual((status, info["version"], info["version_code"], info["size"]), (200, "1.2.0", 10200, 11))
        status, body, headers = self.get("/download/android")
        self.assertEqual((status, body), (200, b"PK fake apk"))
        self.assertEqual(headers["Content-Type"], "application/vnd.android.package-archive")
        self.assertIn("attachment", headers["Content-Disposition"])

    def test_signed_out_needs_a_working_invite(self):
        self.publish()
        for path in ("/api/app/android", "/download/android", "/download/android?code=made-up"):
            self.assertIn(self.get(path, signed_in=False)[0], (401, 410), path)
        code = self.s.admin_invite({}, {})["link"].split("=", 1)[1]
        self.assertEqual(self.get(f"/api/app/android?code={code}", signed_in=False)[0], 200)
        self.assertEqual(self.get(f"/download/android?code={code}", signed_in=False)[:2], (200, b"PK fake apk"))
        self.s.auth_accept({"code": code, "email": "sis@example.com", "password": "friend pass 1"}, {})
        self.assertEqual(self.get(f"/download/android?code={code}", signed_in=False)[0], 401)  # used up


class LockdownTest(unittest.TestCase):
    """Signed out, the site is only its sign-in page."""
    def setUp(self):
        import server
        self.s = server
        self.tmp = tempfile.TemporaryDirectory()
        db._conn = None
        db.DB_PATH = Path(self.tmp.name) / "t.db"
        self.token = auth.create_token(db.admin_id(), "device", "test")
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        if db._conn:
            db._conn.close()
        db._conn = None
        self.tmp.cleanup()

    get = AppDownloadTest.get

    def test_signed_out_gets_only_the_sign_in_page(self):
        status, body, _ = self.get("/", signed_in=False)
        self.assertEqual(status, 200)
        self.assertIn(b"auth-form", body)
        self.assertNotIn(b"app.js", body)
        self.assertEqual(self.get("/style.css", signed_in=False)[0], 200)
        self.assertIn(b"auth-form", self.get("/index.html", signed_in=False)[1])
        for path in ("/app.js", "/api.html", "/nope.html"):
            self.assertEqual(self.get(path, signed_in=False)[0], 401, path)
        for path in ("/api/state", "/api/made-up-route", "/api/settings"):
            self.assertEqual(self.get(path, signed_in=False)[0], 401, path)  # no 404s to map the API with
        self.assertEqual(self.get("/api/auth/status", signed_in=False)[0], 200)

    def test_signed_in_gets_the_app(self):
        status, body, _ = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"app.js", body)
        for path in ("/app.js", "/api.html"):
            self.assertEqual(self.get(path)[0], 200, path)
        self.assertEqual(self.get("/api/made-up-route")[0], 404)


if __name__ == "__main__":
    unittest.main()
