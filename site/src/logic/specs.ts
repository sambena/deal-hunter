// Filling in a computer's parts from a one-line command the person runs on it: a port of the Python app's specs.py,
// rule for rule (test/logic.test.ts checks it against Python).
//
// The commands print plain "key: value" lines (cpu, board, system, ram, gpu, disk) and need no install or admin
// rights. The person pastes the output into Deal Hunter; nothing connects back. Text in any other shape (System
// Information, a receipt, notes) goes to AI instead.
import { asciiDigits, pyRe, pyStrip, splitlines } from './py.js';
import type { Part } from './hardware.js';

// Windows PowerShell (5.1+). One line, so it can be pasted straight into a PowerShell window.
export const WINDOWS_COMMAND =
  "$c=Get-CimInstance Win32_Processor|Select -First 1;$b=Get-CimInstance Win32_BaseBoard;$s=Get-CimInstance Win32_ComputerSystem;\"cpu: $($c.Name.Trim())\";\"board: $($b.Manufacturer) $($b.Product)\";\"system: $($s.Manufacturer) $($s.Model)\";$t=@{20='DDR';21='DDR2';24='DDR3';26='DDR4';34='DDR5'};Get-CimInstance Win32_PhysicalMemory|%{\"ram: $($_.Capacity/1GB)GB $($t[[int]$_.SMBIOSMemoryType]) $($_.ConfiguredClockSpeed) $($_.PartNumber.Trim())\"};Get-CimInstance Win32_VideoController|%{\"gpu: $($_.Name)\"};Get-PhysicalDisk|%{\"disk: $([math]::Round($_.Size/1GB))GB $($_.FriendlyName) $($_.MediaType)\"}";

// Linux, SteamOS-style systems included. Plain sh; no root, so RAM is the total only.
export const LINUX_COMMAND =
  "sh -c 'd=/sys/class/dmi/id; echo \"cpu: $(grep -m1 \"model name\" /proc/cpuinfo | cut -d: -f2 | sed \"s/^ //\")\"; echo \"board: $(cat $d/board_vendor 2>/dev/null) $(cat $d/board_name 2>/dev/null)\"; echo \"system: $(cat $d/sys_vendor 2>/dev/null) $(cat $d/product_name 2>/dev/null)\"; echo \"ram: $(free -g | awk \"/Mem:/{print \\$2+1}\")GB total\"; lspci 2>/dev/null | grep -iE \"vga|3d controller|display controller\" | sed -E \"s/^[^:]*:[^:]*: /gpu: /\"; lsblk -dn -o NAME,TYPE,SIZE,MODEL | awk \"\\$2==\\\"disk\\\" && \\$1 !~ /^(zram|loop)/ {\\$1=\\$2=\\\"\\\"; sub(/^ +/,\\\"\\\"); print \\\"disk: \\\" \\$0}\"'";

const KEYS = ['cpu', 'board', 'system', 'ram', 'gpu', 'disk'] as const;
const LINE = pyRe('^\\s*(cpu|board|system|ram|gpu|disk)\\s*:\\s*(.*?)\\s*$', 'i');

const VENDORS: [RegExp, string][] = ([   // long OEM names -> what people call them
  ["micro-star int(?:ernationa)?'?l co\\.?,? ltd\\.?", 'MSI'], ['asustek computer inc\\.?', 'ASUS'],
  ['gigabyte technology co\\.?,? ltd\\.?', 'Gigabyte'], ['asrock.*', 'ASRock'], ['dell inc\\.?', 'Dell'],
  ['hewlett-packard|hp inc\\.?', 'HP'], ['lenovo', 'Lenovo'], ['intel corporation', 'Intel'],
  ['advanced micro devices,? inc\\.?', 'AMD'], ['nvidia corporation', 'NVIDIA'],
] as [string, string][]).map(([p, short]) => [pyRe(`^${p}(?=\\s|$)`, 'i'), short]);
const PLACEHOLDER = pyRe('to be filled|system product name|default string|not applicable|^o\\.?e\\.?m\\.?$', 'i');
const VIRTUAL_GPU = pyRe('virtual|basic display|basic render|remote|parsec|idd|mirror|meta quest', 'i');

const spaces = (s: string) => pyStrip(s.replace(pyRe('\\s+', 'g'), ' '));

/** "Intel(R) Core(TM) i5-7500 CPU @ 3.40GHz" -> "Intel Core i5-7500"; "AMD Ryzen 7 3700X 8-Core Processor" ->
 * "AMD Ryzen 7 3700X". */
export function tidyCpu(name: string): string {
  name = name.replace(pyRe('\\((?:r|tm)\\)|®|™|^\\s*\\d+(st|nd|rd|th) gen\\s+', 'gi'), '');   // "12th Gen Intel..."
  name = pyStrip(name).replace(pyRe('\\s*@.*$|\\s+\\d+-core processor$|\\s+processor$|\\s+cpu$', 'gi'), '');
  return spaces(name.replace(pyRe('\\bcpu\\b', 'gi'), ''));
}

function vendor(text: string): string {
  for (const [pattern, short] of VENDORS) text = pyStrip(text).replace(pattern, () => short);
  return spaces(text);
}

export function looksLikeReport(text: string): boolean {
  const found = new Set(splitlines(text).map((line) => LINE.exec(line)?.[1]?.toLowerCase()).filter(Boolean));
  return found.has('cpu') && found.size >= 2;
}

function boardName(board: string, system: string): string {
  board = vendor(board);
  system = vendor(system);
  const name = board.includes(' ') ? board.slice(board.indexOf(' ') + 1) : board;
  // OEM machines report a part code as the board (Dell "0XHGV1"); the system name is more useful.
  if (system && !PLACEHOLDER.test(system) && (/^[0-9A-Z]{5,8}$/.test(name) || !board)) return name ? `${system} (board ${name})` : system;
  return PLACEHOLDER.test(board) ? '' : board;
}

const RAM_LINE = pyRe('^(\\d+(?:\\.\\d+)?)\\s*GB\\s*(total)?\\s*(DDR\\d)?\\s*(\\d{3,5})?\\s*(.*)$', 'i');
const gigabytes = (s: string) => Math.trunc(Number(asciiDigits(s)));

function ram(lines: string[]): string {
  const sticks: [number, string, string, string][] = [];
  let total: string | null = null;
  for (const line of lines) {
    const m = RAM_LINE.exec(line);
    if (!m) continue;
    if (m[2]) {
      total = `${gigabytes(m[1]!)}GB`;
      continue;
    }
    sticks.push([gigabytes(m[1]!), (m[3] ?? '').toUpperCase(), m[4] ?? '', pyStrip(m[5]!)]);
  }
  if (!sticks.length) return total ?? '';
  const size = sticks.reduce((n, s) => n + s[0], 0);
  const counts = new Map<number, number>();
  for (const [gb] of sticks) counts.set(gb, (counts.get(gb) ?? 0) + 1);
  const layout = [...counts].sort((a, b) => b[0] - a[0]).map(([gb, n]) => `${n}x${gb}GB`).join(' + ');
  const [, kind, speed] = sticks[0]!;
  const part = new Set(sticks.map((s) => s[3])).size === 1 ? sticks[0]![3] : '';
  let desc = `${size}GB (${layout})`;
  if (kind) desc += ` ${kind}` + (speed ? `-${speed}` : '');
  else if (speed) desc += ` ${speed} MT/s`;
  return pyStrip(`${desc} ${part}`);
}

function gpu(name: string): string {
  name = vendor(name.replace(pyRe('\\s*\\(rev \\w+\\)$'), ''));
  // lspci: "NVIDIA TU116 [GeForce GTX 1660 SUPER]", "AMD [AMD/ATI] Navi 48 [Radeon RX 9070 XT]": the last bracket is
  // the marketing name.
  const brackets = [...name.matchAll(pyRe('\\[([^\\]]+)\\]', 'g'))].map((m) => m[1]!);
  return brackets.length ? `${name.split(' ')[0]} ${brackets.at(-1)}` : name;
}

const MEDIA: Record<string, string> = { ssd: ' SSD', '4': ' SSD', hdd: ' HDD', '3': ' HDD', unspecified: '', '0': '' };
const DISK_MEDIA = pyRe('^(.*?)\\s+(SSD|HDD|Unspecified|[034])$', 'i');

/** Parts from the commands' output, in the order Deal Hunter shows them. */
export function parseReport(text: string): { category: string; model: string }[] {
  const found = Object.fromEntries(KEYS.map((k) => [k, [] as string[]])) as Record<(typeof KEYS)[number], string[]>;
  for (const line of splitlines(text)) {
    const m = LINE.exec(line);
    if (m && m[2]) found[m[1]!.toLowerCase() as (typeof KEYS)[number]].push(m[2]);
  }
  const parts: { category: string; model: string }[] = [];
  if (found.cpu.length) parts.push({ category: 'cpu', model: tidyCpu(found.cpu[0]!) });
  const board = boardName(found.board[0] ?? '', found.system[0] ?? '');
  if (board) parts.push({ category: 'motherboard', model: board });
  const memory = ram(found.ram);
  if (memory) parts.push({ category: 'ram', model: memory });
  const gpus = found.gpu.filter((g) => !VIRTUAL_GPU.test(g)).map(gpu);
  for (const g of new Set(gpus)) parts.push({ category: 'gpu', model: g });   // same card listed twice -> once
  for (let disk of found.disk) {
    // Windows ends each disk with its media type (a word, or a number on older PowerShell).
    const m = DISK_MEDIA.exec(pyStrip(disk));
    disk = m ? m[1]! + MEDIA[m[2]!.toLowerCase()]! : pyStrip(disk);
    disk = disk.replace(pyRe('^(\\d+(?:\\.\\d+)?)([GT])\\b'), '$1$2B');   // lsblk "1.8T" -> "1.8TB"
    parts.push({ category: 'storage', model: disk });
  }
  return parts;
}

/** New parts replace existing parts of the same category; other categories (network...) are kept. */
export function mergeParts(existing: Part[], parts: Part[]): Part[] {
  const replaced = new Set(parts.map((p) => p.category));
  return [...parts, ...existing.filter((p) => !replaced.has(p.category))];
}
