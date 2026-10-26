"""#541: pairing a phone through central login, to the Cougar Cave pairing API contract v1 (cougar-cave-ui
docs/work/502-*.md with #526), over real HTTP against the fake provider. Each refusal has its contract code, next to a
valid control in the same test. A 5xx or 429 never counts as a correct refusal (except the rate-limit test itself)."""

import http.client
import json
import sys
import time
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import central  # noqa: E402
import db  # noqa: E402
import pairing  # noqa: E402
from tests import test_central_login as fixture  # noqa: E402
from tests.test_backchannel import logout_token  # noqa: E402

CSRF = {"X-Cave-Request": "1"}


class Phone:
    """An app on a phone: no cookies, a Bearer credential once paired."""
    def __init__(self, port, credential=None):
        self.port, self.credential = port, credential

    def call(self, method, path, body=None, headers=None, bearer=True):
        h = {**CSRF, **(headers or {})}
        if self.credential and bearer:
            h["Authorization"] = f"Bearer {self.credential}"
        if body is not None:
            h["Content-Type"] = "application/json"
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request(method, path, None if body is None else json.dumps(body), h)
        r = conn.getresponse()
        data = r.read()
        conn.close()
        return r.status, r.headers, (json.loads(data) if data else None)


class PairingTest(unittest.TestCase):
    setUpClass = classmethod(fixture.CentralLoginTest.setUpClass.__func__)
    browser = fixture.CentralLoginTest.browser
    begin = fixture.CentralLoginTest.begin
    sign_in = fixture.CentralLoginTest.sign_in
    state_of = fixture.CentralLoginTest.state_of

    @classmethod
    def tearDownClass(cls):
        fixture.CentralLoginTest.tearDownClass.__func__(cls)
        cls.ui.server_close()
        cls.api.server_close()

    def setUp(self):
        fixture.CentralLoginTest.setUp(self)
        for t in ("central_logout_attempts", "central_logout_jti", "tokens", "pair_codes", "paired_devices"):
            db.execute(f"DELETE FROM {t}")
        pairing._rotated.clear()

    def tearDown(self):
        fixture.CentralLoginTest.tearDown(self)

    # ---- helpers ----

    def owner(self, sub="owner-uuid", **args):
        status, _, b, _ = self.sign_in(sub=sub, **args)
        self.assertEqual(status, 200)
        return b

    def code(self, b):
        status, _, body, _ = b.get("/auth/devices/code", "POST", {}, CSRF)
        self.assertEqual(status, 201, body)
        return json.loads(body)

    def pair(self, pin, name="Pixel 10", port=None):
        return Phone(port or self.ui.server_port).call("POST", "/auth/devices/pair", {"pin": pin, "name": name})

    def paired(self, b=None):
        status, _, reply = self.pair(self.code(b or self.owner())["pin"])
        self.assertEqual(status, 200, reply)
        return Phone(self.ui.server_port, reply["credential"]), reply

    def refused(self, reply, status, code):
        self.assertEqual((reply[0], reply[2] and reply[2].get("error")), (status, code))

    # ---- the valid control ----

    def test_pair_reads_the_app_as_that_person_on_both_ports(self):
        b = self.owner()
        made = self.code(b)
        self.assertEqual(set(made), {"pin", "expires_in", "qr"})
        self.assertRegex(made["pin"], r"^\d{6}$")
        self.assertEqual(made["expires_in"], 120)
        q = urllib.parse.parse_qs(made["qr"].split("?", 1)[1])
        self.assertTrue(made["qr"].startswith("cougarcave-pair:v1?"))
        cb = urllib.parse.urlsplit(fixture.CALLBACK)
        self.assertEqual((q["server"], q["pin"]), ([f"{cb.scheme}://{cb.netloc}"], [made["pin"]]))  # configured, not Host
        status, _, reply = self.pair(made["pin"])
        self.assertEqual(status, 200)
        self.assertEqual(set(reply), {"credential", "device_id", "expires_at", "rotate_after", "person"})
        self.assertEqual(reply["person"], {"name": "Sam", "role": "owner"})
        for port in (self.ui.server_port, self.api.server_port):
            status, _, state = Phone(port, reply["credential"]).call("GET", "/api/state")
            self.assertEqual((status, state["me"]["name"]), (200, "Sam"))
        listed = Phone(self.ui.server_port, reply["credential"]).call("GET", "/auth/devices")[2]["devices"]
        self.assertEqual([(d["name"], d["current"]) for d in listed], [("Pixel 10", True)])
        # Nothing secret in the database or the audit log.
        dump = json.dumps(db.query("SELECT * FROM pair_codes") + db.query("SELECT * FROM paired_devices")
                          + central.audit_log(50))
        self.assertNotIn(made["pin"], dump)
        self.assertNotIn(reply["credential"], dump)

    # ---- the PIN ----

    @patch.object(pairing, "PAIR_LIMIT", 100)  # many tries in one test; the limit has its own test
    def test_pin_refusals_are_one_code(self):
        b = self.owner()
        made = self.code(b)
        self.refused(self.pair("000000" if made["pin"] != "000000" else "111111"), 400, "pin_invalid")  # wrong
        self.assertEqual(self.pair(made["pin"])[0], 200)                                             # control
        self.refused(self.pair(made["pin"]), 400, "pin_invalid")                                     # used
        first, second = self.code(b), self.code(b)
        self.refused(self.pair(first["pin"]), 400, "pin_invalid")                                    # cancelled
        self.assertEqual(self.pair(second["pin"])[0], 200)
        late = self.code(b)
        db.execute("UPDATE pair_codes SET expires = ?", (time.time() - 1,))
        self.refused(self.pair(late["pin"]), 400, "pin_invalid")                                     # expired
        self.refused(self.pair("12ab56"), 400, "pin_invalid")
        self.refused(self.pair(123456), 400, "bad_request")
        self.refused(self.pair(self.code(b)["pin"], name="x" * 61), 400, "bad_request")

    def test_app_wide_cap_kills_the_right_pin_after_three_wrong_ones_from_anywhere(self):
        made = self.code(self.owner())
        wrong = "000000" if made["pin"] != "000000" else "111111"
        for _ in range(3):
            self.refused(self.pair(wrong), 400, "pin_invalid")
        self.refused(self.pair(made["pin"]), 400, "pin_invalid")
        self.assertEqual(self.pair(self.code(self.owner())["pin"])[0], 200)  # control: a new PIN works

    def test_rate_limit_per_address(self):
        wrong = "999999"
        for _ in range(5):
            self.assertNotEqual(self.pair(wrong)[0], 429)
        status, headers, reply = self.pair(wrong)
        self.assertEqual((status, reply["error"]), (429, "rate_limited"))

    def test_code_needs_a_fresh_central_sign_in_with_mfa_and_never_a_phone(self):
        stale = self.owner(auth_age=600)
        status, _, body, _ = stale.get("/auth/devices/code", "POST", {}, CSRF)
        self.assertEqual((status, json.loads(body)["error"]), (403, "needs_recent_signin"))
        status, _, body, _ = self.owner().get("/auth/devices/code", "POST", {}, {})          # no X-Cave-Request
        self.assertEqual((status, json.loads(body)["error"]), (403, "csrf"))
        self.refused(Phone(self.ui.server_port).call("POST", "/auth/devices/code", {}), 401, "signed_out")
        phone, _ = self.paired()
        self.refused(phone.call("POST", "/auth/devices/code", {}), 403, "bearer_not_allowed")  # never 401 (#526)
        self.assertEqual(phone.call("GET", "/api/state")[0], 200)                               # still paired

    def test_someone_elses_browser_cannot_use_the_pin(self):
        made = self.code(self.owner())
        member = self.owner("member-uuid")
        status, _, body, _ = member.get("/auth/devices/pair", "POST", {"pin": made["pin"], "name": "x"}, CSRF)
        self.assertEqual((status, json.loads(body)["error"]), (400, "pin_invalid"))

    # ---- the credential ----

    def test_cookie_and_bearer_is_refused(self):
        b = self.owner()
        phone, _ = self.paired(b)
        status, _, body, _ = b.get("/api/state", headers={"Authorization": f"Bearer {phone.credential}"})
        self.assertEqual((status, json.loads(body)["error"]), (400, "cookie_and_bearer"))
        self.assertEqual(self.state_of(b), 200)

    def test_live_access_checks(self):
        phone, _ = self.paired()
        other, _ = self.paired(self.owner("member-uuid"))
        central.set_status(self.owner_ident, "disabled")
        self.refused(phone.call("GET", "/api/state"), 403, "no_access")
        self.assertEqual(other.call("GET", "/api/state")[0], 200)                     # control
        central.set_status(self.owner_ident, "active")
        self.assertEqual(phone.call("GET", "/api/state")[0], 200)                     # live: back
        central.set_switch("off")
        self.refused(phone.call("GET", "/api/state"), 403, "no_access")
        central.set_switch("on")
        self.assertEqual(phone.call("GET", "/api/state")[0], 200)
        db.execute("UPDATE users SET role = 'member' WHERE id = ?", (self.admin,))
        self.refused(phone.call("GET", "/api/state"), 401, "device_revoked")
        db.execute("UPDATE users SET role = 'admin' WHERE id = ?", (self.admin,))

    def test_backchannel_logout_ends_every_phone_of_that_person(self):
        phone, _ = self.paired()
        other, _ = self.paired(self.owner("member-uuid"))
        conn = http.client.HTTPConnection("127.0.0.1", self.ui.server_port, timeout=10)
        conn.request("POST", "/auth/backchannel", urllib.parse.urlencode({"logout_token": logout_token()}),
                     {"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(conn.getresponse().status, 200)
        conn.close()
        self.refused(phone.call("GET", "/api/state"), 401, "device_revoked")
        self.assertEqual(other.call("GET", "/api/state")[0], 200)

    def test_remove_and_unpair(self):
        b = self.owner()
        phone, reply = self.paired(b)
        second, second_reply = self.paired(b)
        other, other_reply = self.paired(self.owner("member-uuid"))
        status, _, body, _ = b.get(f"/auth/devices/{other_reply['device_id']}", "DELETE", None, CSRF)
        self.assertEqual((status, json.loads(body)["error"]), (404, "not_found"))     # not yours
        status, _, _, _ = b.get(f"/auth/devices/{reply['device_id']}", "DELETE", None, CSRF)
        self.assertEqual(status, 204)
        self.refused(phone.call("GET", "/api/state"), 401, "device_revoked")
        self.assertEqual(second.call("GET", "/api/state")[0], 200)                    # control
        self.assertEqual(second.call("DELETE", "/auth/devices/current")[0], 204)
        self.refused(second.call("GET", "/api/state"), 401, "device_revoked")
        self.assertEqual(other.call("GET", "/api/state")[0], 200)

    def test_idle_and_absolute_expiry(self):
        phone, reply = self.paired()
        db.execute("UPDATE paired_devices SET seen = ?", (time.time() - 6 * 86400,))
        self.assertEqual(phone.call("GET", "/api/state")[0], 200)                     # control: 6 days idle
        db.execute("UPDATE paired_devices SET seen = ?", (time.time() - 8 * 86400,))
        self.refused(phone.call("GET", "/api/state"), 401, "device_expired")
        phone, _ = self.paired()
        db.execute("UPDATE paired_devices SET expires = ? WHERE revoked = 0 AND seen > ?", (time.time() - 1, time.time() - 60))
        self.refused(phone.call("GET", "/api/state"), 401, "device_expired")

    # ---- rotation (#526) ----

    def test_rotation_grace_replay_and_reuse(self):
        phone, reply = self.paired()
        self.assertIsNone(phone.call("GET", "/api/state")[1].get("Cave-Device-Rotate"))
        db.execute("UPDATE paired_devices SET rotated = ?", (time.time() - 86401,))
        self.assertEqual(phone.call("GET", "/api/state")[1].get("Cave-Device-Rotate"), "due")
        status, _, new = phone.call("POST", "/auth/devices/rotate", {})
        self.assertEqual((status, set(new)), (200, {"credential", "device_id", "expires_at", "rotate_after"}))
        newer = Phone(self.ui.server_port, new["credential"])
        self.assertEqual(newer.call("GET", "/api/state")[0], 200)
        self.assertEqual(phone.call("GET", "/api/state")[0], 200)                     # old one in its grace
        status, _, again = phone.call("POST", "/auth/devices/rotate", {})             # a lost reply, asked again
        self.assertEqual((status, again["credential"]), (200, new["credential"]))     # the same, never a second
        pairing._rotated.clear()                                                      # as after a restart
        self.refused(phone.call("POST", "/auth/devices/rotate", {}), 401, "device_rotated")
        self.assertEqual(newer.call("GET", "/api/state")[0], 200)
        db.execute("UPDATE paired_devices SET old_until = ?", (time.time() - 1,))
        self.refused(phone.call("GET", "/api/state"), 401, "device_revoked")          # reuse after the grace
        self.refused(newer.call("GET", "/api/state"), 401, "device_revoked")          # the whole phone ends


if __name__ == "__main__":
    unittest.main()
