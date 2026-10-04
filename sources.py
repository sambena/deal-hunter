"""Marketplace sources. Each returns a list of raw listing dicts:

    {source, source_id, title, price, shipping, currency, url, image,
     location, condition, buying, text}

`text` is extra description used only for junk filtering.
"""

from __future__ import annotations

import base64
import email.utils
import html
import json
import math
import re
import threading
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


# Reddit rate-limits anonymous RSS hard, and every Reddit source shares the limit: space all requests
# out, and after a 429 leave Reddit alone for a while instead of retrying into it.
REDDIT_GAP_SECONDS = 8
REDDIT_COOLDOWN_SECONDS = 600
_reddit_lock = threading.Lock()
_reddit_state = {"last": 0.0, "blocked_until": 0.0}


def _reddit_feed(sub: str) -> list[dict]:
    # Reddit blocks its JSON API for scripts but still serves RSS.
    url = f"https://www.reddit.com/r/{sub}/new/.rss?limit=100"
    with _reddit_lock:
        wait_out = _reddit_state["blocked_until"] - time.time()
        if wait_out > 0:
            raise SourceError(f"Reddit asked us to slow down; trying again in {int(wait_out / 60) + 1} min")
        gap = REDDIT_GAP_SECONDS - (time.time() - _reddit_state["last"])
        if gap > 0:
            time.sleep(gap)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                root = ET.fromstring(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                _reddit_state["blocked_until"] = time.time() + REDDIT_COOLDOWN_SECONDS
                raise SourceError("Reddit asked us to slow down (HTTP 429); trying again in 10 min") from e
            raise SourceError(f"HTTP {e.code} from reddit") from e
        except (urllib.error.URLError, ET.ParseError) as e:
            raise SourceError(f"couldn't read feed: {e}") from e
        finally:
            _reddit_state["last"] = time.time()
    posts = []
    for entry in root.findall("a:entry", ATOM):
        link = entry.find("a:link", ATOM)
        thumb = entry.find("{http://search.yahoo.com/mrss/}thumbnail")
        cat = entry.find("a:category", ATOM)  # which subreddit, in a combined "a+b+c" feed
        posts.append({
            "id": (entry.findtext("a:id", "", ATOM)).removeprefix("t3_"),
            "title": html.unescape(entry.findtext("a:title", "", ATOM)),
            "url": link.get("href") if link is not None else "",
            "body": _html_to_text(entry.findtext("a:content", "", ATOM)),
            "image": thumb.get("url") if thumb is not None else None,
            "_sub": (cat.get("term") if cat is not None and cat.get("term") else sub).lower(),
        })
    return posts


def reddit_subs(settings: dict) -> list[str]:
    """Every subreddit any Reddit source needs, so one combined request covers them all."""
    subs = [s.strip().lower().removeprefix("r/") for s in settings.get("reddit_subs") or [] if s.strip()]
    if (settings.get("sources_enabled") or {}).get("buildapcsales", True):
        subs.append("buildapcsales")
    return list(dict.fromkeys(subs))


def _reddit_posts(settings: dict) -> list[dict]:
    """One request per poll cycle for all subreddits ("r/a+b+c"), shared by every watch and Reddit source."""
    combined = "+".join(reddit_subs(settings))
    if not combined:
        return []
    if _reddit_cache.get("subs") == combined and time.time() - _reddit_cache["at"] < 300:
        return _reddit_cache["posts"]
    try:
        posts = _reddit_feed(combined)
    except SourceError:
        if _reddit_cache.get("subs") == combined and _reddit_cache["posts"]:
            return _reddit_cache["posts"]  # a recent good fetch beats an error while Reddit cools off
        raise
    _reddit_cache.update(at=time.time(), posts=posts, subs=combined)
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
    wanted = {s.strip().lower().removeprefix("r/") for s in settings.get("reddit_subs") or []}
    for p in _reddit_posts(settings):
        if p["_sub"] not in wanted:
            continue
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


# ---- Retail deal feeds (Slickdeals, r/buildapcsales) ------------------------
# Posts about store deals (Amazon, Newegg, Walmart, Best Buy...). The price is somewhere in the title.

DEAL_PRICE_RE = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+|\d+)(\.\d{1,2})?")
DEAL_CONDITIONS = (("open box", "open box"), ("open-box", "open box"), ("refurb", "refurbished"),
                   ("renewed", "refurbished"), ("resale", "used"), ("used", "used"), ("pre-owned", "used"))


def _first_price(text: str) -> float | None:
    m = DEAL_PRICE_RE.search(text)
    return float(m.group(1).replace(",", "") + (m.group(2) or "")) if m else None


def _deal_condition(title: str) -> str:
    low = title.lower()
    return next((label for word, label in DEAL_CONDITIONS if word in low), "new")


def slickdeals(watch: dict, settings: dict) -> list[dict]:
    terms = search_terms(watch["query"])
    if not terms:
        return []
    url = ("https://slickdeals.net/newsearch.php?searcharea=deals&searchin=first&rss=1&q="
           + urllib.parse.quote_plus(terms))
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            root = ET.fromstring(resp.read())
    except urllib.error.HTTPError as e:
        raise SourceError(f"HTTP {e.code} from slickdeals") from e
    except (urllib.error.URLError, ET.ParseError) as e:
        raise SourceError(f"couldn't read Slickdeals: {e}") from e
    # Search results reach back years; only recent deals might still be live.
    max_age = float(settings.get("deal_max_age_days") or 14) * 86400
    out = []
    for it in root.findall("./channel/item"):
        try:
            posted = email.utils.parsedate_to_datetime(it.findtext("pubDate") or "").timestamp()
        except (TypeError, ValueError):
            posted = time.time()
        if time.time() - posted > max_age:
            continue
        title = html.unescape(it.findtext("title") or "").strip()
        content = it.findtext("{http://purl.org/rss/1.0/modules/content/}encoded") or ""
        img = re.search(r'<img[^>]+src="([^"]+)"', content)
        out.append({
            "source": "slickdeals",
            "source_id": (it.findtext("guid") or it.findtext("link") or title).removeprefix("thread-"),
            "title": title,
            "price": _first_price(title),
            "shipping": None,
            "currency": "USD",
            "url": it.findtext("link"),
            "image": html.unescape(img.group(1)) if img else None,
            "location": "",
            "condition": _deal_condition(title),
            "buying": "slickdeals",
            "text": "",
        })
    return out


def buildapcsales(watch: dict, settings: dict) -> list[dict]:
    out = []
    for p in _reddit_posts(settings):
        if p["_sub"] != "buildapcsales":
            continue
        title = p["title"]
        out.append({
            "source": "buildapcsales",
            "source_id": p["id"],
            "title": title,
            "price": _first_price(title),  # "[GPU] Zotac RTX 3060 12GB - $249.99 (Newegg)"
            "shipping": None,
            "currency": "USD",
            "url": p["url"],
            "image": p["image"],
            "location": "",
            "condition": _deal_condition(title),
            "buying": "r/buildapcsales",
            "text": "",
        })
    return out


# ---- KSL Classifieds (Utah; local pickup) -----------------------------------
# No API or RSS. The search page carries its results as JSON inside the Next.js payload
# (SearchStoreProvider -> initialState.results). KSL runs bot protection, so: one request per watch
# per cycle, a few seconds apart, with ordinary browser headers.

KSL_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
KSL_GAP_SECONDS = 4
_ksl_last = {"at": 0.0}


def _ksl_results(page: str) -> list[dict]:
    for chunk in re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', page, re.S):
        if "SearchStoreProvider" not in chunk:
            continue
        text = json.loads(f'"{chunk}"')  # it's a JS string literal holding the payload
        at = text.find('"results":')
        if at < 0:
            continue
        results, _ = json.JSONDecoder().raw_decode(text, at + len('"results":'))
        # A list of pages, each a list of listings.
        return [r for page_ in results for r in (page_ if isinstance(page_, list) else [page_])]
    raise SourceError("couldn't find listings on the KSL page (did the site change?)")


def ksl(watch: dict, settings: dict) -> list[dict]:
    zip_code = str(settings.get("zip_code") or "").strip()
    if not zip_code:
        raise SourceError("set your ZIP code in Settings > Local area")
    terms = search_terms(watch["query"])
    if not terms:
        return []
    radius = max(1, int(float(settings.get("local_radius_miles") or 50)))
    url = (f"https://classifieds.ksl.com/search/keyword/{urllib.parse.quote(terms, safe='')}"
           f"/zip/{urllib.parse.quote(zip_code, safe='')}/miles/{radius}")
    wait = KSL_GAP_SECONDS - (time.time() - _ksl_last["at"])
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(url, headers=KSL_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            page = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code in (403, 429):
            raise SourceError(f"KSL's bot protection blocked the request (HTTP {e.code}); "
                              "it usually clears on a later check") from e
        raise SourceError(f"HTTP {e.code} from KSL") from e
    except urllib.error.URLError as e:
        raise SourceError(f"Network error reaching KSL: {e.reason}") from e
    finally:
        _ksl_last["at"] = time.time()
    if "Access to this page has been denied" in page:
        raise SourceError("KSL's bot protection blocked the request; it usually clears on a later check")
    out = []
    for r in _ksl_results(page):
        if r.get("marketType") != "Sale":  # skip wanted / rental / service posts
            continue
        loc = r.get("location") or {}
        seller = (r.get("sellerType") or "").lower()
        out.append({
            "source": "ksl",
            "source_id": str(r["id"]),
            "title": r.get("title") or "",
            "price": float(r["price"]) if r.get("price") else None,
            "shipping": 0.0,  # local pickup
            "currency": "USD",
            "url": f"https://classifieds.ksl.com/listing/{r['id']}",
            "image": (r.get("primaryImage") or {}).get("url"),
            "location": ", ".join(x for x in (loc.get("city"), loc.get("state")) if x),
            "condition": "used",
            "buying": f"KSL · {seller} seller" if seller else "KSL",
            "text": "",
        })
    return out


# ---- Craigslist (local; the search page's own JSON API) ---------------------
# sapi.craigslist.org answers the search page with packed rows: offsets from a base id/date, the price,
# "area:place~lat~lon", then tagged lists ([4, images...], [6, slug], [13, uuid]) and the title last.
# Searches spill into nearby areas, so rows farther than the radius are dropped here.

CRAIGSLIST_URL = "https://sapi.craigslist.org/web/v8/postings/search/full"
CRAIGSLIST_GAP_SECONDS = 3
_craigslist_last = {"at": 0.0}
_places: dict = {}  # ZIP -> {lat, lon, city, state}, from Craigslist's answers (OfferUp needs it too)


def _miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 3958.8 * 2 * math.asin(math.sqrt(a))


def _craigslist_search(zip_code: str, radius: int, query: str, lo=None, hi=None) -> dict:
    params = {"batch": "1-0-360-0-0", "cc": "US", "lang": "en", "searchPath": "sss",
              "postal": zip_code, "search_distance": str(radius), "query": query}
    if lo:
        params["min_price"] = str(int(lo))
    if hi:
        params["max_price"] = str(int(math.ceil(hi)))
    wait = CRAIGSLIST_GAP_SECONDS - (time.time() - _craigslist_last["at"])
    if wait > 0:
        time.sleep(wait)
    try:
        data = _http(CRAIGSLIST_URL + "?" + urllib.parse.urlencode(params),
                     headers={**KSL_HEADERS, "Accept": "application/json", "Referer": "https://www.craigslist.org/"})
    finally:
        _craigslist_last["at"] = time.time()
    data = data.get("data") or {}
    loc = data.get("location") or {}
    if loc.get("lat") is not None:
        _places[zip_code] = {"lat": loc["lat"], "lon": loc["lon"], "city": loc.get("city", ""),
                             "state": loc.get("region", "")}
    return data


def zip_place(zip_code: str) -> dict:
    """Where a ZIP is (lat/lon, city, state), asked of Craigslist once and remembered."""
    if zip_code not in _places:
        _craigslist_search(zip_code, 1, "")
    if zip_code not in _places:
        raise SourceError(f"couldn't find where ZIP {zip_code} is")
    return _places[zip_code]


def _craigslist_row(row: list, decode: dict) -> dict | None:
    try:
        tags = {r[0]: r[1:] for r in row[6:-1] if isinstance(r, list) and r}
        area, place, lat, lon = re.match(r"(\d+):(\d+)~([-\d.]+)~([-\d.]+)", row[4]).groups()
        places = decode.get("locationDescriptions") or []
        return {"id": decode["minPostingId"] + row[0], "posted": decode["minPostedDate"] + row[1],
                "price": row[3] if row[3] >= 0 else None, "lat": float(lat), "lon": float(lon),
                "place": places[int(place)] if int(place) < len(places) and places[int(place)] else "",
                "images": tags.get(4, []), "slug": (tags.get(6) or [""])[0], "uuid": (tags.get(13) or [""])[0],
                "title": row[-1] if isinstance(row[-1], str) else ""}
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return None  # a row in a shape we don't know; skip it rather than fail the whole search


def craigslist(watch: dict, settings: dict) -> list[dict]:
    zip_code = str(settings.get("zip_code") or "").strip()
    if not zip_code:
        raise SourceError("set your ZIP code in Settings > Local area")
    terms = search_terms(watch["query"])
    if not terms:
        return []
    radius = max(1, int(float(settings.get("local_radius_miles") or 50)))
    data = _craigslist_search(zip_code, radius, terms, watch.get("min_price"), watch.get("max_price"))
    decode, home = data.get("decode") or {}, data.get("location") or {}
    if data and "minPostingId" not in decode and data.get("items"):
        raise SourceError("couldn't read Craigslist's results (did the site change?)")
    out = []
    for raw in data.get("items") or []:
        r = _craigslist_row(raw, decode)
        if not r or not r["title"]:
            continue
        if home.get("lat") is not None and _miles(home["lat"], home["lon"], r["lat"], r["lon"]) > radius:
            continue
        img = r["images"][0].split(":", 1)[-1] if r["images"] else None
        out.append({
            "source": "craigslist",
            "source_id": str(r["id"]),
            "title": r["title"],
            "price": float(r["price"]) if r["price"] is not None else None,
            "shipping": 0.0,  # local pickup
            "currency": "USD",
            "url": (f"https://www.craigslist.org/view/d/{r['slug']}/{r['uuid']}" if r["uuid"]
                    else f"https://www.craigslist.org/search/sss?query={urllib.parse.quote(terms)}"),
            "image": f"https://images.craigslist.org/{img}_300x300.jpg" if img else None,
            "location": r["place"] if len(r["place"]) <= 40 else "",  # some sellers put ads in this field
            "condition": "used",
            "buying": "Craigslist",
            "text": "",
        })
    return out


# ---- OfferUp (local; the search page's Next.js data) ------------------------
# No public API. The search page carries its results in __NEXT_DATA__; the area comes from the
# ou.location cookie (otherwise OfferUp guesses from the IP), and radius only takes 5/10/20/30/50 miles.

OFFERUP_RADII = (5, 10, 20, 30, 50)
OFFERUP_GAP_SECONDS = 4
_offerup_last = {"at": 0.0}


def _offerup_listings(page: str) -> list[dict]:
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
    if not m:
        raise SourceError("couldn't find listings on the OfferUp page (did the site change?)")
    feed = json.loads(m.group(1)).get("props", {}).get("pageProps", {}).get("searchFeedResponse")
    if feed is None:
        raise SourceError("couldn't find listings on the OfferUp page (did the site change?)")
    found: dict = {}

    def walk(node):
        if isinstance(node, dict):
            if node.get("__typename") == "ModularFeedListing" and node.get("listingId"):
                found.setdefault(node["listingId"], node)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(feed)  # listings sit in looseTiles and inside modules
    return list(found.values())


def offerup(watch: dict, settings: dict) -> list[dict]:
    zip_code = str(settings.get("zip_code") or "").strip()
    if not zip_code:
        raise SourceError("set your ZIP code in Settings > Local area")
    terms = search_terms(watch["query"])
    if not terms:
        return []
    place = zip_place(zip_code)
    radius = int(float(settings.get("local_radius_miles") or 50))
    radius = next((r for r in OFFERUP_RADII if r >= radius), OFFERUP_RADII[-1])
    params = {"q": terms, "radius": str(radius), "sort": "-posted"}
    if watch.get("min_price"):
        params["price_min"] = str(int(watch["min_price"]))
    if watch.get("max_price"):
        params["price_max"] = str(int(math.ceil(watch["max_price"])))
    cookie = json.dumps({"city": place["city"], "state": place["state"], "zipCode": zip_code,
                         "longitude": place["lon"], "latitude": place["lat"], "source": "manual"},
                        separators=(",", ":"))
    wait = OFFERUP_GAP_SECONDS - (time.time() - _offerup_last["at"])
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request("https://offerup.com/search?" + urllib.parse.urlencode(params),
                                 headers={**KSL_HEADERS, "Cookie": "ou.location=" + urllib.parse.quote(cookie)})
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            page = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code in (403, 429):
            raise SourceError(f"OfferUp's bot protection blocked the request (HTTP {e.code}); "
                              "it usually clears on a later check") from e
        raise SourceError(f"HTTP {e.code} from OfferUp") from e
    except urllib.error.URLError as e:
        raise SourceError(f"Network error reaching OfferUp: {e.reason}") from e
    finally:
        _offerup_last["at"] = time.time()
    out = []
    for r in _offerup_listings(page):
        try:
            price = float(r["price"]) if r.get("price") not in (None, "") else None
        except (TypeError, ValueError):
            price = None
        if price is not None and price <= 1:
            price = None  # "$1" / "$0" is a placeholder for "make an offer"
        flags = r.get("flags") or []
        out.append({
            "source": "offerup",
            "source_id": r["listingId"],
            "title": r.get("title") or "",
            "price": price,
            "shipping": 0.0 if "LOCAL_PICKUP" in flags else None,
            "currency": "USD",
            "url": f"https://offerup.com/item/detail/{r['listingId']}",
            "image": (r.get("image") or {}).get("url"),
            "location": r.get("locationName") or "",
            "condition": (r.get("conditionText") or "used").lower(),
            "buying": "OfferUp" + (" · ships" if "SHIPPING" in flags else ""),
            "text": "",
        })
    return out


SOURCES = {"ebay": ebay, "ebay_local": ebay_local, "reddit": reddit, "bestbuy": bestbuy,
           "slickdeals": slickdeals, "buildapcsales": buildapcsales, "ksl": ksl, "craigslist": craigslist,
           "offerup": offerup}

# Sources that see the same items under the same ids; a listing is stored once per family.
SOURCE_FAMILY = {"ebay_local": "ebay"}


def family(source: str) -> list[str]:
    root = SOURCE_FAMILY.get(source, source)
    return [root, *(s for s, r in SOURCE_FAMILY.items() if r == root)]
