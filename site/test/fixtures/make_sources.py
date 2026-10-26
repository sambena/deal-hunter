"""Records the Python app's sources (sources.py) on made-up pages, for test/sources.test.ts. Nothing is fetched: urlopen
answers from RULES below (the first whose URL prefix, and every "has" part, fits), and time.time / time.sleep are a
fake clock, so every rate gap is recorded too. Made-up listings, people and places only.

Run from the repository root:  python site/test/fixtures/make_sources.py > site/test/fixtures/sources-python.json
Prints {"rules": [...], "calls": [...], "results": [...]}: each result is the source's listings or its error, with
the requests it sent (method, URL, headers, body) and the seconds it slept."""
import email.utils
import io
import json
import sys
import time
import urllib.error
import urllib.request

T0 = 1793000000.0
clock = [T0]
slept = []
time.time = lambda: clock[0]


def fake_sleep(seconds):
    slept.append(seconds)
    clock[0] += seconds


time.sleep = fake_sleep

import sources  # noqa: E402 - after the clock is faked

sent = []


def date(seconds_ago):
    return email.utils.formatdate(T0 - seconds_ago, usegmt=True)


def next_page(payload):
    """A Next.js page carrying payload in one self.__next_f.push chunk (a JS string literal)."""
    return ('<html><script>self.__next_f.push([1,"' + json.dumps(payload)[1:-1] + '"])</script>'
            '<script src="https://cars.ksl.com/_next/static/chunks/app-made-up.js"></script></html>')


def ksl_payload(results):
    return 'x:["$","SearchStoreProvider",null,{"initialState":{"results":' + json.dumps(results) + ',"other":1}}]'


ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:media="http://search.yahoo.com/mrss/">
<entry><id>t3_aaa1</id><title>[USA-UT] [H] RTX 3080 FE &amp; i9-10980XE [W] PayPal, local cash</title>
 <link href="https://www.reddit.com/r/hardwareswap/comments/aaa1/"/><category term="hardwareswap"/>
 <content type="html">&lt;p&gt;RTX 3080 FE - $450 shipped&lt;br/&gt;i9-10980XE $520 obo&lt;/p&gt;&lt;p&gt;Timestamps &amp;amp; more&lt;/p&gt;</content>
 <media:thumbnail url="https://i.example.test/aaa1.jpg"/></entry>
<entry><id>t3_aaa2</id><title>[US-CA] [H] PayPal [W] RTX 3080</title><link href="https://www.reddit.com/r/hardwareswap/comments/aaa2/"/>
 <category term="hardwareswap"/><content type="html">buying</content></entry>
<entry><id>t3_aaa3</id><title>[FS] Dell R730 server, 3080 too</title><link href="https://www.reddit.com/r/hardwareswap/comments/aaa3/"/>
 <category term="HardwareSwap"/><content type="html">Server 3080 shipped for 300 usd&lt;br&gt;nothing else</content></entry>
<entry><id>t3_bbb1</id><title>[GPU] Zotac RTX 3080 12GB - $1,249.99 (Newegg) open box</title>
 <link href="https://www.reddit.com/r/buildapcsales/comments/bbb1/"/><category term="buildapcsales"/><content type="html">deal</content></entry>
<entry><id>bbb2</id><title>[CPU] Ryzen 5 5600X $129 (Amazon renewed)</title><link href="https://www.reddit.com/r/buildapcsales/comments/bbb2/"/>
 <category term="buildapcsales"/><content type="html">deal &amp;eacute;t&amp;eacutee &amp;#x41;&amp;#128;</content></entry>
<entry><id>t3_ccc1</id><title>[H] RTX 3080 [W] cash</title><link href="https://www.reddit.com/r/homelabsales/comments/ccc1/"/>
 <category term="homelabsales"/><content type="html">RTX3080 $400</content></entry>
</feed>"""

RSS = f"""<?xml version="1.0"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel><title>deals</title>
<item><title>RTX 3080 FE &amp;#36;499.99 at Made-up Store (open-box)</title><link>https://slickdeals.net/f/1</link><guid>thread-111</guid>
 <pubDate>{date(3600)}</pubDate><content:encoded><![CDATA[<p><img class="x" src="https://i.example.test/a.jpg?a=1&amp;b=2"/></p>]]></content:encoded></item>
<item><title>  Old RTX 3080 deal $300  </title><link>https://slickdeals.net/f/2</link><guid>thread-222</guid><pubDate>{date(40 * 86400)}</pubDate></item>
<item><title>RTX 3080 refurb $1,050</title><link>https://slickdeals.net/f/3</link><pubDate>not a date</pubDate></item>
<item><title>RTX 3080 no price</title><link>https://slickdeals.net/f/4</link><pubDate>{date(86400)}</pubDate></item>
<item><title>RTX 3080 two digit year $5.5</title><guid></guid><link>https://slickdeals.net/f/5</link><pubDate>Mon, 26 Oct 26 01:00:00 GMT</pubDate></item>
<item><title>RTX 3080 -0000 $10</title><guid>g6</guid><pubDate>26 Oct 2026 01:00:00 -0000</pubDate></item>
</channel></rss>"""

CL_ITEMS = [
    [1, 10, 0, 250, "1:3~40.7608~-111.8910", 0, [4, "3:00a0a_abc", "3:00b0b_def"], [6, "rtx-3080-card"], [13, "uuid-1"], "RTX 3080 card"],
    [2, 20, 0, -1, "1:9~40.70~-111.90", 0, [6, "slug"], "RTX 3080 no price"],
    [3, 30, 0, 300, "1:1~41.9000~-111.5000", 0, "RTX 3080 far away"],
    [4, 40, 0, 99, "bad place", 0, "RTX 3080 bad row"],
    [5, 50, 145, 15000, "1:2~40.75~-111.88", 0, [9, 61000], [4, "3:"], "2018 Toyota Tacoma"],
    [6, 60, 146, 22000, "1:2~40.75~-111.88", 0, [9, "70000"], "2016 Ford F-150 " + "x" * 50],
    [7, 70, 0, 120, "1:0~40.76~-111.89", 0, [13, ""], ""],
]
CL_DATA = {"data": {"decode": {"minPostingId": 7000000000, "minPostedDate": 1790000000,
                               "locationDescriptions": ["", "Ogden", "Somewhere, UT", "A place name that is far longer than forty characters"]},
                    "location": {"lat": 40.7608, "lon": -111.891, "city": "Made-up City", "region": "UT"},
                    "items": CL_ITEMS}}

OFFERUP = {"props": {"pageProps": {"searchFeedResponse": {"looseTiles": [
    {"__typename": "ModularFeedListing", "listingId": "ou1", "title": "RTX 3080", "price": "425.00", "flags": ["LOCAL_PICKUP"],
     "image": {"url": "https://i.example.test/ou1.jpg"}, "locationName": "Somewhere, UT", "conditionText": "Used"},
    {"__typename": "ModularFeedListing", "listingId": "ou2", "title": "RTX 3080 offer", "price": "1", "flags": ["SHIPPING"]},
    {"__typename": "Banner", "listingId": "nope"}],
    "modules": [{"tiles": [{"__typename": "ModularFeedListing", "listingId": "ou1", "title": "duplicate"},
                           {"__typename": "ModularFeedListing", "listingId": "ou3", "title": "2018 Tacoma", "price": "abc",
                            "vehicleMiles": 61000}]}]}}}}

POSHMARK = {"$_search": {"gridData": {"data": [
    {"id": "pm1", "title": "Running shoes", "price_amount": {"val": "35.0"}, "picture_url": "https://i.example.test/pm1.jpg",
     "condition": "nwt", "brand": "Made-up Brand", "department": {"display": "Men"}, "size": "10.5"},
    {"id": "pm2", "title": "Sold shoes", "inventory": {"status": "sold_out"}},
    {"id": "pm3", "title": "No price", "price_amount": {"val": None}, "condition": "zz"},
    {"title": "no id"}]}}}

KSL_RESULTS = [[{"id": 101, "title": "RTX 3080", "price": 400, "marketType": "Sale", "sellerType": "Private",
                 "location": {"city": "Made-up City", "state": "UT"}, "primaryImage": {"url": "https://i.example.test/k101.jpg"}},
                {"id": 102, "title": "Wanted: RTX 3080", "marketType": "Wanted"}],
               {"id": 103, "title": None, "price": 0, "marketType": "Sale"}]
KSL_CARS_1 = {"results": [[{"id": 9001, "makeYear": 2018, "make": "Toyota", "model": "Tacoma", "trim": "TRD", "price": 27500,
                            "mileage": 61000, "sellerType": "Dealer", "dealer": {"name": "Made-up Motors"},
                            "location": {"city": "Made-up City", "state": "UT"}, "newUsed": "Used"},
                           {"id": 9002, "title": "2016 Tacoma", "price": 22000, "mileage": 0, "sellerType": "Private"},
                           {"id": 9003, "listingType": "BOAT"}]]}
KSL_CARS_PAGE = ('x:["$","SearchStoreProvider",null,{"initialState":' + json.dumps(KSL_CARS_1, separators=(",", ":"))
                 + '}] "searchParams":{"keyword":"toyota tacoma","page":1} "pageInfo":[{"hasNextPage":true,"endCursor":"c1","total":3}]')
JS = 'a=1;(0,b.createServerReference)("' + "ab" * 21 + '",c.callServer,void 0,d.findSourceMapURL,"search");'
MORE = '0:"$@1"\n1:' + json.dumps({"items": [{"id": 9004, "makeYear": 2019, "make": "Toyota", "model": "Tacoma", "price": 30000},
                                                {"id": 9001, "title": "repeat"}], "pageInfo": {"hasNextPage": False}}) + '\n'

EBAY_ITEMS = {"itemSummaries": [
    {"itemId": "v1|1|0", "title": "RTX 3080 FE", "price": {"value": "449.99", "currency": "USD"},
     "shippingOptions": [{"shippingCost": {"value": "12.50"}}], "itemWebUrl": "https://www.ebay.example.test/1",
     "image": {"imageUrl": "https://i.example.test/e1.jpg"}, "itemLocation": {"city": "Made-up", "stateOrProvince": "UT", "country": "US"},
     "condition": "Used", "buyingOptions": ["FIXED_PRICE", "BEST_OFFER"], "compatibilityMatch": "EXACT"},
    {"itemId": "v1|2|0", "title": "RTX 3080 auction", "currentBidPrice": {"value": "300"}, "shippingOptions": [{"type": "x"}],
     "condition": "For parts or not working", "buyingOptions": ["AUCTION"], "compatibilityMatch": "POSSIBLE"},
    {"itemId": "v1|3|0", "price": {}}]}

RULES = [
    {"url": "https://api.ebay.com/identity/v1/oauth2/token", "json": {"access_token": "made-up-token", "expires_in": 7200}},
    {"url": "https://api.ebay.com/commerce/taxonomy/", "has": ["q=wiper"], "status": 500, "body": "oops"},
    {"url": "https://api.ebay.com/commerce/taxonomy/", "json": {"categorySuggestions": [
        {"category": {"categoryId": "111"}, "categoryTreeNodeAncestors": [{"categoryId": "1"}]},
        {"category": {"categoryId": "33707"}, "categoryTreeNodeAncestors": [{"categoryId": "6030"}, {"categoryId": "6028"}]}]}},
    {"url": "https://api.ebay.com/buy/browse/v1/item_summary/search?", "has": ["compatibility_filter"], "status": 400,
     "json": {"errors": [{"errorId": 12506, "message": "The category ID submitted does not support fitment. More."}]}},
    {"url": "https://api.ebay.com/buy/browse/v1/item_summary/search?", "has": ["limit=200"], "status": 400,
     "json": {"errors": [{"errorId": 12023, "message": "Too large."}]}},
    {"url": "https://api.ebay.com/buy/browse/v1/item_summary/search?", "has": ["q=nothing"], "status": 500, "body": "<html>down</html>"},
    {"url": "https://api.ebay.com/buy/browse/v1/item_summary/search?", "has": ["q=weird"], "status": 400,
     "json": {"errors": [{"errorId": 99, "message": "Something odd happened. Then more."}]}},
    {"url": "https://api.ebay.com/buy/browse/v1/item_summary/search?", "json": EBAY_ITEMS},
    {"url": "https://www.reddit.com/r/hardwareswap+buildapcsales/", "body": ATOM},
    {"url": "https://www.reddit.com/r/hardwareswap+homelabsales+buildapcsales/", "body": ATOM},
    {"url": "https://www.reddit.com/r/homelabsales/", "status": 429, "body": ""},
    {"url": "https://www.reddit.com/r/broken/", "body": "<feed><entry></feed>"},
    {"url": "https://api.bestbuy.com/v1/products((search=nothing", "json": {"products": []}},
    {"url": "https://api.bestbuy.com/v1/products(", "json": {"products": [{"sku": 6430620}, {"sku": "6430621"}]}},
    {"url": "https://api.bestbuy.com/beta/products/openBox(", "json": {"results": [
        {"sku": 6430620, "names": {"title": "RTX 3080 FE"}, "links": {"web": "https://bestbuy.example.test/1"},
         "images": {"standard": "https://i.example.test/b1.jpg"},
         "offers": [{"condition": "excellent", "prices": {"current": 599.99}}, {"condition": "fair", "prices": {}}]},
        {"sku": 6430621, "offers": [{"prices": {"current": "0"}}]}]}},
    {"url": "https://slickdeals.net/newsearch.php", "has": ["q=broken"], "body": "<rss><channel><item></rss>"},
    {"url": "https://slickdeals.net/newsearch.php", "has": ["q=gone"], "status": 503, "body": ""},
    {"url": "https://slickdeals.net/newsearch.php", "has": ["q=offline"], "network": "[Errno 11001] made-up failure"},
    {"url": "https://slickdeals.net/newsearch.php", "body": RSS},
    {"url": "https://classifieds.ksl.com/search/keyword/denied", "body": "<h1>Access to this page has been denied</h1>"},
    {"url": "https://classifieds.ksl.com/search/keyword/blocked", "status": 403, "body": ""},
    {"url": "https://classifieds.ksl.com/search/keyword/changed", "body": "<html>no payload</html>"},
    {"url": "https://classifieds.ksl.com/search/keyword/", "body": next_page(ksl_payload(KSL_RESULTS))},
    {"url": "https://cars.ksl.com/_next/static/chunks/app-made-up.js", "body": JS},
    {"url": "https://cars.ksl.com/search/keyword/ford", "method": "POST", "body": "garbage"},
    {"url": "https://cars.ksl.com/search/", "method": "POST", "body": MORE},
    {"url": "https://cars.ksl.com/search/", "body": next_page(KSL_CARS_PAGE)},
    {"url": "https://poshmark.com/search?query=broken", "body": "window.__INITIAL_STATE__ = {not json"},
    {"url": "https://poshmark.com/search", "body": "<script>window.__INITIAL_STATE__ = " + json.dumps(POSHMARK) + ";</script>"},
    {"url": "https://sapi.craigslist.org/web/v8/postings/search/full", "has": ["postal=99999"], "json": {"data": {}}},
    {"url": "https://sapi.craigslist.org/web/v8/postings/search/full", "json": CL_DATA},
    {"url": "https://offerup.com/search", "body": '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(OFFERUP) + "</script>"},
]


class Answer(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def fake_urlopen(req, timeout=None):
    if isinstance(req, str):
        req = urllib.request.Request(req)
    url, method = req.full_url, req.get_method()
    sent.append({"method": method, "url": url, "headers": {k.lower(): v for k, v in req.header_items()},
                 "body": req.data.decode() if req.data else None, "timeout": timeout})
    for rule in RULES:
        if url.startswith(rule["url"]) and all(h in url for h in rule.get("has", [])) and rule.get("method", method) == method:
            break
    else:
        rule = {"status": 404, "body": "not here"}
    if "network" in rule:
        raise urllib.error.URLError(rule["network"])
    body = (json.dumps(rule["json"]) if "json" in rule else rule.get("body", "")).encode()
    status = rule.get("status", 200)
    if status >= 400:
        raise urllib.error.HTTPError(url, status, "made up", {}, io.BytesIO(body))
    return Answer(body)


urllib.request.urlopen = fake_urlopen

S = {"zip_code": "84101", "local_radius_miles": 50, "ebay_client_id": "made-up-id", "ebay_client_secret": "made-up-secret",
     "ebay_marketplace": "EBAY_US", "reddit_subs": ["hardwareswap"], "sources_enabled": {"buildapcsales": True},
     "bestbuy_api_key": "made-up-key", "deal_max_age_days": 14}
W = {"query": "rtx 3080", "kind": "item", "min_price": None, "max_price": None, "condition": "any", "include_auctions": False}
CALLS = [
    ("ebay", W, S, 0), ("ebay", {**W, "query": "rtx|gtx 3080 -ti", "min_price": 100.0, "max_price": 450.5, "condition": "used",
                                  "include_auctions": True}, {**S, "zip_code": ""}, 10),
    ("ebay_local", W, S, 10), ("ebay_local", W, {**S, "zip_code": " "}, 0), ("ebay", W, {**S, "ebay_client_id": ""}, 0),
    ("ebay", {**W, "query": "toyota pickup", "kind": "vehicle", "max_price": 30000.0}, S, 10),
    ("ebay", {**W, "query": "tacoma", "kind": "vehicle"}, S, 0),
    ("ebay", {**W, "query": "brake pads", "fits": {"year": 2018, "make": "Toyota", "model": "Tacoma"}}, S, 10),
    ("ebay", {**W, "query": "brake pads", "fits": {"year": 2018, "make": "Toyota", "model": "Tacoma"}}, S, 0),
    ("ebay", {**W, "query": "wiper", "fits": {"year": 2018, "make": "Toyota", "model": "Tacoma"}}, S, 0),
    ("ebay", {**W, "query": "nothing"}, S, 0), ("ebay", {**W, "query": "weird"}, S, 0),
    ("ebay", W, {**S, "ebay_client_id": "other-id"}, 8000),
    ("reddit", W, S, 0), ("buildapcsales", W, S, 1), ("reddit", {**W, "query": "i9-10980xe"}, S, 100),
    ("reddit", {**W, "query": "server"}, {**S, "reddit_subs": ["  r/HardwareSwap ", "homelabsales", ""]}, 400),
    ("reddit", W, {**S, "reddit_subs": ["homelabsales"], "sources_enabled": {"buildapcsales": False}}, 0),
    ("reddit", W, {**S, "reddit_subs": ["homelabsales"], "sources_enabled": {"buildapcsales": False}}, 30),
    ("reddit", W, {**S, "reddit_subs": ["broken"], "sources_enabled": {"buildapcsales": False}}, 700),
    ("reddit", W, {**S, "reddit_subs": [], "sources_enabled": {"buildapcsales": False}}, 0),
    ("bestbuy", W, S, 0), ("bestbuy", {**W, "query": "nothing"}, S, 0), ("bestbuy", {**W, "query": "!!"}, S, 0),
    ("bestbuy", W, {**S, "bestbuy_api_key": ""}, 0),
    ("slickdeals", W, S, 0), ("slickdeals", {**W, "query": "rtx 3080"}, {**S, "deal_max_age_days": 60}, 0),
    ("slickdeals", {**W, "query": "broken"}, S, 0), ("slickdeals", {**W, "query": "gone"}, S, 0),
    ("slickdeals", {**W, "query": "offline"}, S, 0), ("slickdeals", {**W, "query": "-only"}, S, 0),
    ("ksl", W, S, 0), ("ksl", W, S, 1), ("ksl", {**W, "query": "denied"}, S, 10), ("ksl", {**W, "query": "blocked"}, S, 0),
    ("ksl", {**W, "query": "changed"}, {**S, "local_radius_miles": "0.5"}, 0), ("ksl", W, {**S, "zip_code": ""}, 0),
    ("ksl_cars", {**W, "query": "toyota tacoma", "kind": "vehicle", "year_min": 2016, "year_max": 2020, "max_miles": 90000,
                  "min_price": 5000.5, "max_price": 30000.0}, S, 10),
    ("ksl_cars", {**W, "query": "pickup", "kind": "vehicle"}, S, 0),
    ("ksl_cars", {**W, "query": "ford", "kind": "vehicle"}, S, 0),
    ("ksl_cars", {**W, "query": "ford", "kind": "vehicle"}, S, 0),
    ("poshmark", {**W, "query": "running shoes", "kind": "clothing", "department": "men", "size": "W32 L30"}, S, 0),
    ("poshmark", {**W, "query": "running shoes", "kind": "clothing", "size": "10 1/2"}, S, 1),
    ("poshmark", {**W, "query": "broken"}, S, 5),
    ("craigslist", {**W, "min_price": 100.7, "max_price": 450.2}, S, 0), ("craigslist", W, {**S, "local_radius_miles": 30}, 0),
    ("craigslist", {**W, "query": "toyota pickup", "kind": "vehicle", "year_min": 2015, "max_miles": 100000}, S, 0),
    ("craigslist", W, {**S, "zip_code": "99999"}, 0),
    ("offerup", W, S, 0), ("offerup", {**W, "query": "tacoma", "kind": "vehicle", "year_min": 2016, "max_miles": 60000,
                                       "min_price": 1000.9, "max_price": 30000.1}, {**S, "local_radius_miles": 90}, 0),
    ("offerup", W, {**S, "zip_code": "99999"}, 0), ("offerup", W, {**S, "local_radius_miles": 500}, 10),
]

calls, results = [], []
for name, watch, settings, advance in CALLS:
    clock[0] += advance
    sent.clear()
    slept.clear()
    calls.append({"source": name, "watch": watch, "settings": settings, "advance": advance})
    try:
        out = {"out": sources.SOURCES[name](watch, settings)}
    except Exception as e:  # noqa: BLE001 - recorded, so the port must fail the same way
        out = {"error": type(e).__name__, "message": str(e)}
    results.append({**out, "sent": list(sent), "slept": list(slept), "clock": clock[0]})

import html  # noqa: E402
import urllib.parse  # noqa: E402
from email.utils import parsedate_to_datetime  # noqa: E402


def attempt(fn, *args):
    try:
        return {"in": list(args), "out": fn(*args)}
    except Exception as e:  # noqa: BLE001
        return {"in": list(args), "error": type(e).__name__}


def stamp(text):
    return parsedate_to_datetime(text).timestamp()


def raw(text, idx):
    return list(json.JSONDecoder().raw_decode(text, idx))


helpers = {
    "unescape": [attempt(html.unescape, t) for t in [
        "a &amp; b", "&lt;&gt;&quot;&#39;&apos;", "&eacute;t&eacute &notit; &notin; &not", "&#x41;&#65;&#x1F600;&#0;&#128;&#x80;",
        "&#xD800;&#1114112;&#11;&#xFFFE;", "&amp", "&ampx;", "& alone", "&#;&#x;", "&unknownentity;", "&AMP;&Amp;", "plain",
        "&#00065;&#x0041", "&lt&gt", "&NotNestedLessLess;"]],
    "parsedate": [attempt(stamp, t) for t in [
        "Mon, 26 Oct 2026 01:00:00 GMT", "26 Oct 2026 01:00:00 +0130", "Mon,26 Oct 2026 01:00 -0700", "26-Oct-26 01:00:00 EST",
        "Oct 26 2026 01:00:00 PDT", "26 October 2026 01.02.03 +0000", "Mon, 30 Feb 2026 01:00:00 GMT", "26 Oct 2026 24:00:00 GMT",
        "26 Oct 69 01:00:00 GMT", "26 Oct 68 01:00:00 GMT", "26 Oct 2026", "not a date", "",
        "26 Zzz 2026 01:00:00 GMT", "Oct, 26 2026 01:00:00 GMT"]],
    "quote": [attempt(urllib.parse.quote, t, safe) for t in ["rtx 3080", "a/b?c=d&e", "é ü~*()!'", "x+y"] for safe in ["", "/"]],
    "quote_plus": [attempt(urllib.parse.quote_plus, t) for t in ["rtx 3080", "a/b?c=d&e", "é ü~*()!'", "x+y"]],
    "urlencode": [attempt(urllib.parse.urlencode, [("q", "a b"), ("size[]", "10"), ("t", ["7", "9"])], True),
                  attempt(urllib.parse.urlencode, [("q", "é/&="), ("n", 5)])],
    "raw_decode": [attempt(raw, t, i) for t, i in [('x:[1,{"a":"]}"}],2', 2), ('"results": [1]', 11), ('"results":[1]', 10),
                                                   ('k:"str\\"ing"', 2), ('k:12.5e3,', 2), ('k:true', 2), ('k:{"a":[', 2), ('k:-0', 2)]],
    "dumps": [attempt(json.dumps, v) for v in [[None, "0", {"a": 1, "b": [True, False]}, {"k": "é\n\t\\"}, "c"], {"x": 1.5}]]
,
    "dumps_compact": [attempt(lambda v: json.dumps(v, separators=(",", ":")), {"city": "Ünï", "lon": -111.9, "z": "84101"})],
}

json.dump({"T0": T0, "rules": RULES, "calls": calls, "results": results, "helpers": helpers}, sys.stdout, ensure_ascii=False, indent=0)
sys.stdout.write("\n")
