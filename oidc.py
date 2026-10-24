"""Cougar Cave central login for the web page: OIDC authorization code with PKCE.

Off unless config.json has "oidc": {"enabled": true, ...}. The validation itself is cougar-auth's vendored
library (cougar_login/); this file adapts it to Deal Hunter's plain HTTP server, following Agent Relay's
reference (claude-relay relay/oidc_login.py). Its packages (requirements.txt) are imported only when it is on.

Routes, on the web port only: GET /auth/central starts a sign-in, GET /auth/callback (exactly the registered
path) finishes it, GET /auth/logout is where the login service sends people after signing out.
"""

from __future__ import annotations

import html
import os
import secrets
import stat
import urllib.parse
from pathlib import Path
from urllib.parse import urlsplit

import auth
import central

TX_SECONDS = 300
CALLBACK_PATH = "/auth/callback"
ROUTES = ("/auth/central", CALLBACK_PATH, "/auth/logout")
REQUIRED = ("issuer", "client_id", "callback", "authorize_url", "token_url", "jwks_url", "client_secret_file")

DENIED = ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" '
          'content="width=device-width,initial-scale=1"><meta name="robots" content="noindex"><title>Sign in · Deal Hunter</title>'
          '<link rel="stylesheet" href="/style.css"></head><body><div id="auth-screen"><div class="panel">'
          '<h2>Deal Hunter</h2><p role="alert">MESSAGE</p><div class="row"><a href="/">Back to sign in</a></div>'
          '</div></div></body></html>')
# The callback answers 200 and navigates from this origin (Relay's design choice), so the session cookie set
# here is sent on the next request. A meta refresh, because the page's security policy forbids inline script.
GO = ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta http-equiv="refresh" content="0;url=/">'
      '<title>Signing in</title></head><body><p>Signing in… <a href="/">Continue</a></p></body></html>')

# What a response from here is: (status, extra headers, HTML body). The server adds the security headers.
Response = tuple[int, list[tuple[str, str]], bytes]


def page(message: str, status: int = 403) -> Response:
    return status, [], DENIED.replace("MESSAGE", html.escape(message)).encode()


class CentralLogin:
    def __init__(self, provider, authorize_url: str, transactions, cache, exchange):
        authorize, issuer = urlsplit(authorize_url), urlsplit(provider.issuer)
        if (authorize.scheme, authorize.netloc) != (issuer.scheme, issuer.netloc) or authorize.query or authorize.fragment:
            raise ValueError("The authorization endpoint must be on the trusted issuer's origin")
        if urlsplit(provider.callback).path != CALLBACK_PATH:
            raise ValueError(f"The callback must be the registered {CALLBACK_PATH}")
        self.provider, self.authorize_url = provider, authorize_url
        self.transactions, self.cache, self.exchange = transactions, cache, exchange

    @classmethod
    def from_config(cls, config: dict | None, data_dir: Path, app_dir: Path | None = None) -> "CentralLogin | None":
        """None when central login is off. Missing settings stop the server now, not at the first sign-in.
        Relative paths in the config are relative to the app's folder (app_dir), not wherever it was started."""
        if not config or config.get("enabled") is not True:
            return None
        missing = [k for k in REQUIRED if not config.get(k)]
        if missing:
            raise SystemExit('config.json "oidc" is enabled but needs: ' + ", ".join(missing))
        app_dir = app_dir or Path(__file__).resolve().parent
        secret_file = app_dir / Path(config["client_secret_file"])  # an absolute path stays as it is
        try:
            if os.name == "posix" and stat.S_IMODE(secret_file.stat().st_mode) & 0o077:
                raise SystemExit('config.json "oidc": client_secret_file must be readable by its owner only (chmod 600)')
            secret = secret_file.read_text(encoding="utf-8").strip()
        except OSError:
            secret = ""
        if not secret:
            raise SystemExit('config.json "oidc": client_secret_file is missing or empty')  # never print the secret
        try:
            from cougar_login.jwks_cache import JWKSCache
            from cougar_login.login_validation import OAuthBackchannel, Provider, Transactions
        except ImportError as e:
            raise SystemExit(f"Central login needs its packages: pip install -r requirements.txt ({e.name})") from None
        tx_db = app_dir / Path(config["tx_db"]) if config.get("tx_db") else data_dir / "oidc" / "login-tx.db"
        try:
            tx_db.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            transactions = Transactions(tx_db)  # refuses a directory or file anyone else can read
        except ValueError as e:
            raise SystemExit(f'config.json "oidc": the login transaction store needs a private folder owned by '
                             f'this user ({tx_db.parent}, chmod 700): {e}') from None
        try:
            provider = Provider(config["issuer"], config["client_id"], config["callback"])
            return cls(provider, config["authorize_url"], transactions, JWKSCache(provider, config["jwks_url"]),
                       OAuthBackchannel(provider, config["token_url"], secret))
        except ValueError as e:  # an http:// issuer or callback, or an endpoint off the issuer's origin
            raise SystemExit(f'config.json "oidc": {e}') from None

    @staticmethod
    def _cookie_name(secure: bool) -> str:
        # A __Host- cookie must be Secure; plain HTTP is only for local development.
        return "__Host-dh_login" if secure else "dh_login"

    @staticmethod
    def _tx_cookie(name: str, value: str, max_age: int, secure: bool) -> tuple[str, str]:
        flag = "; Secure" if secure else ""
        return "Set-Cookie", f"{name}={value}; Path=/; Max-Age={max_age}; HttpOnly; SameSite=Lax{flag}"

    def begin(self, address: str, secure: bool) -> Response:
        try:
            central.throttle(address)
            if not central.switch_on():
                raise central.CentralError("Cougar Cave sign-in is turned off for Deal Hunter.")
        except central.CentralError as e:
            return page(str(e), e.status)
        browser = secrets.token_urlsafe(32)
        params = self.transactions.begin(browser, self.provider)
        params.update(response_type="code", client_id=self.provider.client_id,
                      redirect_uri=self.provider.callback, scope="openid")
        # No prompt=login and no max_age: this provider answers "Not Found" to the first and ignores max_age=0.
        location = self.authorize_url + "?" + urllib.parse.urlencode(params)
        return 302, [("Location", location), self._tx_cookie(self._cookie_name(secure), browser, TX_SECONDS, secure)], b""

    def callback(self, query: str, cookie_header: str, secure: bool) -> tuple[Response, tuple[int, str] | None]:
        """Finish a sign-in. Returns the response and, on success, (account id, session token) for the caller to set."""
        from cougar_login.jwks_cache import complete_cached_callback
        from cougar_login.login_validation import LoginRejected
        name = self._cookie_name(secure)
        browser = auth.cookie_value(cookie_header, name) or ""
        clear = self._tx_cookie(name, "", 0, secure)
        session = None
        try:
            claims = complete_cached_callback(query, browser, self.provider, self.transactions, self.cache, self.exchange)
            user, token = central.sign_in(claims["iss"], claims["sub"], claims["auth_time"], claims.get("amr"))
        except LoginRejected:
            status, headers, body = page("Sign-in could not be completed. Start again.", 400)
        except central.CentralError as e:
            status, headers, body = page(str(e), e.status)
        except Exception as e:  # noqa: BLE001 - anything unexpected still gets an answer and a cleared cookie
            print(f"central sign-in failed: {type(e).__name__}")  # the type only: messages can carry claims
            status, headers, body = page("Sign-in could not be completed. Start again.", 500)
        else:
            status, headers, body = 200, [], GO.encode()
            session = (user["id"], token)
        return (status, headers + [clear], body), session

    @staticmethod
    def logout() -> Response:
        """Where the login service sends people after signing out: back to the sign-in page, no session needed."""
        return 302, [("Location", "/")], b""


def redact(path: str) -> str:
    """The callback URL carries a one-time code and the state: keep them out of every log line."""
    # By the parsed path, so a request line with a full URL ("GET http://host/auth/callback?...") is caught too.
    if urlsplit(path).path.startswith("/auth/") and "?" in path:
        return path.split("?", 1)[0] + "?[redacted]"
    return path


LOGIN: dict = {"central": None}  # the configured CentralLogin, or None when it is off


def enabled() -> bool:
    return LOGIN["central"] is not None
