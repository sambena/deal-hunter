"""Filling in a computer's parts from a one-line command the user runs on it.

The commands print plain "key: value" lines (cpu, board, system, ram, gpu, disk) and need no install
or admin rights. The user pastes the output into Deal Hunter; nothing connects back. Text in any
other shape (System Information, a receipt, notes) goes to AI instead (see ai.specs_prompt).
"""

from __future__ import annotations

import re
from collections import Counter

# Windows PowerShell (5.1+). One line, so it can be pasted straight into a PowerShell window.
WINDOWS_COMMAND = (
    "$c=Get-CimInstance Win32_Processor|Select -First 1;$b=Get-CimInstance Win32_BaseBoard;"
    "$s=Get-CimInstance Win32_ComputerSystem;"
    "\"cpu: $($c.Name.Trim())\";\"board: $($b.Manufacturer) $($b.Product)\";\"system: $($s.Manufacturer) $($s.Model)\";"
    "$t=@{20='DDR';21='DDR2';24='DDR3';26='DDR4';34='DDR5'};"
    "Get-CimInstance Win32_PhysicalMemory|%{\"ram: $($_.Capacity/1GB)GB $($t[[int]$_.SMBIOSMemoryType]) "
    "$($_.ConfiguredClockSpeed) $($_.PartNumber.Trim())\"};"
    "Get-CimInstance Win32_VideoController|%{\"gpu: $($_.Name)\"};"
    "Get-PhysicalDisk|%{\"disk: $([math]::Round($_.Size/1GB))GB $($_.FriendlyName) $($_.MediaType)\"}"
)

# Linux, including Bazzite/SteamOS. Plain sh; no root, so RAM is the total only.
LINUX_COMMAND = (
    "sh -c 'd=/sys/class/dmi/id; "
    "echo \"cpu: $(grep -m1 \"model name\" /proc/cpuinfo | cut -d: -f2 | sed \"s/^ //\")\"; "
    "echo \"board: $(cat $d/board_vendor 2>/dev/null) $(cat $d/board_name 2>/dev/null)\"; "
    "echo \"system: $(cat $d/sys_vendor 2>/dev/null) $(cat $d/product_name 2>/dev/null)\"; "
    "echo \"ram: $(free -g | awk \"/Mem:/{print \\$2+1}\")GB total\"; "
    "lspci 2>/dev/null | grep -iE \"vga|3d controller|display controller\" | sed -E \"s/^[^:]*:[^:]*: /gpu: /\"; "
    "lsblk -dn -o NAME,TYPE,SIZE,MODEL | awk \"\\$2==\\\"disk\\\" && \\$1 !~ /^(zram|loop)/ "
    "{\\$1=\\$2=\\\"\\\"; sub(/^ +/,\\\"\\\"); print \\\"disk: \\\" \\$0}\"'"
)

KEYS = ("cpu", "board", "system", "ram", "gpu", "disk")
LINE = re.compile(r"^\s*(cpu|board|system|ram|gpu|disk)\s*:\s*(.*?)\s*$", re.I)

VENDORS = [  # long OEM names -> what people call them
    (r"micro-star int(?:ernationa)?'?l co\.?,? ltd\.?", "MSI"), (r"asustek computer inc\.?", "ASUS"),
    (r"gigabyte technology co\.?,? ltd\.?", "Gigabyte"), (r"asrock.*", "ASRock"), (r"dell inc\.?", "Dell"),
    (r"hewlett-packard|hp inc\.?", "HP"), (r"lenovo", "Lenovo"), (r"intel corporation", "Intel"),
    (r"advanced micro devices,? inc\.?", "AMD"), (r"nvidia corporation", "NVIDIA"),
]
PLACEHOLDER = re.compile(r"to be filled|system product name|default string|not applicable|^o\.?e\.?m\.?$", re.I)
VIRTUAL_GPU = re.compile(r"virtual|basic display|basic render|remote|parsec|idd|mirror|meta quest", re.I)


def tidy_cpu(name: str) -> str:
    """"Intel(R) Core(TM) i5-7500 CPU @ 3.40GHz" -> "Intel Core i5-7500"; "AMD Ryzen 7 3700X 8-Core Processor" ->
    "AMD Ryzen 7 3700X"."""
    name = re.sub(r"\((?:r|tm)\)|®|™|^\s*\d+(st|nd|rd|th) gen\s+", "", name, flags=re.I)  # "12th Gen Intel..."
    name = re.sub(r"\s*@.*$|\s+\d+-core processor$|\s+processor$|\s+cpu$", "", name.strip(), flags=re.I)
    return re.sub(r"\s+", " ", re.sub(r"\bcpu\b", "", name, flags=re.I)).strip()


def _vendor(text: str) -> str:
    for pattern, short in VENDORS:
        text = re.sub(rf"^{pattern}(?=\s|$)", short, text.strip(), flags=re.I)
    return re.sub(r"\s+", " ", text).strip()


def looks_like_report(text: str) -> bool:
    found = {m.group(1).lower() for line in text.splitlines() if (m := LINE.match(line))}
    return "cpu" in found and len(found) >= 2


def _board(board: str, system: str) -> str:
    board, system = _vendor(board), _vendor(system)
    name = board.split(" ", 1)[1] if " " in board else board
    # OEM machines report a part code as the board (Dell "0XHGV1"); the system name is more useful.
    if system and not PLACEHOLDER.search(system) and (re.fullmatch(r"[0-9A-Z]{5,8}", name) or not board):
        return f"{system} (board {name})" if name else system
    return "" if PLACEHOLDER.search(board) else board


def _ram(lines: list[str]) -> str:
    sticks, total = [], None
    for line in lines:
        m = re.match(r"(\d+(?:\.\d+)?)\s*GB\s*(total)?\s*(DDR\d)?\s*(\d{3,5})?\s*(.*)$", line, re.I)
        if not m:
            continue
        if m.group(2):
            total = f"{int(float(m.group(1)))}GB"
            continue
        sticks.append((int(float(m.group(1))), (m.group(3) or "").upper(), m.group(4) or "", m.group(5).strip()))
    if not sticks:
        return total or ""
    size = sum(s[0] for s in sticks)
    counts = Counter(s[0] for s in sticks)
    layout = " + ".join(f"{n}x{gb}GB" for gb, n in sorted(counts.items(), reverse=True))
    kind = sticks[0][1]
    speed = sticks[0][2]
    part = sticks[0][3] if len({s[3] for s in sticks}) == 1 else ""
    desc = f"{size}GB ({layout})"
    if kind:
        desc += f" {kind}" + (f"-{speed}" if speed else "")
    elif speed:
        desc += f" {speed} MT/s"
    return f"{desc} {part}".strip()


def _gpu(name: str) -> str:
    name = _vendor(re.sub(r"\s*\(rev \w+\)$", "", name))
    # lspci: "NVIDIA TU116 [GeForce GTX 1660 SUPER]", "AMD [AMD/ATI] Navi 48 [Radeon RX 9070 XT]":
    # the last bracket is the marketing name.
    brackets = re.findall(r"\[([^\]]+)\]", name)
    return f"{name.split(' ')[0]} {brackets[-1]}" if brackets else name


def parse_report(text: str) -> list[dict]:
    """Parts from the commands' output: [{"category", "model"}], in the order Deal Hunter shows them."""
    found: dict[str, list[str]] = {k: [] for k in KEYS}
    for line in text.splitlines():
        m = LINE.match(line)
        if m and m.group(2):
            found[m.group(1).lower()].append(m.group(2))
    parts = []
    if found["cpu"]:
        parts.append({"category": "cpu", "model": tidy_cpu(found["cpu"][0])})
    board = _board(found["board"][0] if found["board"] else "", found["system"][0] if found["system"] else "")
    if board:
        parts.append({"category": "motherboard", "model": board})
    ram = _ram(found["ram"])
    if ram:
        parts.append({"category": "ram", "model": ram})
    gpus = [_gpu(g) for g in found["gpu"] if not VIRTUAL_GPU.search(g)]
    for g in dict.fromkeys(gpus):  # same card listed twice -> once
        parts.append({"category": "gpu", "model": g})
    media = {"ssd": " SSD", "4": " SSD", "hdd": " HDD", "3": " HDD", "unspecified": "", "0": ""}
    for disk in found["disk"]:
        # Windows ends each disk with its media type (a word, or a number on older PowerShell).
        m = re.match(r"(.*?)\s+(SSD|HDD|Unspecified|[034])$", disk.strip(), re.I)
        disk = (m.group(1) + media[m.group(2).lower()]) if m else disk.strip()
        disk = re.sub(r"^(\d+(?:\.\d+)?)([GT])\b", r"\1\2B", disk)  # lsblk "1.8T" -> "1.8TB"
        parts.append({"category": "storage", "model": disk})
    return parts


def merge_parts(existing: list[dict], new: list[dict]) -> list[dict]:
    """New parts replace existing parts of the same category; other categories (network...) are kept."""
    replaced = {p["category"] for p in new}
    return [*new, *(p for p in existing if p.get("category") not in replaced)]
