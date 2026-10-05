"""Checking at set times each day (in a time zone), or every few minutes."""

import datetime
import sys
import tempfile
import unittest
import zoneinfo
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402
import poller  # noqa: E402

try:
    DENVER = zoneinfo.ZoneInfo("America/Denver")
except zoneinfo.ZoneInfoNotFoundError:  # Windows without the tzdata package; the server's Linux has zones
    DENVER = None


def at(y, mo, d, h, mi):
    return datetime.datetime(y, mo, d, h, mi, tzinfo=DENVER).timestamp()


@unittest.skipIf(DENVER is None, "no time zone database here")
class CheckTimesTest(unittest.TestCase):
    def test_clean_times(self):
        self.assertEqual(poller.clean_times("18:00, 7:00; 25:00, junk, 07:00"), ["07:00", "18:00"])
        self.assertEqual(poller.clean_times(["6:5", "23:59"]), ["23:59"])
        self.assertEqual(poller.clean_times(None), [])

    def test_next_set_time(self):
        s = {"check_mode": "times", "check_times": ["07:00", "18:00"], "check_timezone": "America/Denver"}
        self.assertEqual(poller.next_check(s, at(2026, 10, 4, 6, 0)), at(2026, 10, 4, 7, 0))
        self.assertEqual(poller.next_check(s, at(2026, 10, 4, 12, 0)), at(2026, 10, 4, 18, 0))
        self.assertEqual(poller.next_check(s, at(2026, 10, 4, 19, 0)), at(2026, 10, 5, 7, 0))  # tomorrow
        self.assertEqual(poller.next_check(s, at(2026, 11, 1, 1, 0)), at(2026, 11, 1, 7, 0))  # DST ends that night

    def test_interval_and_fallbacks(self):
        self.assertEqual(poller.next_check({"poll_minutes": 30}, 1000), 1000 + 1800)
        self.assertEqual(poller.next_check({"poll_minutes": 1}, 1000), 1000 + 300)  # at least 5 minutes
        self.assertEqual(poller.next_check({"check_mode": "times", "check_times": [], "poll_minutes": 15}, 1000), 1900)
        s = {"check_mode": "times", "check_times": ["07:00"], "check_timezone": "Mars/Base"}
        self.assertEqual(poller.next_check(s, at(2026, 10, 4, 6, 0)), at(2026, 10, 4, 7, 0))  # unknown zone

    def test_admin_saves_times_cleanly(self):
        tmp = tempfile.TemporaryDirectory()
        db._conn, db.DB_PATH = None, Path(tmp.name) / "t.db"
        try:
            import server
            out = server.put_settings({"check_mode": "times", "check_times": "18:00, 7:00, nope"}, {})
            self.assertEqual((out["check_mode"], out["check_times"]), ("times", ["07:00", "18:00"]))
            out = server.put_settings({"check_mode": "sometimes"}, {})
            self.assertEqual(out["check_mode"], "times")
        finally:
            db._conn.close()
            db._conn = None
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
