"""Built-in upgrade rules: work out a machine's platform from its parts and
suggest drop-in CPU and RAM upgrades. Used when AI is off.

It only knows desktop platforms from roughly 2015 on. Anything it can't place
(laptops, mini PCs, GPUs) is left to AI mode or manual watches.
"""

from __future__ import annotations

import re

RAM_EXCLUDES = ["ecc", "rdimm", "lrdimm", "registered", "server", "sodimm", "so dimm", "laptop"]

# Best upgrade first. (display name, query, note)
PLATFORMS: dict[str, dict] = {
    "LGA2066": {
        "label": "Intel X299 (LGA2066)",
        "ram": "DDR4", "channels": 4,
        "cpus": [
            ("i9-10980XE", "10980xe", "Needs a BIOS with Cascade Lake-X support"),
            ("i9-10940X", "10940x", "Needs a BIOS with Cascade Lake-X support"),
            ("i9-9980XE", "9980xe", "Needs a BIOS with 9th-gen X support"),
            ("i9-10920X", "10920x", "Needs a BIOS with Cascade Lake-X support"),
            ("i9-9960X", "9960x", "Needs a BIOS with 9th-gen X support"),
            ("i9-10900X", "10900x", "Needs a BIOS with Cascade Lake-X support"),
            ("i9-7980XE", "7980xe", ""),
            ("i9-7960X", "7960x", ""),
            ("i9-9940X", "9940x", "Needs a BIOS with 9th-gen X support"),
            ("i9-7940X", "7940x", ""),
            ("i9-7920X", "7920x", ""),
            ("i9-7900X", "7900x", ""),
        ],
        "cpu_exclude": ["xeon", "es", "qs"],
    },
    "LGA2011-3": {
        "label": "Intel X99 (LGA2011-3)",
        "ram": "DDR4", "channels": 4,
        "cpus": [
            ("i7-6950X", "6950x", "Needs a BIOS with Broadwell-E support"),
            ("i7-6900K", "6900k", "Needs a BIOS with Broadwell-E support"),
            ("i7-5960X", "5960x", ""),
            ("i7-6850K", "6850k", "Needs a BIOS with Broadwell-E support"),
        ],
        "cpu_exclude": ["es", "qs"],
    },
    "LGA1151": {
        "label": "Intel 100/200-series (LGA1151)",
        "ram": "DDR4", "channels": 2,
        "cpus": [
            ("i7-7700K", "7700k", "100-series boards need a BIOS update for 7th gen"),
            ("i7-6700K", "6700k", ""),
            ("i7-7700", "7700 -7700k -7700hq", "100-series boards need a BIOS update for 7th gen"),
        ],
        "cpu_exclude": [],
    },
    "LGA1151v2": {
        "label": "Intel 300-series (LGA1151 v2)",
        "ram": "DDR4", "channels": 2,
        "cpus": [
            ("i9-9900KS", "9900ks", "Z370/B360/H370 need a BIOS update for 9th gen"),
            ("i9-9900K", "9900k -9900ks", "Z370/B360/H370 need a BIOS update for 9th gen"),
            ("i9-9900", "9900 -9900k -9900ks -9900kf -9900x", "Z370/B360/H370 need a BIOS update for 9th gen"),
            ("i7-9700K", "9700k", "Z370/B360/H370 need a BIOS update for 9th gen"),
            ("i7-8700K", "8700k", ""),
        ],
        "cpu_exclude": [],
    },
    "LGA1200": {
        "label": "Intel 400/500-series (LGA1200)",
        "ram": "DDR4", "channels": 2,
        "cpus": [
            ("i9-11900K", "11900k", "11th gen only works on Z490/H470 (with BIOS) and 500-series, not B460/H410"),
            ("i9-10900K", "10900k", ""),
            ("i9-10850K", "10850k", ""),
            ("i7-11700K", "11700k", "11th gen only works on Z490/H470 (with BIOS) and 500-series, not B460/H410"),
            ("i7-10700K", "10700k", ""),
        ],
        "cpu_exclude": [],
    },
    "LGA1700": {
        "label": "Intel 600/700-series (LGA1700)",
        "ram": "DDR4 or DDR5 (check your board)", "channels": 2,
        "cpus": [
            ("i9-14900K", "14900k", "600-series boards need a BIOS update. Check 13th/14th-gen i7/i9 microcode (stability issue)"),
            ("i7-14700K", "14700k", "600-series boards need a BIOS update. Check 13th/14th-gen i7/i9 microcode (stability issue)"),
            ("i9-13900K", "13900k", "600-series boards need a BIOS update. Check 13th/14th-gen i7/i9 microcode (stability issue)"),
            ("i7-13700K", "13700k", "600-series boards need a BIOS update. Check 13th/14th-gen i7/i9 microcode (stability issue)"),
            ("i5-14600K", "14600k", "600-series boards need a BIOS update"),
            ("i5-13600K", "13600k", "600-series boards need a BIOS update"),
        ],
        "cpu_exclude": [],
    },
    "LGA1851": {
        "label": "Intel 800-series (LGA1851)",
        "ram": "DDR5", "channels": 2,
        "cpus": [
            ("Core Ultra 9 285K", "285k", ""),
            ("Core Ultra 7 265K", "265k", ""),
            ("Core Ultra 5 245K", "245k", ""),
        ],
        "cpu_exclude": ["laptop"],
    },
    "AM4": {
        "label": "AMD AM4",
        "ram": "DDR4", "channels": 2,
        "cpus": [
            ("Ryzen 7 5800X3D", "5800x3d", "300/400-series boards need a BIOS with Ryzen 5000 support"),
            ("Ryzen 9 5950X", "5950x", "300/400-series boards need a BIOS with Ryzen 5000 support"),
            ("Ryzen 9 5900X", "5900x", "300/400-series boards need a BIOS with Ryzen 5000 support"),
            ("Ryzen 7 5700X3D", "5700x3d", "300/400-series boards need a BIOS with Ryzen 5000 support"),
            ("Ryzen 7 5700X", "5700x -5700x3d", "300/400-series boards need a BIOS with Ryzen 5000 support"),
        ],
        "cpu_exclude": [],
    },
    "AM5": {
        "label": "AMD AM5",
        "ram": "DDR5", "channels": 2,
        "cpus": [
            ("Ryzen 9 9950X3D", "9950x3d", "Needs a BIOS with Ryzen 9000 support"),
            ("Ryzen 7 9800X3D", "9800x3d", "Needs a BIOS with Ryzen 9000 support"),
            ("Ryzen 9 9950X", "9950x -9950x3d", "Needs a BIOS with Ryzen 9000 support"),
            ("Ryzen 7 7800X3D", "7800x3d", ""),
            ("Ryzen 9 7950X3D", "7950x3d", ""),
            ("Ryzen 9 9900X", "9900x -9900x3d", "Needs a BIOS with Ryzen 9000 support"),
        ],
        "cpu_exclude": [],
    },
}

CHIPSETS = [
    (r"\bx299\b", "LGA2066"),
    (r"\bx99\b", "LGA2011-3"),
    (r"\b[zbh](?:170|270|250|150|110)\b|\bq[12]70\b", "LGA1151"),
    (r"\b[zbh](?:370|390|360|365|310|370)\b|\bq370\b", "LGA1151v2"),
    (r"\b[zbhw](?:490|590|460|560|470|570|410|510|480)\b", "LGA1200"),
    (r"\b[zbh](?:690|790|660|760|670|770|610)\b", "LGA1700"),
    (r"\b[zbh](?:890|860|810)\b", "LGA1851"),
    (r"\b[abx](?:320|350|370|450|470|520|550|570)\b", "AM4"),
    (r"\b[abx](?:620|650|670|850|870)e?\b", "AM5"),
]


def cpu_socket(model: str) -> str | None:
    # Raw names ("Intel(R) Core(TM) i7-7800X CPU @ 3.50GHz") would glue "cpu" onto the suffix and hide the X.
    t = re.sub(r"\((?:r|tm)\)|\bcpu\b|@.*$|\bprocessor\b", "", model.lower())
    t = t.replace(" ", "").replace("-", "")
    m = re.search(r"i[3579](\d{4,5})([a-z]*)", t)
    if m:
        num, suffix = m.group(1), m.group(2)
        hedt = suffix in ("x", "xe")
        if len(num) == 5:
            gen = int(num[:2])
            if gen == 10:
                return "LGA2066" if hedt else "LGA1200"
            if gen == 11:
                return "LGA1200"
            if gen in (12, 13, 14):
                return "LGA1700"
            return None
        gen = int(num[0])
        if gen in (5, 6) and suffix in ("k", "x") and num[1] in "89":
            return "LGA2011-3"
        if gen in (7, 9) and hedt:
            return "LGA2066"
        if gen in (6, 7):
            return "LGA1151"
        if gen in (8, 9):
            return "LGA1151v2"
        return None
    m = re.search(r"ultra[579](2\d{2})[a-z]*", t)
    if m:
        return "LGA1851"
    m = re.search(r"(?:ryzen[3579]?|r[3579])(\d)(\d{3})([a-z0-9]*)", t)
    if m:
        series = int(m.group(1))
        if series in (1, 2, 3, 4, 5):
            return "AM4"
        if series in (7, 8, 9):
            return "AM5"
    return None


def detect_platform(parts: list[dict]) -> tuple[str | None, str]:
    """Return (platform key, how it was worked out)."""
    for p in parts:
        if p.get("category") == "motherboard":
            text = p.get("model", "").lower()
            for pattern, key in CHIPSETS:
                if re.search(pattern, text):
                    return key, f"from motherboard '{p['model']}'"
    for p in parts:
        if p.get("category") == "cpu":
            key = cpu_socket(p.get("model", ""))
            if key:
                return key, f"from CPU '{p['model']}'"
    return None, "couldn't recognise the motherboard chipset or CPU"


def _cpu_rank(platform: dict, parts: list[dict]) -> int:
    """Index of the machine's current CPU in the upgrade list (len if not found)."""
    current = " ".join(p.get("model", "") for p in parts if p.get("category") == "cpu").lower().replace("-", "")
    current = current.replace(" ", "")
    for i, (_, query, _) in enumerate(platform["cpus"]):
        key = query.split()[0]
        if key in current:
            return i
    return len(platform["cpus"])


def suggest(machine: dict) -> dict:
    parts = machine.get("parts", [])
    key, how = detect_platform(parts)
    if not key:
        return {"platform": None, "explanation": how, "suggestions": []}
    plat = PLATFORMS[key]
    suggestions = []
    for name, query, note in plat["cpus"][:_cpu_rank(plat, parts)]:
        suggestions.append({
            "name": f"{name} for {machine['name']}",
            "category": "cpu",
            "query": query,
            "exclude": list(plat["cpu_exclude"]),
            "max_price": None,
            "reason": note or f"Drop-in for {plat['label']}",
        })
    ram = plat["ram"]
    if ram in ("DDR4", "DDR5"):
        kits = ["4x32gb|128gb", "4x16gb|64gb"] if plat["channels"] == 4 else ["2x32gb|64gb", "2x16gb|32gb"]
        for kit in kits:
            suggestions.append({
                "name": f"{ram} {kit.split('|')[0].upper()} kit for {machine['name']}",
                "category": "ram",
                "query": f"{ram.lower()} {kit}",
                "exclude": list(RAM_EXCLUDES),
                "max_price": None,
                "reason": f"{plat['channels']}-channel {ram} desktop DIMMs. Check your board's max capacity and QVL.",
            })
    else:
        suggestions.append({
            "name": f"RAM for {machine['name']}", "category": "ram", "query": "", "exclude": list(RAM_EXCLUDES),
            "max_price": None, "reason": f"This platform can be {ram}; add the RAM watch yourself once you know which.",
        })
    return {"platform": plat["label"], "explanation": how, "suggestions": suggestions}
