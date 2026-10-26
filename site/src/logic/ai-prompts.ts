// The AI buttons' prompts and answer shapes: the prompt half of the Python app's ai.py, word for word, so each
// request asks exactly what it asked before (test/ai.test.ts compares them with Python's own).
import { pyJsonIndent } from './py-text.js';
import { pyCapitalize, pyFixed, pyStr, truthy } from './py.js';

type Row = Record<string, unknown>;

export const SUGGESTION_SCHEMA = {
  type: 'object',
  properties: {
    suggestions: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          name: { type: 'string' },
          category: { type: 'string' },
          query: { type: 'string' },
          exclude: { type: 'array', items: { type: 'string' } },
          max_price: { type: ['number', 'null'] },
          reason: { type: 'string' },
        },
        required: ['name', 'category', 'query', 'exclude', 'max_price', 'reason'],
        additionalProperties: false,
      },
    },
  },
  required: ['suggestions'],
  additionalProperties: false,
};

export const VERDICT_SCHEMA = {
  type: 'object',
  properties: {
    verdict: { type: 'string', enum: ['good', 'ok', 'skip'] },
    note: { type: 'string' },
  },
  required: ['verdict', 'note'],
  additionalProperties: false,
};

const QUERY_RULES = `Watch query syntax (matched against listing titles, case/punctuation-insensitive):
- space-separated terms must ALL appear, so keep queries short: usually just the model number (e.g. "10980xe")
- a|b means either term (e.g. "n100|n150|n97")
- "exact phrase" in quotes
- exclude is a list of words that disqualify a listing (e.g. "rdimm", "ecc", "laptop")
Used-market sellers write titles inconsistently, so prefer model numbers over full product names.`;

/** f"{x:,.0f}" / f"{x:,}": a number with thousands separators. */
function thousands(x: number, decimals: number | null): string {
  const text = decimals === null ? String(x) : pyFixed(x, decimals);
  const [whole, frac] = text.split('.') as [string, string?];
  const sign = whole.startsWith('-') ? '-' : '';
  const digits = whole.replace('-', '').replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  return sign + digits + (frac !== undefined ? '.' + frac : '');
}

export function describeMachine(machine: Row): string {
  const kind = (machine.kind as string) || 'pc';
  const lines = [`${kind === 'pc' || kind === 'server' ? 'Machine' : pyCapitalize(kind)}: ${pyStr(machine.name)}`];
  const makeModel = [machine.make, machine.model].filter(truthy).map(pyStr).join(' ');
  if (makeModel) lines.push(`Make/model: ${makeModel}`);
  if (truthy(machine.year)) lines.push(`Year: ${pyStr(machine.year)}`);
  if (truthy(machine.msrp)) lines.push(`MSRP: $${thousands(machine.msrp as number, 0)}`);
  if (truthy(machine.price_paid)) {
    lines.push(`Paid: $${thousands(machine.price_paid as number, 0)}` + (truthy(machine.purchased) ? ` (${pyStr(machine.purchased)})` : ''));
  }
  lines.push(...((machine.parts ?? []) as Row[]).map((p) => `- ${'category' in p ? pyStr(p.category) : 'other'}: ${'model' in p ? pyStr(p.model) : ''}`));
  lines.push(...((truthy(machine.custom) ? machine.custom : []) as Row[]).filter((f) => truthy(f.value)).map((f) => `${pyStr(f.label)}: ${pyStr(f.value)}`));
  if (truthy(machine.notes)) lines.push(`Notes: ${pyStr(machine.notes)}`);
  return lines.join('\n');
}

export function upgradesPrompt(machine: Row): [string, object] {
  if (!['pc', 'server'].includes((machine.kind as string) || 'pc')) return gearPrompt(machine);
  return [`You help someone find used PC hardware upgrades.

${describeMachine(machine)}

Suggest the best drop-in upgrades for this exact machine: parts that work without
replacing the motherboard (CPU, RAM, GPU, storage, anything else worthwhile). For each,
mention BIOS, power, cooling or physical-fit caveats in \`reason\`, and set max_price to a
sensible used-market ceiling in USD, or null if you're unsure. Only suggest parts you
are confident are compatible. Give 3 to 10 suggestions, best value first.

${QUERY_RULES}`, SUGGESTION_SCHEMA];
}

export const RADAR_SCHEMA = {
  type: 'object',
  properties: {
    suggestions: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          device_id: { type: 'integer' },
          name: { type: 'string' },
          category: { type: 'string' },
          query: { type: 'string' },
          exclude: { type: 'array', items: { type: 'string' } },
          max_price: { type: ['number', 'null'] },
          reason: { type: 'string' },
        },
        required: ['device_id', 'name', 'category', 'query', 'exclude', 'max_price', 'reason'],
        additionalProperties: false,
      },
    },
  },
  required: ['suggestions'],
  additionalProperties: false,
};

/** One request for the whole deal radar: the single best upgrade to watch for each device. */
export function radarPrompt(devices: Row[]): [string, object] {
  const listing = devices.map((d) => `[device_id ${pyStr(d.id)}]\n${describeMachine(d)}`).join('\n\n');
  return [`You help someone keep an eye out for deals that would upgrade the things they own.

For EACH device below, suggest the single most worthwhile upgrade or replacement to watch for
(one per device, using its device_id): a clearly better model that's now affordable used or on
sale, or a genuinely useful accessory if the device is already excellent. Skip a device (leave
it out) if nothing is worth watching for. Set max_price to a good-deal price in USD, or null if
unsure, and explain in \`reason\` why it's a real step up. Use the device kind as \`category\`.

${listing}

${QUERY_RULES}`, RADAR_SCHEMA];
}

/** Upgrade/replacement ideas for anything that isn't a PC: TVs, phones, network gear, appliances... */
export function gearPrompt(device: Row): [string, object] {
  return [`You help someone find good deals on things they own, new or used.

${describeMachine(device)}

Work out what this device is from its make and model (model codes like OLED65B2AUA or U7NHD are
fine to interpret). Suggest worthwhile upgrades or replacements to watch for: clearly better
models a generation or two newer that are now affordable, plus genuinely useful accessories
(e.g. a soundbar for a TV, a case for a phone). For each, say in \`reason\` why it's a real step up
from what they have and any compatibility caveats, use the device kind as \`category\`, and set
max_price to a sensible deal price in USD (used or on sale), or null if you're unsure.
Give 3 to 8 suggestions, best value first. If the device is already top of its class, say so in
the reasons and suggest fewer.

${QUERY_RULES}`, SUGGESTION_SCHEMA];
}

export const PART_CATEGORIES = ['cpu', 'motherboard', 'ram', 'gpu', 'storage', 'psu', 'cooler', 'case', 'network', 'other'];

export const PARTS_SCHEMA = {
  type: 'object',
  properties: {
    parts: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          category: { type: 'string', enum: PART_CATEGORIES },
          model: { type: 'string' },
        },
        required: ['category', 'model'],
        additionalProperties: false,
      },
    },
  },
  required: ['parts'],
  additionalProperties: false,
};

/** Parts from whatever the person pasted: System Information, a receipt, notes. */
export function specsPrompt(machine: Row, text: string): [string, object] {
  return [`Pull a computer's parts out of the text below, which describes the computer "${pyStr(machine.name)}".

Return each part once with a short, searchable model name, e.g. "AMD Ryzen 7 3700X",
"MSI MPG B550 GAMING PLUS", "32GB (2x16GB) DDR4-3200", "NVIDIA GeForce RTX 3060 12GB", "Samsung 980 Pro 1TB NVMe".
Combine RAM sticks into one entry. Leave out anything the text doesn't actually say; don't guess.

Text:
---
${[...text].slice(0, 6000).join('')}
---`, PARTS_SCHEMA];
}

export function draftPrompt(description: string, machines: Row[]): [string, object] {
  const owned = machines.map(describeMachine).join('\n\n') || '(none listed)';
  return [`You help someone set up saved searches for used hardware.

They want: ${description}

Their current machines, in case it's relevant:
${owned}

Turn this into 1 to 6 watches. If they describe a need rather than a product
(e.g. "low power box for Frigate NVR"), pick the specific models that fit best
and make a watch per model family. Set max_price if they gave a budget, else a sensible
used-market ceiling in USD, or null if you're unsure. Explain each pick in \`reason\`.

${QUERY_RULES}`, SUGGESTION_SCHEMA];
}

export function judgePrompt(listing: Row, watch: Row, machine: Row | null): [string, object] {
  const price = listing.total !== null && listing.total !== undefined ? `$${pyFixed(listing.total as number, 2)}` : 'not stated';
  if (watch.kind === 'vehicle') return vehicleJudgePrompt(listing, watch, price);
  return [`Is this used-hardware listing worth pursuing?

Listing title: ${pyStr(listing.title)}
Price incl. shipping: ${price}
Condition: ${(listing.condition as string) || 'unknown'}
Source: ${pyStr(listing.source)}
Searched for: ${pyStr(watch.name)} (query "${pyStr(watch.query)}")
${machine ? 'It is meant for this machine:' + '\n' + describeMachine(machine) : ''}

Answer with verdict good / ok / skip and a short note (2 sentences max) covering
compatibility problems, red flags in the title, and whether the price is fair for
the used market.`, VERDICT_SCHEMA];
}

function vehicleJudgePrompt(listing: Row, watch: Row, price: string): [string, object] {
  const miles = listing.miles !== null && listing.miles !== undefined ? thousands(listing.miles as number, null) : 'not stated';
  const years = [watch.year_min, watch.year_max].filter(truthy).map(pyStr).join('–') || 'any';
  return [`Is this used car or truck listing worth pursuing?

Listing title: ${pyStr(listing.title)}
Model year: ${truthy(listing.year) ? pyStr(listing.year) : 'unknown'}
Odometer: ${miles} miles
Title status: ${(listing.title_status as string) || 'not stated (assume clean unless the title says otherwise)'}
Asking price: ${price}
Location: ${(listing.location as string) || 'unknown'}
Seller / source: ${(listing.buying as string) || pyStr(listing.source)}
Searched for: ${pyStr(watch.name)} (query "${pyStr(watch.query)}", years ${years}, max miles ${truthy(watch.max_miles) ? pyStr(watch.max_miles) : 'any'})

Answer with verdict good / ok / skip and a short note (2 sentences max): is the price fair for that
year and mileage in the US used market, and any red flags (salvage/rebuilt title, mileage too high
for the year, a price far below market that looks like a scam, "needs work" wording).`, VERDICT_SCHEMA];
}

export const REVIEW_SCHEMA = {
  type: 'object',
  properties: {
    reviews: { type: 'array', items: {
      type: 'object',
      properties: { id: { type: 'integer' }, verdict: { type: 'string', enum: ['good', 'ok', 'skip'] }, reason: { type: 'string' } },
      required: ['id', 'verdict', 'reason'], additionalProperties: false } },
    // Changes to a watch that would stop the bad finds coming back; null/[] = leave as is.
    watch_changes: { type: 'array', items: {
      type: 'object',
      properties: { watch_id: { type: 'integer' }, add_excludes: { type: 'array', items: { type: 'string' } },
        max_price: { type: ['number', 'null'] }, min_price: { type: ['number', 'null'] },
        year_min: { type: ['integer', 'null'] }, max_miles: { type: ['integer', 'null'] }, reason: { type: 'string' } },
      required: ['watch_id', 'add_excludes', 'max_price', 'min_price', 'year_min', 'max_miles', 'reason'],
      additionalProperties: false } },
  },
  required: ['reviews', 'watch_changes'],
  additionalProperties: false,
};

function reviewLine(l: Row): string {
  const price = l.total !== null && l.total !== undefined ? `$${pyFixed(l.total as number, 0)}` : 'no price';
  const facts = [truthy(l.year) ? `year ${pyStr(l.year)}` : '', l.miles !== null && l.miles !== undefined ? `${thousands(l.miles as number, null)} miles` : '',
    truthy(l.title_status) ? `${pyStr(l.title_status)} title` : '', truthy(l.size) ? `size ${pyStr(l.size)}` : '',
    (l.condition as string) || '', (l.location as string) || '', (l.buying as string) || (l.source as string) || ''];
  return `- id ${pyStr(l.id)}: ${pyStr(l.title)} | ${price} | ` + facts.filter(Boolean).join(' | ');
}

/** One request judging many finds: which are worth a look and which are obvious bad deals. */
export function reviewPrompt(listings: Row[], watches: Map<unknown, Row>, instructions: string): [string, object] {
  const groups = new Map<unknown, Row[]>();
  for (const l of listings) groups.set(l.watch_id, [...(groups.get(l.watch_id) ?? []), l]);
  const parts: string[] = [];
  for (const [wid, ls] of groups) {
    const w = watches.get(wid) ?? {};
    const want = [`query "${'query' in w ? pyStr(w.query) : ''}"`];
    if (truthy(w.max_price)) want.push(`max $${pyFixed(w.max_price as number, 0)}`);
    if (truthy(w.year_min) || truthy(w.year_max)) want.push(`years ${truthy(w.year_min) ? pyStr(w.year_min) : 'any'}-${truthy(w.year_max) ? pyStr(w.year_max) : 'any'}`);
    if (truthy(w.max_miles)) want.push(`max ${thousands(w.max_miles as number, null)} miles`);
    if (truthy(w.size)) want.push(`size ${pyStr(w.size)}`);
    parts.push(`Watch id ${pyStr(wid)} "${'name' in w ? pyStr(w.name) : '?'}" (${want.join(', ')}):\n` + ls.map(reviewLine).join('\n'));
  }
  const asked = instructions.trim() || '(nothing extra: use your best judgement)';
  return [`You're helping someone weed out bad deals from listings their watches found on used and retail
marketplaces (eBay, KSL, Craigslist, OfferUp, Poshmark, Slickdeals...). Prices are in US dollars.

Their own requirements, which outrank your defaults:
${asked}

For EVERY listing below give a verdict:
- "good": a fair or better price for what it is, worth a look
- "ok": nothing wrong, but not a deal
- "skip": an obvious bad deal or not what they want: overpriced for the used market, wrong item or a part/accessory
  instead of the thing, scam signs (price far too low, vague title), salvage/rebuilt title or very high miles for a
  car without a matching low price, wrong size, or it breaks one of their requirements above.
Be decisive but don't skip something only because details are missing. The reason is one short sentence
(under 20 words) a person can act on. Use each listing's id exactly.

Then, in watch_changes, suggest changes to a watch only where they'd stop the same kind of bad find coming
back: words to exclude (short, specific: "floor mats", "salvage", "parts"; never a word the good finds use), a
price limit, a minimum model year or a mileage cap. Use null (or []) for anything to leave as it is, and leave
watch_changes empty when the watch is fine. Never loosen a limit the person set.

${parts.join('\n')}`, REVIEW_SCHEMA];
}

export const TUNE_SCHEMA = {
  type: 'object',
  properties: {
    query: { type: 'string' },
    exclude: { type: 'array', items: { type: 'string' } },
    min_price: { type: ['number', 'null'] }, max_price: { type: ['number', 'null'] },
    year_min: { type: ['integer', 'null'] }, year_max: { type: ['integer', 'null'] },
    max_miles: { type: ['integer', 'null'] }, size: { type: ['string', 'null'] },
    sources: { type: 'array', items: { type: 'string' } },
    reason: { type: 'string' },
  },
  required: ['query', 'exclude', 'min_price', 'max_price', 'year_min', 'year_max', 'max_miles', 'size', 'sources', 'reason'],
  additionalProperties: false,
};

/** A better version of one watch: its search words, excludes, limits and sites. */
export function tunePrompt(watch: Row, context: string, sources: Record<string, string>, instructions: string): [string, object] {
  const kind = ({ vehicle: 'a car or truck', clothing: 'clothes or shoes' } as Record<string, string>)[watch.kind as string] ?? 'an item';
  const fields = Object.fromEntries(['name', 'query', 'exclude', 'min_price', 'max_price', 'condition', 'year_min', 'year_max', 'max_miles',
    'size', 'department', 'sources'].filter((k) => truthy(watch[k])).map((k) => [k, watch[k]]));
  const asked = instructions.trim() || '(nothing extra)';
  return [`Improve this Deal Hunter watch, a saved search for ${kind} that's run on several sites.

How searching works: every word in the query must be in a listing's title (case and punctuation ignored);
a|b means either word; "two words" is a phrase; -word excludes. Car and truck watches: a body style word
(pickup, suv, van, minivan, sedan, coupe, convertible, hatchback, wagon) searches that style, and a finding
must have a model year. Clothing watches: the size is matched too. Too many words, a too-specific model
name or price limits that are too tight find nothing; too few words find junk.

The watch now:
${pyJsonIndent(fields, 1)}

How its last searches went:
${context || '(not searched yet)'}

Sites it can use (key: what it is):
${Object.entries(sources).map(([k, v]) => `- ${k}: ${v}`).join('\n')}

What the person says, which outranks your defaults: ${asked}

Give the improved watch: the full query (use the syntax above), the full exclude list, limits (null = none),
and the sites to search (keys from the list). Keep what's already right; change only what helps it find the
thing they want at a good price. reason: two short sentences on what you changed and why.`, TUNE_SCHEMA];
}
