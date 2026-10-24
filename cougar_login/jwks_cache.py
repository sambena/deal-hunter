# Vendored from sambena/cougar-auth scripts/ at commit ad9d8f0c33a432761410835e618d560192f6815f (unmodified except the import path).
"""Bounded trusted JWKS cache for backend consumers; never follows token URLs."""
import copy
import json
import threading
import time
from urllib.parse import urlsplit

import requests
from cougar_login.login_validation import LoginRejected


class JWKSCache:
    def __init__(self, provider, endpoint, ttl=300, refresh_interval=30, timeout=5,
                 max_bytes=262144, clock=time.monotonic):
        parsed, issuer = urlsplit(endpoint), urlsplit(provider.issuer)
        if ((parsed.scheme, parsed.netloc) != (issuer.scheme, issuer.netloc)
                or not parsed.path or parsed.query or parsed.fragment
                or parsed.username or parsed.password):
            raise ValueError('JWKS endpoint must be pinned to trusted issuer origin')
        if not (1 <= ttl <= 300 and 1 <= refresh_interval <= ttl and 0 < timeout <= 15
                and type(max_bytes) is int and 1024 <= max_bytes <= 1048576):
            raise ValueError('Invalid JWKS cache limits')
        self.endpoint, self.ttl, self.interval = endpoint, ttl, refresh_interval
        self.timeout, self.max_bytes, self.clock = timeout, max_bytes, clock
        self._lock = threading.Lock()
        self._keys, self._expires, self._attempt = None, 0, None

    def _fetch(self):
        with requests.Session() as client:
            client.trust_env = False
            with client.get(self.endpoint, timeout=self.timeout, allow_redirects=False,
                            stream=True, headers={'Accept': 'application/json'}) as response:
                if response.status_code != 200:
                    raise LoginRejected('jwks-unavailable')
                body = bytearray()
                for chunk in response.iter_content(chunk_size=8192):
                    body.extend(chunk)
                    if len(body) > self.max_bytes:
                        raise LoginRejected('jwks-unavailable')
        value = json.loads(body)
        keys = value.get('keys') if isinstance(value, dict) else None
        if not isinstance(keys, list) or not 1 <= len(keys) <= 64:
            raise LoginRejected('jwks-unavailable')
        ids = []
        for key in keys:
            if not isinstance(key, dict) or not isinstance(key.get('kid'), str) or not 1 <= len(key['kid']) <= 256:
                raise LoginRejected('jwks-unavailable')
            ids.append(key['kid'])
        if len(set(ids)) != len(ids):
            raise LoginRejected('jwks-unavailable')
        return {'keys': keys}

    def get(self, kid):
        # kid selects within this fixed endpoint; it never selects a URL.
        if not isinstance(kid, str) or not 1 <= len(kid) <= 256:
            raise LoginRejected('key')
        with self._lock:
            now = self.clock()
            fresh = self._keys is not None and now < self._expires
            if fresh and any(key['kid'] == kid for key in self._keys['keys']):
                return copy.deepcopy(self._keys)
            if self._attempt is not None and now - self._attempt < self.interval:
                raise LoginRejected('jwks-unavailable' if not fresh else 'key')
            self._attempt = now
            try:
                keys = self._fetch()
            except Exception:
                raise LoginRejected('jwks-unavailable') from None
            self._keys, self._expires = keys, now + self.ttl
            if not any(key['kid'] == kid for key in keys['keys']):
                raise LoginRejected('key')
            return copy.deepcopy(keys)


def complete_cached_callback(query_string, browser, provider, transactions, cache, exchange):
    """Use the hardened callback with request-local keys from a shared cache."""
    import jwt
    from cougar_login.login_validation import complete_callback
    keys = {}

    def exchange_with_keys(code, verifier):
        tokens = exchange(code, verifier)
        try:
            kid = jwt.get_unverified_header(tokens['id_token'])['kid']
            keys.update(cache.get(kid))
        except Exception:
            raise LoginRejected('key') from None
        return tokens

    return complete_callback(query_string, browser, provider, transactions, keys, exchange_with_keys)
