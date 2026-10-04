"""Fixes from the 2026-10-04 security audit: member-set addresses, the old API key, sign-in lockout,
invite races, error text and the content security policy."""

import socket
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ai  # noqa: E402
import auth  # noqa: E402
import db  # noqa: E402
import homeassistant  # noqa: E402
import netguard  # noqa: E402


def resolves_to(ip):
    return lambda host, port, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]


class NetguardTest(unittest.TestCase):
    def test_schemes(self):
        for url in ("file:///etc/passwd", "gopher://x", "ftp://x", "", "http://"):
            with self.assertRaises(netguard.BlockedURL):
                netguard.check(url, local_ok=True)

    def test_private_addresses_blocked_unless_local_ok(self):
        for ip in ("127.0.0.1", "192.168.86.82", "10.0.0.5", "169.254.169.254", "::1", "100.64.0.1"):
            with mock.patch.object(socket, "getaddrinfo", resolves_to(ip)):
                with self.assertRaises(netguard.BlockedURL, msg=ip):
                    netguard.check("http://sneaky.example.com:5000/x", local_ok=False)
                self.assertTrue(netguard.check("http://sneaky.example.com:5000/x", local_ok=True))
        with mock.patch.object(socket, "getaddrinfo", resolves_to("93.184.216.34")):
            self.assertTrue(netguard.check("https://ha.example.com", local_ok=False))

    def test_discord_only(self):
        netguard.check_discord("https://discord.com/api/webhooks/1/abc")
        for url in ("http://discord.com/api/webhooks/1/abc", "https://evil.com/api/webhooks/1",
                    "https://discord.com.evil.com/api/webhooks/1", "https://discord.com/other",
                    "http://192.168.86.82:5000/api/restart"):
            with self.assertRaises(netguard.BlockedURL, msg=url):
                netguard.check_discord(url)

    def test_member_requests_dont_follow_redirects(self):
        class Redirect:
            def open(self, req, timeout):
                raise urllib.error.HTTPError(req.full_url, 302, "redirects aren't followed", {}, None)
        with mock.patch.object(socket, "getaddrinfo", resolves_to("93.184.216.34")), \
                mock.patch.object(netguard, "_no_redirect", Redirect()):
            with self.assertRaises(urllib.error.HTTPError):
                netguard.urlopen(urllib.request.Request("http://public.example.com"), 5, local_ok=False)


class DbTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db._conn = None
        db.DB_PATH = Path(self.tmp.name) / "t.db"
        auth._device_key.clear()
        auth._fails.clear()
        import server
        self.s = server
        self.admin = db.admin_id()
        db.execute("UPDATE users SET name = 'Sam', email = 'sam@example.com', password_hash = ? WHERE id = ?",
                   (auth.hash_password("admin pass 1"), self.admin))
        self.sis = db.execute("INSERT INTO users (email, name, role, created_at) VALUES ('sis@x.y', 'Sis', 'member', ?)",
                              (time.time(),))

    def tearDown(self):
        auth._device_key.clear()
        if db._conn:
            db._conn.close()
        db._conn = None
        self.tmp.cleanup()


class MemberAddressTest(DbTest):
    def test_members_cant_save_private_addresses(self):
        with db.as_user(self.sis), mock.patch.object(socket, "getaddrinfo", resolves_to("192.168.86.82")):
            for key in ("ha_url", "ollama_url"):
                with self.assertRaises(self.s.HTTPError) as cm:
                    self.s.put_settings({key: "http://frigate.lan:5000/api/restart?"}, {})
                self.assertEqual(cm.exception.status, 400)
            with self.assertRaises(self.s.HTTPError):
                self.s.put_settings({"discord_webhook": "http://192.168.86.82:5000/api/restart"}, {})
            # Saving the form with the unchanged default Ollama address still works.
            self.s.put_settings({"ollama_url": db.DEFAULT_SETTINGS["ollama_url"], "zip_code": "84057"}, {})
        with mock.patch.object(socket, "getaddrinfo", resolves_to("192.168.86.131")):
            self.s.put_settings({"ha_url": "http://192.168.86.131"}, {})  # the admin's home network is fine

    def test_members_own_ollama_cant_reach_the_home_network(self):
        with db.as_user(self.sis):
            db.update_settings({"ai_source": "own", "ai_provider": "ollama", "ollama_model": "qwen3.5:4b",
                                "ollama_url": "http://127.0.0.1:5000/api/restart?"})
            with mock.patch.object(urllib.request, "urlopen") as raw:
                with self.assertRaises(ai.AIError) as cm:
                    ai.run(ai.settings_for(), "judge", *ai.judge_prompt({"title": "x", "source": "ebay"}, {"name": "x", "query": "x"}, None))
            raw.assert_not_called()
            self.assertIn("?", str(cm.exception))
            db.update_settings({"ollama_url": "http://127.0.0.1:5000"})
            with self.assertRaises(ai.AIError) as cm:
                ai.run(ai.settings_for(), "judge", *ai.judge_prompt({"title": "x", "source": "ebay"}, {"name": "x", "query": "x"}, None))
            self.assertIn("public internet address", str(cm.exception))

    def test_member_errors_dont_echo_upstream_text(self):
        def refused(req, timeout):
            raise urllib.error.URLError(ConnectionRefusedError("[Errno 111] Connection refused"))
        with mock.patch.object(socket, "getaddrinfo", resolves_to("93.184.216.34")), \
                mock.patch.object(netguard._no_redirect, "open", refused):
            with self.assertRaises(homeassistant.HAError) as cm:
                homeassistant.fetch_devices("http://ha.example.com:8123", "t", local_ok=False)
        self.assertEqual(str(cm.exception), "Can't reach Home Assistant at that address")


class LockoutTest(DbTest):
    def test_known_device_skips_the_per_email_limit(self):
        for _ in range(auth.FAIL_LIMIT):  # a stranger fails on purpose from many addresses
            with self.assertRaises(auth.AuthError):
                auth.sign_in("sam@example.com", "wrong", f"203.0.113.{_}")
        with self.assertRaises(auth.AuthError) as cm:  # a new device is locked out for now...
            auth.sign_in("sam@example.com", "admin pass 1", "198.51.100.7")
        self.assertIn("Too many", str(cm.exception))
        mark = auth.device_mark(self.admin)  # ...but Sam's own browser isn't
        self.assertEqual(auth.sign_in("sam@example.com", "admin pass 1", "198.51.100.7", mark)["id"], self.admin)
        self.assertFalse(auth.is_device_mark(mark, self.sis))
        self.assertFalse(auth.is_device_mark(f"{self.admin}.forged", self.admin))

    def test_failure_table_is_capped(self):
        with mock.patch.object(auth, "FAIL_KEYS_MAX", 50):
            for i in range(200):
                auth.note_failure(f"email:random{i}@x.y")
            self.assertLessEqual(len(auth._fails), 51)

    def test_web_sign_in_sets_the_device_mark(self):
        out = self.s.auth_login({"email": "sam@example.com", "password": "admin pass 1"}, {})
        self.assertEqual(out["__device_mark__"], auth.device_mark(self.admin))


class InviteRaceTest(DbTest):
    def test_a_link_can_only_be_claimed_once(self):
        code = self.s.admin_invite({}, {})["link"].split("=", 1)[1]
        stale = self.s._open_invite(code)  # both requests passed the check before either used the link
        self.s.auth_accept({"code": code, "email": "a@x.y", "password": "friend pass 1"}, {})
        with mock.patch.object(self.s, "_open_invite", lambda c: stale):
            with self.assertRaises(self.s.HTTPError) as cm:
                self.s.auth_accept({"code": code, "email": "b@x.y", "password": "friend pass 1"}, {})
        self.assertEqual(cm.exception.status, 410)
        self.assertEqual(db.query("SELECT COUNT(*) AS n FROM users WHERE email IN ('a@x.y', 'b@x.y')")[0]["n"], 1)


class HeadersTest(unittest.TestCase):
    def test_csp_allows_only_our_scripts(self):
        import server
        csp = dict(server.SECURITY_HEADERS)["Content-Security-Policy"]
        self.assertIn("script-src 'self'", csp)
        self.assertNotIn("unsafe-inline';", csp.split("script-src")[1].split(";")[0])
        self.assertNotIn("<script>", (server.STATIC_DIR / "login.html").read_text())


if __name__ == "__main__":
    unittest.main()
