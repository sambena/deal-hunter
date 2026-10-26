"""Records the Python app's own answers for the rules ported to site/src/logic (matching, hardware, specs, vehicles,
and the Python behaviour they rest on), for test/logic.test.ts. Made-up inputs only; NHTSA's answers are made up
and nothing is fetched.

Run from the repository root:  python site/test/fixtures/make_logic.py > site/test/fixtures/logic-python.json
Each entry is {"in": <arguments>, "out": <result>} or {"in": ..., "error": <exception type>, "message": <text>}."""
import json
import shlex
import sys
import urllib.parse

import hardware
import matching
import specs
import vehicles


def run(fn, *args):
    try:
        return {"in": list(args), "out": fn(*args)}
    except Exception as e:  # noqa: BLE001 - recorded, so the port must fail the same way
        return {"in": list(args), "error": type(e).__name__, "message": str(e)}


W = {"query": "rtx 3080"}
TITLES = ["NVIDIA GeForce RTX 3080 FE", "i9-10980 XE 18-core", "RX 5700 XT 8GB", "Intel Core i9-10980XE",
          "4 x 32GB DDR4 3200", "vertx rtx-3080 Ti", "EVGA 3080 FTW3 Ultra", "", "Café-racer 3080", "rtx3080",
          "AMD Ryzen 9 5950X (for parts)", "Samsung 980 Pro 2TB NVMe", "rtx 3080 broken fan", "RTX ３０８０ fullwidth"]
QUERIES = ["rtx 3080", "9980xe|10980xe", '"ddr4 32gb"', "-ecc ddr4", "10980xe", "rtx -ti", 'unclosed "quote',
           "a|b|c d", "-", "--x", "'single quoted' x", 'say "a\\"b" c', "back\\ slash", "", "   ", "x|", "|y",
           'mid"dle word"s', "4x32gb", "5700x", "rx 5700 xt", "3080 -mining -lhr", "ram|memory 32gb"]

cases = {}
cases["normalize"] = [run(matching.normalize, t) for t in TITLES]
cases["compact"] = [run(matching.compact, t) for t in TITLES]
cases["parse_query"] = [run(matching.parse_query, q) for q in QUERIES]
cases["search_terms"] = [run(matching.search_terms, q) for q in QUERIES]
cases["check"] = [run(matching.check, t, {**W, "query": q}, ["for parts", "broken"], None, "") for q in QUERIES for t in TITLES]
cases["check"] += [
    run(matching.check, "RTX 3080 $400", {"query": "rtx 3080", "max_price": 450.0, "min_price": 100}, [], 400.0, ""),
    run(matching.check, "RTX 3080", {"query": "rtx 3080", "max_price": 450.0}, [], 450.01, ""),
    run(matching.check, "RTX 3080", {"query": "rtx 3080", "min_price": 100}, [], 99.99, ""),
    run(matching.check, "RTX 3080", {"query": "rtx 3080", "exclude": ["rtx"]}, [], None, ""),
    run(matching.check, "RTX 3080", {"query": "rtx 3080"}, ["as is"], None, "Sold as is, no returns"),
    run(matching.check, "RTX 3080 LHR", {"query": "rtx 3080 -lhr"}, [], None, ""),
    run(matching.check, "2018 Ford F-150 XLT", {"query": "ford pickup", "kind": "vehicle"}, ["as is"], None, "as is"),
    run(matching.check, "2018 Ford F-150 XLT", {"query": "pickup|suv ford", "kind": "vehicle"}, [], None, ""),
    run(matching.check, "2018 Ford Explorer", {"query": "ford|truck", "kind": "vehicle"}, [], None, ""),
    run(matching.check, "2005 Chevy Silverado parts only", {"query": "truck", "kind": "vehicle"}, [], None, ""),
    run(matching.check, "2012 Honda Odyssey", {"query": "minivan", "kind": "vehicle"}, [], None, ""),
    run(matching.check, "2012 Honda Civic", {"query": "sedan", "kind": "vehicle"}, [], None, ""),
    run(matching.check, "ISO: Tacoma", {"query": "tacoma", "kind": "vehicle"}, [], None, ""),
]
cases["still_wanted"] = [
    run(matching.still_wanted, "RTX 3080 LHR", 400.0, {"query": "rtx -lhr"}),
    run(matching.still_wanted, "RTX 3080", 400.0, {"query": "rtx", "max_price": 399.0}),
    run(matching.still_wanted, "RTX 3080", 400.0, {"query": "rtx", "min_price": 401}),
    run(matching.still_wanted, "RTX 3080", None, {"query": "rtx", "min_price": 401, "exclude": ["gtx"]}),
    run(matching.still_wanted, "RTX 3080 mining", 1.0, {"query": "zzz", "exclude": ["mining"]}),
]
cases["body_style"] = [run(matching.body_style, q) for q in
                       ["ford pickup", "truck", "SUV", "toyota", "pickup|f150 4x4", "sedan coupe", "-truck ford", ""]]
VEHICLE_TITLES = ["2005 chevy silverado 1500", "2018 Toyota Tacoma TRD", "2010 land rover range rover",
                  "2015 Mercedes-Benz C300", "2016 mercedes benz e350", "1999 VW Jetta", "2020 tesla model 3",
                  "2012 McLaren MP4", "2008 ford f-150", "2011 mini cooper", "2017 alfa romeo giulia",
                  "no year honda civic", "2019", "1949 ford", "2050 ford", "２０１８ honda", "2014 rolls-royce ghost",
                  "2003 am general hummer", "2001 kia rio", "2007 bmw 328i", "2013 GMC sierra", "2011 ram 1500"]
cases["vehicle_make"] = [run(matching.vehicle_make, t) for t in VEHICLE_TITLES]
cases["vehicle_year"] = [run(matching.vehicle_year, t) for t in VEHICLE_TITLES]
MILES = ["123,456 miles", "85k miles", "85 k mi", "85.5k miles", "120.5 miles", "5 miles from campus", "99 miles",
         "100 miles", "odometer 45000", "45000odometer", "1,234,567 miles", "mileage unknown", "12mi.", "12 mile",
         "75K MILES", "٣٤٥٠٠ miles", "3.2 miles", "1000.5 miles", "40,000 mi", "40000 mies"]
cases["vehicle_miles"] = [run(matching.vehicle_miles, t) for t in MILES]
cases["title_status"] = [run(matching.title_status, t) for t in
                         ["Salvage title", "REBUILT", "rebuild title", "branded title", "lemon law", "flood car",
                          "clean title", "", "rebuilt salvage"]]
cases["check_vehicle"] = [run(matching.check_vehicle, y, m, w) for y, m, w in [
    (None, None, {}), (2015, None, {"year_min": 2016}), (2021, 1000, {"year_max": 2020}),
    (2018, 120000, {"max_miles": 100000}), (2018, None, {"max_miles": 100000}), (2018, 50000, {"year_min": 0}),
    (2018, 50000, {"year_min": 2016, "year_max": 2020, "max_miles": 90000})]]
SIZES = ["10 1/2", "10½", "medium", "W32 L30", "32x30", "32/30", "w32l30", "M", "xl", "x-large", "2XL", "10.5M",
         "10.0", "10 US", "9W", "", None, "  extra   large ", "eu 42", "one size", "11.5 w", "size 7"]
cases["norm_size"] = [run(matching.norm_size, s) for s in SIZES]
SIZE_TITLES = ["Nike Air Max size 10.5", "Running shoes sz 10 1/2", "Boots US 9", "Jeans W32 L30", "Levi's 32x30",
               "Hoodie size M", "Sneakers 10.5M", "Women's 8 W heels", "Shirt XL", "No size here", "size: xxl tee",
               "Men's 11 men's", "uk 7.5 trainers", "Shoes 10½ US", "Jacket size large", "34 x 32 pants"]
cases["title_size"] = [run(matching.title_size, t) for t in SIZE_TITLES]
cases["check_size"] = [run(matching.check_size, s, w) for s, w in [
    ("10.5", {"size": "10 1/2"}), ("11", {"size": "10.5"}), ("", {"size": "M"}), ("M", {}), ("32x30", {"size": "W32 L30"}),
    (None, {"size": None}), ("medium", {"size": "M"})]]
HIST = [[400.0, 2018], [420.0, 2019], [390.0, 2017], [500.0, 2010], [380.0, 2020], [410.0, 2016], [450.0, None]]
cases["vehicle_history"] = [run(matching.vehicle_history, y, [tuple(h) for h in HIST]) for y in [2018, 2010, None, 2030]]
cases["vehicle_history"] += [run(matching.vehicle_history, 2018, [])]
PRICES = [[], [1, 2, 3, 4], [100, 200, 300, 400, 500], [100, 200, 300, 400, 500, 600], [0, None, -5, 100, 200, 300, 400],
          [1.0] + [400.0] * 9 + [9999.0], [399.99, 401.25, 405.5, 410.0, 389.0, 420.0, 999.0, 1.0, 400.0, 402.0, 404.0],
          [0.1, 0.2, 0.3, 0.4, 0.5]]
cases["typical_price"] = [run(matching.typical_price, p) for p in PRICES]
cases["deal_pct"] = [run(matching.deal_pct, t, p) for t in [None, 0.0, 300.0, 400.0, 450.0, 337.5, 1000.0] for p in PRICES]
cases["deal_pct"] += [run(matching.deal_pct, 320.0, [400.0] * 5), run(matching.deal_pct, 400.25, [400.0] * 5),
                      run(matching.deal_pct, 375.0, [400.0] * 5), run(matching.deal_pct, 0.0625, [1.0] * 5)]
cases["deal_label"] = [run(matching.deal_label, p) for p in [None, 0.25, 0.2499, 0.1, 0.0999, -0.5, 0.0, 1.0]]

CPUS = ["Intel(R) Core(TM) i7-7800X CPU @ 3.50GHz", "Intel Core i9-10980XE", "i9-10900K", "i5-11400", "i7-12700K",
        "i7-13700K", "i9-14900KS", "i7-15700", "i7-4790K", "i7-5775C", "i7-5960X", "i7-6950X", "i7-3930K", "i7-4960X",
        "i5-2500K", "i7-3770", "i7-6700K", "i7-7700", "i9-7980XE", "i9-9980XE", "i7-8700K", "i9-9900K",
        "Core Ultra 9 285K", "Core Ultra 7 265", "AMD Ryzen 7 3700X 8-Core Processor", "Ryzen 5 5600X", "R5 1600",
        "Ryzen 9 7950X3D", "Ryzen 7 9800X3D", "Ryzen 5 6600H", "Threadripper 3970X", "Xeon E5-2690 v4", "", "i3-1005G1",
        "Pentium G4560", "i7 4770k", "i7-4770 @ 3.4", "Ryzen ٥ ٥٦٠٠X"]
cases["cpu_socket"] = [run(hardware.cpu_socket, c) for c in CPUS]
BOARDS = ["ASUS P8P67 Pro", "Gigabyte B85M-D3H", "ASRock X299 Taichi", "MSI X99A SLI", "ASUS Z170-A", "MSI Z390-A Pro",
          "Gigabyte Z490 Aorus", "ASUS ROG Z690", "MSI Z890 Tomahawk", "ASUS B550-F", "Gigabyte X670E Aorus",
          "Dell 0XHGV1", "ASUS Prime H310M", "Gigabyte H97-D3H", "ASRock Z97 Extreme4", "Q370M", "W480 Pro", "Unknown board",
          "ASUS X99-A", "ASRock H310 Pro", "MSI Q170 Pro", "ASUS P8Z77-V", "Gigabyte H61M-S1", "Gigabyte B650 Aorus",
          "MSI B760 Gaming", "ASUS B860-F", "ASRock A520M", "MSI H81M", "Q85 board", "MSI Z87-G45"]
cases["detect_platform"] = [run(hardware.detect_platform, [{"category": "motherboard", "model": b}]) for b in BOARDS]
cases["detect_platform"] += [run(hardware.detect_platform, [{"category": "cpu", "model": c}]) for c in CPUS]
cases["detect_platform"] += [run(hardware.detect_platform, []),
                             run(hardware.detect_platform, [{"category": "cpu", "model": "i7-4790K"},
                                                            {"category": "motherboard", "model": "ASUS Z97-A"}])]
MACHINES = [{"name": "Desk", "parts": [{"category": "cpu", "model": c}]} for c in CPUS] + [
    {"name": "Old", "parts": [{"category": "cpu", "model": "i7-4770K"}, {"category": "motherboard", "model": "ASUS Z87-A"}]},
    {"name": "Old 97", "parts": [{"category": "cpu", "model": "i7-4770K"}, {"category": "motherboard", "model": "ASUS Z97-A"}]},
    {"name": "Twelve", "parts": [{"category": "motherboard", "model": "MSI Z690 Pro"}]},
    {"name": "Server", "parts": [{"category": "motherboard", "model": "Supermicro X11"}]},
    {"name": "Top", "parts": [{"category": "cpu", "model": "i9-10980XE"}, {"category": "motherboard", "model": "X299"}]},
    {"name": "Empty", "parts": []}, {"name": "Nothing"}]
cases["suggest"] = [run(hardware.suggest, m) for m in MACHINES]

REPORTS = [
    "cpu: Intel(R) Core(TM) i5-7500 CPU @ 3.40GHz\nboard: Micro-Star International Co., Ltd. B250M PRO-VDH (MS-7A70)\n"
    "system: Micro-Star International Co., Ltd. MS-7A70\nram: 8GB DDR4 2400 CMK16GX4M2A2400C14\n"
    "ram: 8GB DDR4 2400 CMK16GX4M2A2400C14\ngpu: NVIDIA GeForce GTX 1060 6GB\ngpu: Microsoft Basic Display Adapter\n"
    "disk: 466GB Samsung SSD 860 EVO 500GB SSD\ndisk: 1863GB ST2000DM008-2FR102 HDD\ndisk: 932GB WDC WD10EZEX 3\n",
    "cpu: AMD Ryzen 7 3700X 8-Core Processor\nboard: ASUSTeK COMPUTER INC. TUF GAMING X570-PLUS\n"
    "system: System manufacturer System Product Name\nram: 16GB DDR4 3200 F4-3200C16-16GVK\nram: 8GB DDR4 3200 X\n"
    "gpu: NVIDIA TU116 [GeForce GTX 1660 SUPER] (rev a1)\ngpu: AMD [AMD/ATI] Navi 48 [Radeon RX 9070 XT]\n"
    "gpu: NVIDIA TU116 [GeForce GTX 1660 SUPER] (rev a1)\ndisk: 1.8T Samsung SSD 970\ndisk: 931.5G WDC\r\n",
    "cpu: 12th Gen Intel(R) Core(TM) i7-12700K\nboard: Dell Inc. 0XHGV1\nsystem: Dell Inc. OptiPlex 7090\n"
    "ram: 31GB total\ngpu: Intel Corporation UHD Graphics 770\ndisk: 512G KXG60ZNV512G Unspecified\n",
    "CPU : Intel Core i3\nBOARD: To be filled by O.E.M. To be filled by O.E.M.\nsystem: To Be Filled By O.E.M.\n",
    "board: Gigabyte Technology Co., Ltd. Z390 AORUS PRO\nsystem: Gigabyte Technology Co., Ltd. Z390 AORUS PRO\n",
    "cpu: AMD Ryzen 5 5600X 6-Core Processor             \nram: 16GB 3600 X\nram: 16GB  3600 X\ngpu: Parsec Virtual Display\n"
    "gpu: Meta Quest Virtual Monitor\ndisk: 2T Seagate\n",
    "notes from a receipt: bought a PC", "", "cpu:\nboard:   \ncpu: second\nram: 4GB DDR3 1600 A\nram: 8GB DDR3 1600 B\n",
    "cpu: Intel Core i7 CPU 920 board: ASRock X58 Extreme ram: 6.5 GB DDR3\x85gpu: x",
]
cases["looks_like_report"] = [run(specs.looks_like_report, r) for r in REPORTS]
cases["parse_report"] = [run(specs.parse_report, r) for r in REPORTS]
cases["tidy_cpu"] = [run(specs.tidy_cpu, c) for c in CPUS + ["Intel(R) Xeon(R) CPU E5-2690 v4 @ 2.60GHz", "AMD EPYC 7302 16-Core Processor",
                                                            "  Intel® Core™ i7 CPU  ", "13th gen Intel Core i5-13600K Processor"]]
cases["merge_parts"] = [run(specs.merge_parts, [{"category": "cpu", "model": "old"}, {"category": "network", "model": "nic"}],
                            [{"category": "cpu", "model": "new"}, {"category": "ram", "model": "16GB"}]),
                        run(specs.merge_parts, [], []), run(specs.merge_parts, [{"model": "no category"}], [{"category": "gpu", "model": "g"}])]
cases["commands"] = [{"in": [], "out": [specs.WINDOWS_COMMAND, specs.LINUX_COMMAND]}]

cases["clean_vin"] = [run(vehicles.clean_vin, v) for v in
                      ["1abcd23efgh456789", "1ABCD-23EFG H456789", "1ABCD23EFGH45678", "1ABCD23EFGH45678O", "", None,
                       "1abcd23efgh456789\n", "1ABCD23EFGH4567899"]]

DECODES = [
    {"Make": "TOYOTA", "Model": "Tacoma", "ModelYear": "2018", "Trim": "TRD", "BodyClass": "Pickup", "DriveType": "4WD",
     "DisplacementL": "3.5", "EngineCylinders": "6", "FuelTypePrimary": "Gasoline", "ErrorCode": "0", "ErrorText": "0 - clean"},
    {"Make": "MERCEDES-BENZ", "Model": "C300", "ModelYear": "", "DisplacementL": "2.25", "EngineCylinders": "",
     "FuelTypePrimary": "Diesel", "ErrorCode": "1,11", "ErrorText": "1 - Check digit incorrect"},
    {"Make": "ford", "Model": None, "ModelYear": "2008", "DisplacementL": "4.6.1", "FuelTypePrimary": None},
    {"Make": "", "Model": "x"},
    {"Make": "TESLA", "Model": "Model 3", "ModelYear": "2020", "DisplacementL": "", "FuelTypePrimary": "Electric",
     "ErrorCode": None, "ErrorText": None},
    {"Make": "HONDA", "DisplacementL": "2.35", "ModelYear": 2019, "EngineCylinders": 4},
]


def decode(vin, result):
    vehicles._get = lambda url: {"Results": [result]} if result is not None else {}
    out = vehicles.decode_vin(vin)
    return out


cases["decode_vin"] = [run(decode, "1abcd23efgh456789", r) for r in DECODES] + [run(decode, "1ABCD23EFGH45678", DECODES[0]),
                                                                                 run(decode, "1abcd23efgh456789", None)]
cases["decode_vin_url"] = []
seen = []
vehicles._get = lambda url: seen.append(url) or {"Results": [DECODES[0]]}
vehicles.decode_vin(" 1abcd-23efgh456789 ")
cases["decode_vin_url"].append({"in": [" 1abcd-23efgh456789 "], "out": seen[-1]})

RECALL = {"NHTSACampaignNumber": "18V123000", "ReportReceivedDate": "01/02/2018", "Component": "AIR BAGS",
          "Summary": "Made-up summary.", "Consequence": "Made-up consequence.", "Remedy": "Made-up remedy.", "parkIt": True}


def recalls(make, model, year, answers):
    """answers: {model asked: list of results, or "fail" for an HTTP error}. Records which URLs were asked."""
    vehicles._recall_cache.clear()
    asked = []

    def get(url):
        asked.append(url)
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        found = answers.get(q["model"][0])
        if found == "fail":
            raise vehicles.LookupFailed("NHTSA answered HTTP 400")
        return {"results": found} if found is not None else {}
    vehicles._get = get
    try:
        return {"recalls": vehicles.recalls(make, model, year, now=1000.0), "asked": asked}
    except vehicles.LookupFailed as e:
        return {"error": str(e), "asked": asked}


cases["recalls"] = [run(recalls, *args) for args in [
    ("Toyota", "Tacoma", 2018, {"Tacoma": [RECALL, {"NHTSACampaignNumber": "19V/1 2", "parkIt": "false"}]}),
    ("Toyota", "Tacoma TRD Off-Road", 2018, {"Tacoma TRD Off-Road": "fail", "Tacoma": [RECALL]}),
    ("Toyota", "Tacoma TRD", 2018, {"Tacoma TRD": [], "Tacoma": []}),
    ("Toyota", "Tacomaa", 2018, {"Tacomaa": "fail"}),
    ("Toyota", "Tacoma TRD", 2018, {"Tacoma TRD": "fail", "Tacoma": "fail"}),
    ("Land Rover", "Range Rover Sport", 2010.0, {"Range Rover Sport": [{"Summary": None, "parkIt": None, "NHTSACampaignNumber": "10V~1*"}]}),
    ("", "Tacoma", 2018, {}), ("Toyota", "Tacoma", None, {}), ("Toyota", "Tacoma", 0, {}),
    ("Ford", "F-150 & co", 2008, {"F-150 & co": []}),
]]

FLOATS = [0.0625, -0.0625, 0.0005, 0.0015, 0.0025, 2.675, 1.0005, -0.0001, 0.12345, 1e-9, 0.1 + 0.2, 2.25, 2.35, 3.45, 0.05, -2.5]
cases["round3"] = [run(round, x, 3) for x in FLOATS]
cases["fixed1"] = [run(lambda x: f"{x:.1f}", x) for x in FLOATS]
SHLEX = QUERIES + ["a\\", '"a\\', "'a", 'x "a\\\\b" y', 'x "a\\$b"', "a\\ b\tc\nd", '""', "''", 'a""b', "é \"ü ö\""]
cases["shlex"] = [run(shlex.split, s) for s in SHLEX]
TEXT = ["land rover", "o'neil mcdonald", "3xl", "école MIXÉ", "a-b c_d", "ßtraße", "", "ǅungla"]
cases["title"] = [run(str.title, s) for s in TEXT]
cases["capitalize"] = [run(str.capitalize, s) for s in TEXT]
cases["splitlines"] = [run(str.splitlines, s) for s in ["a\nb", "a\r\nb\r", "a\n\nb\n", "", "\n", "a\x0bb\x0cc\x1cd\x85e f g", "a\rb\n\n"]]
cases["quote"] = [run(urllib.parse.quote, s) for s in ["18V123000", "19V/1 2", "a~b*c(d)'e!", "é & ü", ""]]
cases["urlencode"] = [run(urllib.parse.urlencode, {"make": m, "model": "F-150 & co/x~y*", "modelYear": 2008}) for m in ["Land Rover", "é"]]

json.dump(cases, sys.stdout, ensure_ascii=False, indent=0)
sys.stdout.write("\n")
