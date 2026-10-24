# Vendored from sambena/cougar-auth scripts/ at commit ad9d8f0c33a432761410835e618d560192f6815f (unmodified except the import path).
"""Reusable backend login validation, without application roles or sessions.

Keys and provider configuration come from the operator's trusted registry.
Never fetch a URL from a JWT header. Consumers must separately enforce current
app admission, local permissions, session lifetime and revocation (#301).
"""
import base64
from dataclasses import dataclass
from contextlib import contextmanager
import hashlib
import hmac
import math
import os
import secrets
import sqlite3
import stat
import time
from urllib.parse import urlsplit, parse_qs

import jwt


class LoginRejected(Exception):
    """Safe reason only; never include a token, code, cookie or provider response."""


@dataclass(frozen=True)
class Provider:
    issuer: str
    client_id: str
    callback: str
    clock_tolerance: int = 30
    allow_loopback: bool = False

    def __post_init__(self):
        for value in (self.issuer, self.callback):
            p = urlsplit(value)
            if (not p.netloc or p.username or p.password or p.query or p.fragment or '*' in value
                    or (p.scheme != 'https' and not (
                        self.allow_loopback and p.scheme == 'http' and p.hostname == '127.0.0.1'))):
                raise ValueError('Trusted exact HTTPS provider configuration required')
        if not self.client_id or type(self.clock_tolerance) is not int or not 0 <= self.clock_tolerance <= 60:
            raise ValueError('Invalid client or clock tolerance')


class OAuthBackchannel:
    """Authlib code exchange using only the trusted registration's endpoint.

    No redirect following, insecure TLS switches, password grant or implicit
    fallback. Secrets belong in a private server-side secret store.
    """
    def __init__(self, provider, token_endpoint, client_secret=None):
        endpoint, issuer = urlsplit(token_endpoint), urlsplit(provider.issuer)
        if ((endpoint.scheme, endpoint.netloc) != (issuer.scheme, issuer.netloc)
                or endpoint.query or endpoint.fragment or endpoint.username or endpoint.password):
            raise ValueError('Token endpoint must be pinned on the trusted issuer origin')
        self.provider, self.token_endpoint, self.client_secret = provider, token_endpoint, client_secret

    def __call__(self, code, verifier):
        from authlib.integrations.requests_client import OAuth2Session
        try:
            with OAuth2Session(self.provider.client_id, self.client_secret,
                    token_endpoint_auth_method='client_secret_basic' if self.client_secret else 'none',
                    redirect_uri=self.provider.callback, code_challenge_method='S256') as client:
                client.trust_env = False
                def require_success(response):
                    if response.status_code != 200:
                        raise LoginRejected('backchannel')
                    return response
                client.register_compliance_hook('access_token_response', require_success)
                return client.fetch_token(self.token_endpoint, grant_type='authorization_code',
                    code=code, code_verifier=verifier, timeout=15, allow_redirects=False)
        except Exception:
            raise LoginRejected('backchannel') from None


def validate_id_token(token, jwks, provider, nonce, access_token=None):
    """Validate a login ID token against pinned settings and supplied trusted keys.

    Returns verified claims only. Callers must not treat them as app grants.
    """
    try:
        if not isinstance(nonce, str) or not nonce:
            raise LoginRejected('nonce')
        header = jwt.get_unverified_header(token)
        if (header.get('alg') != 'RS256' or not isinstance(header.get('kid'), str) or not header['kid']
                or header.get('typ', 'JWT') != 'JWT'
                or any(k in header for k in ('jku', 'x5u', 'jwk', 'crit'))):
            raise LoginRejected('header')
        if (not isinstance(jwks, dict) or not isinstance(jwks.get('keys'), list)
                or not 1 <= len(jwks['keys']) <= 64 or any(not isinstance(k, dict) for k in jwks['keys'])):
            raise LoginRejected('key')
        candidates = [k for k in jwks['keys'] if k.get('kid') == header['kid']]
        if len(candidates) != 1:
            raise LoginRejected('key')
        candidate = candidates[0]
        if (candidate.get('kty') != 'RSA' or candidate.get('use', 'sig') != 'sig'
                or candidate.get('alg', 'RS256') != 'RS256'
                or ('key_ops' in candidate and (not isinstance(candidate['key_ops'], list)
                                               or 'verify' not in candidate['key_ops']))):
            raise LoginRejected('key')
        key = jwt.PyJWK.from_dict(candidate, algorithm='RS256').key
        claims = jwt.decode(token, key, algorithms=['RS256'], issuer=provider.issuer,
            audience=provider.client_id, leeway=provider.clock_tolerance,
            options={'require': ['iss', 'sub', 'aud', 'exp', 'iat', 'nonce', 'auth_time']})
        if claims['iss'] != provider.issuer:
            raise LoginRejected('issuer')
        if not isinstance(claims['sub'], str) or not 1 <= len(claims['sub']) <= 255:
            raise LoginRejected('subject')
        for field in ('exp', 'iat', 'auth_time'):
            if type(claims[field]) not in (int, float) or not math.isfinite(claims[field]):
                raise LoginRejected('time')
        if claims['exp'] <= claims['iat'] or claims['auth_time'] > claims['iat'] + provider.clock_tolerance:
            raise LoginRejected('time')
        aud = claims['aud']
        if not isinstance(aud, (str, list)) or (isinstance(aud, list) and (
                not aud or any(not isinstance(a, str) or not a for a in aud))):
            raise LoginRejected('audience')
        if (isinstance(aud, list) and len(aud) > 1 and 'azp' not in claims):
            raise LoginRejected('authorized-party')
        if 'azp' in claims and claims['azp'] != provider.client_id:
            raise LoginRejected('authorized-party')
        if not isinstance(claims['nonce'], str) or not hmac.compare_digest(claims['nonce'], nonce):
            raise LoginRejected('nonce')
        if 'at_hash' in claims:
            if not isinstance(access_token, str) or not access_token:
                raise LoginRejected('access-token-binding')
            digest = hashlib.sha256(access_token.encode('ascii')).digest()[:16]
            expected = base64.urlsafe_b64encode(digest).decode().rstrip('=')
            if not isinstance(claims['at_hash'], str) or not hmac.compare_digest(claims['at_hash'], expected):
                raise LoginRejected('access-token-binding')
        return claims
    except LoginRejected:
        raise
    except jwt.ExpiredSignatureError:
        raise LoginRejected('expired-token') from None
    except jwt.InvalidIssuerError:
        raise LoginRejected('issuer') from None
    except jwt.InvalidAudienceError:
        raise LoginRejected('audience') from None
    except jwt.InvalidSignatureError:
        raise LoginRejected('signature') from None
    except (jwt.PyJWTError, ValueError, TypeError, KeyError, UnicodeError):
        raise LoginRejected('invalid-token') from None


class Transactions:
    """SQLite server-side state, browser binding and atomic one-use completion.

    The database contains PKCE verifiers: put it in the app's private data
    directory with owner-only permissions. `browser` is a separate random
    HttpOnly host-only transaction cookie, never the public OAuth state.
    This component does not issue an authenticated application session.
    """
    def __init__(self, database, clock=time.time):
        self.database, self.clock = str(database), clock
        # The verifier lives only in this private server-side store. Never create
        # a readable-by-default database and rely on the deployment to fix it.
        if os.name == 'posix':
            parent = os.stat(os.path.dirname(os.path.abspath(self.database)))
            if parent.st_uid != os.getuid() or stat.S_IMODE(parent.st_mode) & 0o077:
                raise ValueError('Private transaction directory required')
        try:
            fd = os.open(self.database, os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        except FileExistsError:
            info = os.lstat(self.database)
            if not stat.S_ISREG(info.st_mode) or (os.name == 'posix' and (
                    info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077)):
                raise ValueError('Private regular transaction database required')
        else:
            os.close(fd)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS login_transaction ('
                'state_hash TEXT PRIMARY KEY, browser_hash TEXT NOT NULL, '
                'issuer TEXT NOT NULL, client TEXT NOT NULL, callback TEXT NOT NULL, '
                'nonce TEXT NOT NULL, verifier TEXT NOT NULL, created REAL NOT NULL)')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.database, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def digest(value):
        if not isinstance(value, str) or not value or len(value) > 512:
            raise LoginRejected('transaction')
        return hashlib.sha256(value.encode()).hexdigest()

    def begin(self, browser, provider):
        browser_hash = self.digest(browser)
        state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
        with self.connect() as db:
            now = self.clock()
            db.execute('DELETE FROM login_transaction WHERE created <= ?', (now - 300,))
            db.execute('INSERT INTO login_transaction VALUES (?,?,?,?,?,?,?,?)',
                (self.digest(state), browser_hash, provider.issuer, provider.client_id,
                 provider.callback, nonce, verifier, now))
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        return {'state': state, 'nonce': nonce, 'code_challenge': challenge, 'code_challenge_method': 'S256'}

    def consume(self, state, browser, provider):
        """Consume before backchannel exchange; failures require a new login.

        Browser mismatch does not consume another browser's transaction.
        Expired, invalid-context and replayed requests return no verifier.
        """
        state_hash, browser_hash = self.digest(state), self.digest(browser)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM login_transaction WHERE state_hash=? AND browser_hash=?',
                             (state_hash, browser_hash)).fetchone()
            if row is None:
                raise LoginRejected('transaction')
            # Commit the deletion even when the time/context validation fails.
            db.execute('DELETE FROM login_transaction WHERE state_hash=?', (state_hash,))
        age = self.clock() - row[7]
        if not 0 <= age < 300:
            raise LoginRejected('expired-transaction')
        if row[2:5] != (provider.issuer, provider.client_id, provider.callback):
            raise LoginRejected('transaction-context')
        return {'nonce': row[5], 'code_verifier': row[6]}


def complete_callback(query_string, browser, provider, transactions, jwks, exchange):
    """Consume a browser-bound callback, exchange server-side, validate ID token.

    `exchange(code, verifier)` is the app's configured OAuth library client;
    it must use this registration's fixed endpoint/client/callback. This function
    grants no app session or role. Only its successful return is an identity.
    """
    try:
        query = parse_qs(query_string, keep_blank_values=True, strict_parsing=True,
                         max_num_fields=12)
        if (len(query.get('state', [])) != 1 or len(query.get('code', [])) != 1
                or not query['code'][0] or len(query['code'][0]) > 4096 or 'error' in query):
            raise LoginRejected('callback')
        if 'iss' in query and query['iss'] != [provider.issuer]:
            raise LoginRejected('issuer')
        proof = transactions.consume(query['state'][0], browser, provider)
        try:
            tokens = exchange(query['code'][0], proof['code_verifier'])
        except Exception:
            raise LoginRejected('backchannel') from None
        if (not isinstance(tokens, dict) or tokens.get('token_type', '').lower() != 'bearer'
                or not isinstance(tokens.get('access_token'), str) or not tokens['access_token']):
            raise LoginRejected('token-response')
        return validate_id_token(tokens.get('id_token'), jwks, provider, proof['nonce'], tokens['access_token'])
    except (ValueError, TypeError, AttributeError):
        raise LoginRejected('callback') from None
