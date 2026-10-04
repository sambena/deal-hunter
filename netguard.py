"""Addresses that come from people's settings (Ollama, Home Assistant, Discord) are fetched by the server,
so a member could point them at the home network. The admin's own settings may be local (the home Ollama
and Home Assistant are); everyone else's must be public internet addresses, and their requests don't
follow redirects (a public address could otherwise bounce the request inside)."""

from __future__ import annotations

import ipaddress
import socket
import urllib.error
import urllib.parse
import urllib.request


class BlockedURL(ValueError):
    pass


DISCORD_HOSTS = {"discord.com", "discordapp.com", "ptb.discord.com", "canary.discord.com"}


def check(url: str, local_ok: bool, what: str = "address") -> str:
    """The URL, if it may be fetched. Hostnames are resolved and every address they point to is checked."""
    parts = urllib.parse.urlsplit((url or "").strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise BlockedURL(f"The {what} must start with http:// or https://")
    if local_ok:
        return url
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))
    except (socket.gaierror, UnicodeError) as e:
        raise BlockedURL(f"Can't find the {what}'s host") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not ip.is_global or ip.is_multicast:
            raise BlockedURL(f"The {what} must be a public internet address, not one on a private network")
    return url


def check_discord(url: str) -> str:
    """Discord webhooks only: https on Discord's own hosts, for everyone (the admin too)."""
    parts = urllib.parse.urlsplit((url or "").strip())
    if parts.scheme != "https" or (parts.hostname or "").lower() not in DISCORD_HOSTS \
            or not parts.path.startswith("/api/webhooks/"):
        raise BlockedURL("That isn't a Discord webhook address (https://discord.com/api/webhooks/...)")
    return url


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "redirects aren't followed", headers, fp)


_no_redirect = urllib.request.build_opener(_NoRedirect)


def urlopen(req: urllib.request.Request, timeout: float, local_ok: bool, what: str = "address"):
    """urllib.request.urlopen for a URL from someone's settings: checked first, no redirects unless local_ok."""
    check(req.full_url, local_ok, what)
    if local_ok:
        return urllib.request.urlopen(req, timeout=timeout)
    return _no_redirect.open(req, timeout=timeout)
