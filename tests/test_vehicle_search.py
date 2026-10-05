"""Car and truck searches that find what the sites have: body styles and more KSL pages."""

import json
import sys
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import matching  # noqa: E402
import sources  # noqa: E402

PICKUP = {"query": "Pickup", "kind": "vehicle", "min_price": 3000, "max_price": 5000, "exclude": []}
SETTINGS = {"zip_code": "84057", "local_radius_miles": 50}


class Resp:
    def __init__(self, body): self.body = body if isinstance(body, bytes) else body.encode()
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def read(self): return self.body


class BodyStyleTest(unittest.TestCase):
    def test_body_style(self):
        self.assertEqual(matching.body_style("Pickup"), ("pickup", ""))
        self.assertEqual(matching.body_style("ford trucks"), ("pickup", "ford"))
        self.assertEqual(matching.body_style("toyota tacoma"), (None, "toyota tacoma"))

    def test_titles_match_the_style(self):
        for title, ok in (("2007 Ford F-150", True), ("1991 Chevrolet S-10", True), ("2004 Silverado 2500", True),
                          ("2012 Honda Civic", False)):
            self.assertEqual(matching.check(title, PICKUP, [], 4000)[0], ok, title)
        self.assertFalse(matching.check("2007 Ford F-150", {**PICKUP, "kind": "item"}, [], 4000)[0])  # things: as typed

    def test_ebay_searches_the_models(self):
        seen = []
        with mock.patch.object(sources, "_ebay_access_token", lambda s: "t"), \
                mock.patch.object(sources, "_http", lambda url, **kw: seen.append(url) or {"itemSummaries": []}):
            sources.ebay(PICKUP, SETTINGS)
        q = urllib.parse.parse_qs(urllib.parse.urlparse(seen[0]).query)
        self.assertEqual(q["q"], ["(f-150,silverado,ram,tacoma,sierra,tundra,ranger,colorado)"])
        self.assertEqual((q["limit"], q["category_ids"]), (["200"], ["6001"]))

    def test_ebay_too_large_asks_for_less(self):
        seen = []

        def http(url, **kw):
            seen.append(urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["q"][0])
            if len(seen) < 3:
                raise sources.SourceError('HTTP 400 from api.ebay.com: {"errors":[{"errorId":12023}]}')
            return {"itemSummaries": []}
        with mock.patch.object(sources, "_ebay_access_token", lambda s: "t"), mock.patch.object(sources, "_http", http):
            sources.ebay(PICKUP, SETTINGS)
        self.assertEqual(seen, ["(f-150,silverado,ram,tacoma,sierra,tundra,ranger,colorado)",
                                "(f-150,silverado,ram,tacoma)", "pickup"])
        with mock.patch.object(sources, "_ebay_access_token", lambda s: "t"),                 mock.patch.object(sources, "_http", lambda url, **kw: (_ for _ in ()).throw(
                    sources.SourceError("HTTP 400: 12023"))):
            with self.assertRaises(sources.SourceError):  # still too large with just "pickup": say so
                sources.ebay(PICKUP, SETTINGS)

    @mock.patch.object(sources, "CRAIGSLIST_GAP_SECONDS", 0)
    def test_craigslist_type_filter(self):
        seen = []
        body = {"data": {"location": {"lat": 40.3, "lon": -111.7}, "decode": {"minPostingId": 1, "minPostedDate": 0,
                         "locationDescriptions": [0, "Orem"]},
                         "items": [[1, 1, 145, 4500, "1:1~40.30~-111.70", "x", [13, "u"], [6, "s"],
                                    "1950 Chevrolet 3/4 Ton"]]}}
        with mock.patch.object(sources.urllib.request, "urlopen",
                               lambda req, timeout: seen.append(req.full_url) or Resp(json.dumps(body))):
            items = sources.craigslist(PICKUP, SETTINGS)
        q = urllib.parse.parse_qs(urllib.parse.urlparse(seen[0]).query)
        self.assertEqual(q["auto_bodytype"], ["7", "9"])
        self.assertNotIn("query", q)  # "pickup" isn't searched as a word
        self.assertTrue(matching.check(items[0]["match_text"], PICKUP, [], 4500)[0])  # trusted: it's a truck


def ksl_page(rows, cursor="c1"):
    state = ('{"initialState":{"searchParams":{"body":"Truck","zip":"84057","miles":"50"},"filters":[]}}'
             '7:["$","SearchStoreProvider",null,{"initialState":{"results":' + json.dumps([rows])
             + ',"pageInfo":[{"hasNextPage":true,"endCursor":"' + cursor + '","total":229}]}}]')
    return ('<script src="https://marketplace-cdn.ksl.com/_next/static/chunks/abc.js"></script>'
            '<script>self.__next_f.push([1,' + json.dumps(state) + '])</script>')


def car(i, title):
    return {"id": i, "listingType": "CAR", "title": title, "price": 4000, "makeYear": 2001, "mileage": 150000}


@mock.patch.object(sources, "KSL_GAP_SECONDS", 0)
class KslPagesTest(unittest.TestCase):
    def setUp(self):
        sources._ksl_action["id"] = None

    def test_body_path_and_more_pages(self):
        calls = []

        def opener(req, timeout):
            calls.append((req.get_method(), req.full_url, req.headers.get("Next-action"), req.data))
            if req.full_url.endswith(".js"):
                return Resp('x=(0,a.createServerReference)("' + "ab" * 21 + '",b.callServer,void 0,c.findSourceMapURL,"search")')
            if req.get_method() == "POST":
                n = sum(1 for c in calls if c[0] == "POST")
                items = [car(100 + n, f"200{n} Ford F-250")]
                return Resp('0:{}\n1:' + json.dumps({"items": items, "pageInfo": {"hasNextPage": n < 2,
                                                                                    "endCursor": f"c{n + 1}"}}))
            return Resp(ksl_page([car(1, "2001 Ford F-150"), car(2, "1950 Chevrolet 3/4 Ton")]))
        with mock.patch.object(sources.urllib.request, "urlopen", opener):
            items = sources.ksl_cars(PICKUP, SETTINGS)
        self.assertIn("/search/body/Truck/priceFrom/3000/priceTo/5000/zip/84057/miles/50", calls[0][1])
        self.assertNotIn("keyword", calls[0][1])
        posts = [c for c in calls if c[0] == "POST"]
        self.assertEqual(len(posts), 2)  # stops when there's no next page
        self.assertEqual(posts[0][2], "ab" * 21)
        self.assertEqual(json.loads(posts[0][3])[3:], [{"body": "Truck", "zip": "84057", "miles": "50"}, "c1"])
        self.assertEqual([i["source_id"] for i in items], ["1", "2", "101", "102"])
        self.assertTrue(all(matching.check(i["match_text"], PICKUP, [], 4000)[0] for i in items))

    def test_page_one_still_counts_when_more_fails(self):
        def opener(req, timeout):
            if req.full_url.endswith(".js"):
                return Resp("no action here")
            return Resp(ksl_page([car(1, "2001 Ford F-150")]))
        with mock.patch.object(sources.urllib.request, "urlopen", opener):
            items = sources.ksl_cars(PICKUP, SETTINGS)
        self.assertEqual([i["source_id"] for i in items], ["1"])


if __name__ == "__main__":
    unittest.main()
