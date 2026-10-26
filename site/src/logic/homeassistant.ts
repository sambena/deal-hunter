// Importing devices from Home Assistant into My Stuff: a port of the Python app's homeassistant.py.
// Home Assistant's REST API has no device list, but its template endpoint can build one: every device behind an
// entity, with maker, model, area and which integrations/entity types it has. A rough guess at each device's kind is
// made here; the person picks what to import.
import { BlockedURL, check as checkAddress, type Resolve, resolveHost } from './netguard.js';
import { pyJsonDumps } from './py-text.js';
import { pyRe, pyStrip, truthy } from './py.js';
import { type Net, NetError } from './sources.js';

export const DEVICE_TEMPLATE = `
{%- set ns = namespace(ids=[]) -%}
{%- for s in states -%}{%- set d = device_id(s.entity_id) -%}
{%- if d and d not in ns.ids -%}{%- set ns.ids = ns.ids + [d] -%}{%- endif -%}{%- endfor -%}
[{%- for d in ns.ids -%}{{ {"id": d, "name": device_attr(d, "name_by_user") or device_attr(d, "name"),
  "make": device_attr(d, "manufacturer"), "model": device_attr(d, "model"), "area": area_name(d),
  "integrations": device_attr(d, "identifiers") | map("first") | unique | list,
  "domains": device_entities(d) | map("regex_replace", "[.].*", "") | unique | list,
  "entry_type": device_attr(d, "entry_type")} | tojson }}{{ "," if not loop.last }}{%- endfor -%}]
`;

// Software and virtual "devices" that aren't things you own.
const SKIP_INTEGRATIONS = new Set(['sun', 'met', 'backup', 'hassio', 'google_translate', 'google_assistant', 'ping',
  'proxmoxve', 'frigate', 'template', 'group', 'shopping_list', 'radio_browser', 'forecast_solar', 'openai_conversation',
  'wyoming', 'mqtt_statestream', 'uptime', 'rpi_power', 'hacs']);
const SKIP_MODELS = pyRe('^unifi (wlan|network)$|tts|assistant', 'i');
const SKIP_MAKERS = pyRe('proxmox server solutions|intel corporate', 'i');   // virtual machines, bare network cards

// [pattern over "make model name", kind, ticked by default]
const KIND_RULES: [RegExp, string, boolean][] = ([
  ['\\bipad\\b|\\btablet\\b|galaxy tab|fire hd|kindle', 'tablet', true],
  ['oled\\d{2}|qled|smart tv|chromecast|\\broku\\b|\\bshield\\b|bravia|vizio|\\btcl\\b|\\btv\\b|television', 'tv', true],
  ['nest hub|home mini|nest mini|google home|sonos|echo|homepod|onkyo|denon|yamaha|receiver|soundbar|speaker', 'audio', true],
  ['ubiquiti|unifi|netgear|eero|tp-link deco|asus rt|switch.*poe|poe switch|router|access point', 'network', true],
  ['xbox|playstation|ps5|nintendo|steam deck', 'console', true],
  ['printer|envy|laserjet|officejet|epson|brother|canon pixma', 'printer', true],
  ['microwave|blender|air fryer|coffee|espresso|keurig|instant pot|toaster|kettle|mixer', 'kitchen', true],
  ['device_(washer|dryer|refrigerator|dish_washer|oven)|washer|dryer|refrigerator|fridge|freezer|dishwasher'
    + '|\\boven\\b|\\brange\\b|stove|cooktop|water heater|vacuum|roomba', 'appliance', true],
  ['tesla|model [sy3x]\\b|rivian|ford|toyota', 'vehicle', false],
  ['tp-link|leviton|sonoff|kasa|shelly|bhyve|hobeian|aqara|ratgdo|espressif|zigbee|third reality|ecobee'
    + '|smart plug|switch|dimmer|thermostat', 'smart home', false],
] as [string, string, boolean][]).map(([p, k, t]) => [pyRe(p), k, t]);

export class HAError extends Error {}

type Device = Record<string, unknown>;
export interface Candidate { refs: unknown[]; name: string; make: string; model: string; area: unknown; kind: string; checked: boolean;
  already: boolean; parts: { category: string; model: string }[]; computer?: boolean; match_id?: unknown; match_name?: unknown }

/** localOk: the admin's Home Assistant may be on the home network; anyone else's must be public (and its answers
 * don't follow redirects). */
export async function fetchDevices(url: string, token: string, localOk: boolean, net: Net, resolve: Resolve = resolveHost): Promise<Device[]> {
  if (!url || !token) throw new HAError('Add your Home Assistant address and token in Admin > Home Assistant');
  if (url.includes('?') || url.includes('#')) throw new HAError("The Home Assistant address can't contain ? or #");
  const full = pyStrip(url).replace(/\/+$/, '') + '/api/template';
  let res;
  try {
    await checkAddress(full, localOk, 'Home Assistant address', resolve);
    res = await net({ url: full, method: 'POST', body: pyJsonDumps({ template: DEVICE_TEMPLATE }),
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json', 'User-Agent': 'deal-hunter/0.1 (Home Assistant device import)' },
      timeout: 30, redirect: localOk ? 'follow' : 'manual' });
  } catch (e) {
    if (e instanceof BlockedURL) throw new HAError(e.message);
    if (e instanceof NetError) throw new HAError(localOk ? `Can't reach Home Assistant at ${url} (${e.reason})` : "Can't reach Home Assistant at that address");
    throw e;
  }
  if (res.status === 401) throw new HAError('Home Assistant rejected the token');
  if (res.status === 403) {
    // Seen live: a Cloudflare-fronted address answered 403 before Home Assistant saw the request.
    throw new HAError('Access was refused (HTTP 403). If the address goes through Cloudflare or another proxy, '
      + "use Home Assistant's local address instead, e.g. http://192.168.1.10:8123");
  }
  if (res.status >= 300) throw new HAError(`Home Assistant returned HTTP ${res.status}`);
  try {
    return JSON.parse(new TextDecoder('utf-8').decode(res.body)) as Device[];
  } catch {
    throw new HAError("Home Assistant's answer wasn't a device list");
  }
}

const MAKER_NAMES: Record<string, string> = { lge: 'LG' };
const MAKER_SUFFIX = pyRe(',?\\s+(inc\\.?|corporation|corp\\.?|networks|technologies|co\\.?,? ltd\\.?|ltd\\.?|gmbh)$', 'i');

/** "Google Inc." + "Google Nest Hub" -> "Google Nest Hub"; "LGE" + "OLED65B2AUA" -> "LG OLED65B2AUA". */
export function fullModel(make: string, model: string): string {
  make = pyStrip(make).replace(MAKER_SUFFIX, '');
  make = MAKER_NAMES[make.toLowerCase()] ?? make;
  if (!model) return make;
  if (make && model.toLowerCase().includes(make.split(/\s+/).filter(Boolean)[0]!.toLowerCase())) return model;
  return pyStrip(`${make} ${model}`);
}

/** ("Google Inc.", "Google Nest Hub") -> ("Google", "Nest Hub"); ("LGE", "OLED65B2AUA") -> ("LG", "OLED65B2AUA"). */
export function splitMakeModel(make: string, model: string): [string, string] {
  const full = fullModel(make, model);
  const tidy = fullModel(make, '');
  if (tidy && full.toLowerCase().startsWith(tidy.toLowerCase() + ' ')) return [tidy, full.slice(tidy.length + 1)];
  return [tidy, !tidy ? full : model];
}

/** LG ThinQ appends the device type: "T1789EFH_F (DEVICE_WASHER)". */
export const cleanModel = (model: string) => pyStrip((model || '').replace(pyRe('\\s*\\(DEVICE_[A-Z_]+\\)$'), ''));

/** A candidate for import, or null for software and virtual devices. */
export function classify(device: Device, knownNames: Set<string>): Candidate | null {
  const integrations = new Set((truthy(device.integrations) ? device.integrations : []) as string[]);
  const make = (device.make as string) || '', model = (device.model as string) || '', name = pyStrip((device.name as string) || '');
  if (device.entry_type === 'service' || [...integrations].some((i) => SKIP_INTEGRATIONS.has(i)) || SKIP_MODELS.test(model) || SKIP_MAKERS.test(make)) return null;
  if (!(make || model) && !integrations.has('mobile_app') && !/ipad|iphone/i.test(name)) return null;   // bare MAC addresses and the like
  const text = `${make} ${model} ${name}`.toLowerCase();
  let kind = 'other', ticked = false;
  if (integrations.has('mobile_app') || pyRe('\\b(iphone|pixel|galaxy s\\d)').test(text)) {
    [kind, ticked] = [/ipad|tab/.test(text) ? 'tablet' : 'phone', true];
  } else {
    // Maker and model first: a smart plug named "Microwave" is still a smart plug.
    for (const haystack of [`${make} ${model}`.toLowerCase(), name.toLowerCase()]) {
      const match = KIND_RULES.find(([pattern]) => pattern.test(haystack));
      if (match) {
        [, kind, ticked] = match;
        break;
      }
    }
  }
  const already = knownNames.has(name.toLowerCase());
  return { refs: [device.id ?? null], name: name || model, make, model: cleanModel(model), area: (device.area as string) || '', kind,
    checked: ticked && !already, already, parts: [] };
}

// ---- computers ----------------------------------------------------------------------
// Home Assistant doesn't know computers as computers. It sees up to three entries per PC, all named after it: the
// network card the router tracks (maker = the card's vendor), a ping check, and a wake-on-LAN button. Those are grouped
// into one PC, with the card kept as a part; its CPU/RAM/GPU come later from "Get specs".

const NIC_VENDORS = pyRe('micro-star|asustek|gigabyte|asrock|dell|lenovo|hewlett|\\bhp\\b|intel|realtek|lite-on|'
  + 'plugable|killer|rivet|raspberry pi|azurewave|liteon|qualcomm|mediatek|framework', 'i');
const MAC_NAME = /^[0-9a-f]{2}([:-][0-9a-f]{2}){5}(?=\n?$)/i;
const GENERIC_WORDS = new Set(['main', 'pc', 'box', 'server', 'computer', 'gaming', 'the', 'desktop', 'my']);

export function computerSignal(device: Device): string | null {
  const integrations = new Set((truthy(device.integrations) ? device.integrations : []) as string[]);
  const domains = new Set((truthy(device.domains) ? device.domains : []) as string[]);
  const make = (device.make as string) || '', model = (device.model as string) || '';
  if (integrations.has('ping')) return 'ping';
  if (!make && !model && domains.size === 1 && domains.has('button')) return 'wake';
  if (make && !model && [...domains].every((d) => d === 'device_tracker') && !integrations.has('mobile_app')) return 'nic';
  return null;
}

const words = (name: string) => new Set([...name.toLowerCase().matchAll(/[a-z0-9]+/g)].map((m) => m[0]).filter((w) => !GENERIC_WORDS.has(w)));

/** An existing PC/server this Home Assistant name refers to ("proxmox" -> "Proxmox server"). */
export function matchMachine(name: string, machines: Device[]): Device | null {
  const mine = words(name);
  for (const m of machines) {
    const kind = 'kind' in m ? m.kind : 'pc';
    if ((kind === 'pc' || kind === 'server') && mine.size && [...words(m.name as string)].some((w) => mine.has(w))) return m;
  }
  return null;
}

export function tidyVendor(make: string): string {
  make = pyStrip(make).replace(pyRe('\\s+(network\\s+)?communication.*$', 'i'), '');
  make = make.replace(pyRe(',?\\s+(trading\\s+)?(inc\\.?|corporation|corp\\.?|networks|technologies|co\\.?,? ltd\\.?|ltd\\.?|limited|gmbh)$', 'i'), '');
  return ({ 'micro-star intl': 'MSI', "micro-star int'l": 'MSI', 'intel corporate': 'Intel' } as Record<string, string>)[make.toLowerCase()] ?? make;
}

const isUpper = (c: string) => c !== '' && c === c.toUpperCase() && c !== c.toLowerCase();

function computers(devices: Device[], machines: Device[]): [Candidate[], Set<unknown>] {
  const groups = new Map<string, [string, Device][]>();
  for (const d of devices) {
    const sig = computerSignal(d);
    const name = pyStrip((d.name as string) || '');
    if (sig && name && !MAC_NAME.test(name)) {
      const key = name.toLowerCase().replace(/\s+/g, ' ');
      groups.set(key, [...(groups.get(key) ?? []), [sig, d]]);
    }
  }
  // The Pi Home Assistant runs on also reports its board ("Raspberry Pi 4") as a device of its own.
  const piBoard = devices.find((d) => ((truthy(d.integrations) ? d.integrations : []) as string[]).includes('raspberry_pi')) ?? null;
  const out: Candidate[] = [];
  const used = new Set<unknown>();
  const byMatch = new Map<unknown, Candidate>();
  for (const entries of groups.values()) {
    const sigs = new Set(entries.map(([s]) => s));
    const nics = entries.filter(([s]) => s === 'nic').map(([, d]) => d);
    if (sigs.size === 1 && sigs.has('nic') && !nics.every((d) => NIC_VENDORS.test(d.make as string))) continue;   // a tracked client that isn't obviously a computer
    const names = entries.map(([, d]) => pyStrip(d.name as string));
    let name = names.find((n) => isUpper([...n][0] ?? '')) ?? names[0]!;
    const parts = nics.map((d) => ({ category: 'network', model: `${tidyVendor(d.make as string)} network adapter` }));
    const refs: unknown[] = entries.map(([, d]) => d.id);
    let kind = 'pc', model = '';
    if (nics.some((d) => /raspberry pi/i.test(d.make as string))) {
      kind = 'server';
      model = piBoard && truthy(piBoard.model) ? `Raspberry Pi ${piBoard.model as string}` : 'Raspberry Pi';
      name = name.toLowerCase() === 'homeassistant' ? 'Home Assistant (Raspberry Pi)' : name;
      if (piBoard) refs.push(piBoard.id);
    }
    for (const r of refs) used.add(r);
    const match = matchMachine(name, machines);
    if (match && byMatch.has(match.id)) {   // "Gaming PC" and "Main Gaming PC" are the same box
      const merged = byMatch.get(match.id)!;
      merged.refs.push(...refs);
      merged.parts.push(...parts.filter((p) => !merged.parts.some((q) => q.category === p.category && q.model === p.model)));
      continue;
    }
    const cand: Candidate = { refs, name, make: '', model, area: '', kind, parts, checked: true, already: false, computer: true,
      match_id: match ? match.id : null, match_name: match ? match.name : '' };
    if (match) byMatch.set(match.id, cand);
    out.push(cand);
  }
  return [out, used];
}

const ORDER = ['pc', 'tv', 'phone', 'tablet', 'audio', 'network', 'console', 'printer', 'appliance', 'kitchen', 'vehicle', 'smart home', 'other'];

/** What can be imported, ticked ones first, then by kind and name. */
export async function candidates(url: string, token: string, machines: Device[], localOk: boolean, net: Net, resolve?: Resolve): Promise<Candidate[]> {
  const knownRefs = new Set(machines.flatMap((m) => String(m.source_ref || '').split(/\s+/).filter(Boolean)));
  const knownNames = new Set(machines.map((m) => String(m.name).toLowerCase()));
  const devices = (await fetchDevices(url, token, localOk, net, resolve)).filter((d) => !knownRefs.has(d.id as string));
  const [out, used] = computers(devices, machines);
  const seen = new Set<string>();
  for (const d of devices) {
    if (used.has(d.id)) continue;
    const c = classify(d, knownNames);
    if (!c) continue;
    const key = JSON.stringify([c.name.toLowerCase(), c.model.toLowerCase()]);
    if (seen.has(key)) continue;   // e.g. the same gateway reported by two integrations
    seen.add(key);
    out.push(c);
  }
  const rank = (k: string) => (ORDER.includes(k) ? ORDER.indexOf(k) : 99);
  return out.map((c, i) => [c, i] as const).sort(([a, ia], [b, ib]) => Number(!a.checked) - Number(!b.checked) || rank(a.kind) - rank(b.kind)
    || (a.name.toLowerCase() < b.name.toLowerCase() ? -1 : a.name.toLowerCase() > b.name.toLowerCase() ? 1 : 0) || ia - ib).map(([c]) => c);
}
