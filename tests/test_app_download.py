"""The Android app download: served from the downloads folder, with version info for the link."""

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
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.s.DOWNLOADS["dir"] = None
        if db._conn:
            db._conn.close()
        db._conn = None
        self.tmp.cleanup()

    def get(self, path):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.httpd.server_port}{path}") as r:
                return r.status, r.read(), r.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read(), e.headers

    def test_nothing_published(self):
        self.assertEqual(json.loads(self.get("/api/app/android")[1]), {"available": False})
        self.assertEqual(self.get("/download/android")[0], 404)

    def test_published_build_is_public_with_version(self):
        (self.dl / "deal-hunter.apk").write_bytes(b"PK fake apk")
        (self.dl / "android.json").write_text(json.dumps({"versionName": "1.2.0", "versionCode": 10200, "sha256": "ab"}))
        status, body, _ = self.get("/api/app/android")  # no sign-in needed
        info = json.loads(body)
        self.assertEqual((status, info["version"], info["version_code"], info["size"]), (200, "1.2.0", 10200, 11))
        status, body, headers = self.get("/download/android")
        self.assertEqual((status, body), (200, b"PK fake apk"))
        self.assertEqual(headers["Content-Type"], "application/vnd.android.package-archive")
        self.assertIn("attachment", headers["Content-Disposition"])


if __name__ == "__main__":
    unittest.main()
