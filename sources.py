"""Marketplace sources. Each returns a list of raw listing dicts:

    {source, source_id, title, price, shipping, currency, url, image,
     location, condition, buying, text}

`text` is extra description used only for junk filtering.
"""

from __future__ import annotations

import base64
import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from matching import parse_query, search_terms

USER_AGENT = "deal-hunter/0.1 (personal hardware watcher)"


class SourceError(Exception):
    pass


def _http(url: str, *, data: bytes | None = None, headers: dict | None = None, timeout: int = 20):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        raise SourceError(f"HTTP {e.code} from {urllib.parse.urlparse(url).netloc}: {body}") from e
    except urllib.error.URLError as e:
        raise SourceError(f"Network error: {e.reason}") from e


# ---- eBay (official Browse API) -------------------------------------------

_ebay_token: dict = {"value": None, "expires": 0.0, "client": None}


def _ebay_access_token(settings: dict) -> str:
    cid, secret = settings["ebay_client_id"], settings["ebay_client_secret"]
    if not cid or not secret:
        raise SourceError("eBay keys not set (Settings > eBay)")
    if _ebay_token["value"] and _ebay_token["client"] == cid and time.time() < _ebay_token["expires"] - 60:
        return _ebay_token["value"]
    basic = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    body = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "scope": "https://api.ebay.com/oauth/api_scope",
    }).encode()
    tok = _http("https://api.ebay.com/identity/v1/oauth2/token", data=body, headers={
        "Authorization": f"Basic {basic}",
        "Content-Type": "application/x-www-form-urlencoded",
    })
    _ebay_token.update(value=tok["access_token"], expires=time.time() + tok.get("expires_in", 7200), client=cid)
    return tok["access_token"]


def ebay(watch: dict, settings: dict) -> list[dict]:
    return _ebay_search(watch, settings, local=False)


def ebay_local(watch: dict, settings: dict) -> list[dict]:
    """eBay listings the seller lets you pick up within the local radius."""
    return _ebay_search(watch, settings, local=True)


def _ebay_search(watch: dict, settings: dict, local: bool) -> list[dict]:
    zip_code = str(settings.get("zip_code") or "").strip()
    if local and not zip_code:
        raise SourceError("set your ZIP code in Settings > Local area")
    token = _ebay_access_token(settings)
    groups, _ = parse_query(watch["query"])
    # eBay keyword syntax: (a,b) means a OR b.
    q = " ".join(g[0] if len(g) == 1 else "(" + ",".join(g) + ")" for g in groups)
    filters = []
    lo, hi = watch.get("min_price"), watch.get("max_price")
    if lo is not None or hi is not None:
        filters.append(f"price:[{lo or 0}..{hi if hi is not None else ''}]")
        filters.append("priceCurrency:USD")
    cond = {"used": "USED", "new": "NEW"}.get(watch.get("condition", "any"))
    if cond:
        filters.append(f"conditions:{{{cond}}}")
    if not watch.get("include_auctions"):
        filters.append("buyingOptions:{FIXED_PRICE|BEST_OFFER}")
    if local:
        # eBay requires all five pickup filters together.
        radius = max(1, int(float(settings.get("local_radius_miles") or 50)))
        filters += ["deliveryOptions:{SELLER_ARRANGED_LOCAL_PICKUP}", "pickupCountry:US",
                    f"pickupPostalCode:{zip_code}", f"pickupRadius:{radius}", "pickupRadiusUnit:mi"]
    params = {"q": q, "limit": "50", "sort": "newlyListed"}
    if filters:
        params["filter"] = ",".join(filters)
    headers = {
        "Authorization": f"Bearer {token}",
        "X-EBAY-C-MARKETPLACE-ID": settings.get("ebay_marketplace") or "EBAY_US",
    }
    if zip_code:
        headers["X-EBAY-C-ENDUSERCTX"] = f"contextualLocation=country%3DUS%2Czip%3D{zip_code}"
    data = _http("https://api.ebay.com/buy/browse/v1/item_summary/search?" + urllib.parse.urlencode(params),
                 headers=headers)
    out = []
    for it in data.get("itemSummaries", []):
        price = it.get("price") or it.get("currentBidPrice") or {}
        ship = None
        for opt in it.get("shippingOptions") or []:
            cost = (opt.get("shippingCost") or {}).get("value")
            if cost is not None:
                ship = float(cost)
                break
        loc = it.get("itemLocation") or {}
        out.append({
            "source": "ebay_local" if local else "ebay",
            "source_id": it["itemId"],
            "title": it.get("title", ""),
            "price": float(price["value"]) if price.get("value") else None,
            "shipping": ship,
            "currency": price.get("currency", "USD"),
            "url": it.get("itemWebUrl"),
            "image": (it.get("image") or {}).get("imageUrl"),
            "location": ", ".join(x for x in (loc.get("city"), loc.get("stateOrProvince"), loc.get("country")) if x),
            "condition": it.get("condition", ""),
            "buying": ("local pickup · " if local else "")
                      + "/".join(it.get("buyingOptions", [])).lower().replace("_", " "),
            # eBay keeps "For parts or not working" in the condition, not the title; junk words check this too.
            "text": it.get("condition", ""),
        })
    return out


# ---- Reddit (r/hardwareswap, r/homelabsales) -------------------------------

_reddit_cache: dict = {"at": 0.0, "posts": []}

PRICE_RE = re.compile(r"\$\s?(\d{1,5}(?:,\d{3})*(?:\.\d{2})?)|(\d{1,5}(?:\.\d{2})?)\s?(?:usd|shipped|obo)\b", re.I)


ATOM = {"a": "http://www.w3.org/2005/Atom"}


def _html_to_text(fragment: str) -> str:
    text = re.sub(r"<br\s*/?>|</p>|</li>|</tr>", "\n", fragment, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return html.unescape(text)


def _reddit_feed(sub: str) -> list[dict]:
    # Reddit blocks its JSON API for scripts but still serves RSS; it rate-limits hard, so back off once.
    url = f"https://www.reddit.com/r/{sub}/new/.rss?limit=100"
    for attempt in range(2):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                root = ET.fromstring(resp.read())
            break
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt == 0:
                time.sleep(10)
                continue
            raise SourceError(f"HTTP {e.code} from reddit") from e
        except (urllib.error.URLError, ET.ParseError) as e:
            raise SourceError(f"couldn't read feed: {e}") from e
    posts = []
    for entry in root.findall("a:entry", ATOM):
        link = entry.find("a:link", ATOM)
        thumb = entry.find("{http://search.yahoo.com/mrss/}thumbnail")
        posts.append({
            "id": (entry.findtext("a:id", "", ATOM)).removeprefix("t3_"),
            "title": html.unescape(entry.findtext("a:title", "", ATOM)),
            "url": link.get("href") if link is not None else "",
            "body": _html_to_text(entry.findtext("a:content", "", ATOM)),
            "image": thumb.get("url") if thumb is not None else None,
            "_sub": sub,
        })
    return posts


def _reddit_posts(settings: dict) -> list[dict]:
    # One fetch per poll cycle, shared by every watch.
    if time.time() - _reddit_cache["at"] < 300:
        return _reddit_cache["posts"]
    posts, errors = [], []
    for i, sub in enumerate(settings.get("reddit_subs") or []):
        if i:
            time.sleep(3)
        try:
            posts += _reddit_feed(sub)
        except SourceError as e:
            errors.append(f"r/{sub}: {e}")
    if errors and not posts:
        raise SourceError("; ".join(errors))
    _reddit_cache.update(at=time.time(), posts=posts)
    return posts


def _selling_part(title: str) -> str | None:
    """The part of a swap title describing what's for sale, or None if it's a buy post."""
    t = title.lower()
    m = re.search(r"\[h\](.*?)\[w\](.*)", t)
    if m:
        have, want = m.groups()
        # [W] PayPal / cash means they're selling what's in [H].
        return have if re.search(r"paypal|cash|venmo|zelle|money|\$", want) else None
    if "[fs]" in t or "[for sale]" in t:
        return t
    return None


def _guess_price(body: str, query: str) -> float | None:
    """Find a price on the line of the post body that mentions the searched item."""
    groups, _ = parse_query(query)
    key = [a.lower() for a in groups[0]] if groups else []
    for line in body.splitlines():
        low = line.lower().replace("-", "").replace(" ", "")
        if key and not any(k.replace("-", "").replace(" ", "") in low for k in key):
            continue
        m = PRICE_RE.search(line)
        if m:
            return float((m.group(1) or m.group(2)).replace(",", ""))
    return None


def reddit(watch: dict, settings: dict) -> list[dict]:
    out = []
    for p in _reddit_posts(settings):
        selling = _selling_part(p.get("title", ""))
        if selling is None:
            continue
        loc = re.match(r"\s*\[([A-Z]{2,3}(?:-[A-Z]{2})?)\]", p.get("title", ""))
        out.append({
            "source": "reddit",
            "source_id": p["id"],
            "title": p["title"],
            "match_text": selling,  # only the [H] half must match the query
            "price": _guess_price(p["body"], watch["query"]),
            "shipping": None,
            "currency": "USD",
            "url": p["url"],
            "image": p["image"],
            "location": loc.group(1) if loc else "",
            "condition": "used",
            "buying": f"r/{p['_sub']}",
            "text": "",  # bodies list many items; junk words there usually refer to other items
        })
    return out


# ---- Best Buy (open-box via official API; experimental) --------------------

def bestbuy(watch: dict, settings: dict) -> list[dict]:
    key = settings.get("bestbuy_api_key")
    if not key:
        raise SourceError("Best Buy API key not set (Settings > Best Buy)")
    words = [re.sub(r"[^A-Za-z0-9]", "", w) for w in search_terms(watch["query"]).split()]
    words = [w for w in words if w]
    if not words:
        return []
    search = "&".join(f"search={urllib.parse.quote(w)}" for w in words)
    prods = _http(f"https://api.bestbuy.com/v1/products(({search}))?apiKey={key}"
                  "&format=json&pageSize=50&show=sku,name")
    skus = [str(p["sku"]) for p in prods.get("products", [])][:50]
    if not skus:
        return []
    time.sleep(0.3)  # Best Buy allows ~5 requests/second
    ob = _http(f"https://api.bestbuy.com/beta/products/openBox(sku%20in({','.join(skus)}))?apiKey={key}&pageSize=100")
    out = []
    for r in ob.get("results", []):
        for offer in r.get("offers", []):
            price = (offer.get("prices") or {}).get("current")
            out.append({
                "source": "bestbuy",
                "source_id": f"{r.get('sku')}-{offer.get('condition')}",
                "title": (r.get("names") or {}).get("title", ""),
                "price": float(price) if price else None,
                "shipping": 0.0,
                "currency": "USD",
                "url": (r.get("links") or {}).get("web"),
                "image": (r.get("images") or {}).get("standard"),
                "location": "",
                "condition": f"open box ({offer.get('condition', '?')})",
                "buying": "best buy",
                "text": "",
            })
    return out


SOURCES = {"ebay": ebay, "ebay_local": ebay_local, "reddit": reddit, "bestbuy": bestbuy}

# Sources that see the same items under the same ids; a listing is stored once per family.
SOURCE_FAMILY = {"ebay_local": "ebay"}


def family(source: str) -> list[str]:
    root = SOURCE_FAMILY.get(source, source)
    return [root, *(s for s, r in SOURCE_FAMILY.items() if r == root)]
