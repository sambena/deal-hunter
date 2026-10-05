"""Admin-only settings: members see and change only their own area, junk words, Discord and AI."""

import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402


class AdminSettingsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db._conn, db.DB_PATH = None, Path(self.tmp.name) / "t.db"
        import server
        self.server = server
        self.member = db.execute("INSERT INTO users (email, name, role, created_at) VALUES ('f@x.y', 'F', 'member', ?)",
                                 (time.time(),))
        db.update_settings({"ebay_client_id": "sam-id", "ebay_client_secret": "sam-secret", "poll_minutes": 15,
                            "ha_url": "http://192.168.86.131", "ha_token": "sam-ha"})

    def tearDown(self):
        if db._conn:
            db._conn.close()
        db._conn = None
        self.tmp.cleanup()

    def test_member_changes_only_their_own(self):
        with db.as_user(self.member):
            out = self.server.put_settings({"zip_code": "38375", "junk_terms": ["broken"], "discord_deals_only": False,
                                            "gemini_model": "gemini-3.5-flash-lite", "poll_minutes": 1,
                                            "ebay_client_id": "hijack", "ha_url": "https://example.com",
                                            "sources_enabled": {"ebay": False}}, {})
            mine = db.get_settings()
        self.assertEqual((mine["zip_code"], mine["junk_terms"], mine["discord_deals_only"]), ("38375", ["broken"], False))
        self.assertEqual(mine["ha_url"], "")  # Home Assistant is the admin's
        admin = db.get_settings(db.admin_id())
        self.assertEqual((admin["ebay_client_id"], admin["poll_minutes"], admin["sources_enabled"]["ebay"]),
                         ("sam-id", 15, True))
        self.assertNotIn("ebay_client_id", out)

    def test_member_sees_only_their_own(self):
        with db.as_user(self.member):
            s = self.server.put_settings({}, {})
        self.assertTrue(set(s) <= db.MEMBER_KEYS | db.MEMBER_READS)
        for hidden in ("ebay_client_id", "ebay_client_secret", "bestbuy_api_key", "api_key", "ha_url", "ha_token",
                       "reddit_subs"):
            self.assertNotIn(hidden, s)
        self.assertIn("sources_enabled", s)
        admin = self.server.put_settings({}, {})
        self.assertEqual((admin["ebay_client_id"], admin["ebay_client_secret"]), ("sam-id", True))

    def test_home_assistant_import_is_admin_only(self):
        with db.as_user(self.member):
            for call in (lambda: self.server.ha_candidates({}, {}), lambda: self.server.ha_import({"devices": []}, {})):
                with self.assertRaises(self.server.HTTPError) as cm:
                    call()
                self.assertEqual(cm.exception.status, 403)


if __name__ == "__main__":
    unittest.main()
