// Marketplace sources: a port of the Python app's sources.py, source for source, with its rate gaps, caches, retries
// and page reading. Each returns raw listings:
//     {source, source_id, title, price, shipping, currency, url, image, location, condition, buying, text, ...}
// `text` is extra description used only for junk filtering; `match_text`, when there, is what the query must match.
// The network, the clock and sleeping are passed in (Net, Clock), so tests use made-up pages and never call a shop;
// test/sources.test.ts checks every source against the Python code's own answers on the same pages.
import { bodyStyle, normSize, parseQuery, searchTerms } from './matching.js';
import { htmlUnescape, JSONDecodeError, parsedateTimestamp, DateValueError, pyJsonDumps, quote, quotePlus, rawDecode, urlencode } from './py-text.js';
import { pyFloat, pyRe, pySplit, pyStr, pyStrip, pyTitle, splitlines, truthy } from './py.js';
import { type Element, fromstring, ParseError } from './xml.js';

export const USER_AGENT = 'deal-hunter/0.1 (personal hardware watcher)';

export class SourceError extends Error {}

/** A request that never got an answer: Python's URLError, with its reason. */
export class NetError extends Error {
  constructor(readonly reason: string) { super(reason); }
}

export interface NetRequest { url: string; method: 'GET' | 'POST'; headers: Record<string, string>; body: string | null; timeout: number }
export interface NetResponse { status: number; body: Buffer }
/** One HTTP request; throws NetError when there is no answer. Redirects are followed, as urllib follows them. */
export type Net = (req: NetRequest) => Promise<NetResponse>;
export interface Clock { now(): number; sleep(seconds: number): Promise<void> }

export const realNet: Net = async ({ url, method, headers, body, timeout }) => {
  let res: Response;
  try {
    res = await fetch(url, { method, headers, ...(body !== null ? { body } : {}), signal: AbortSignal.timeout(timeout * 1000) });
  } catch (e) {
    const err = e as Error & { cause?: { code?: string; message?: string } };
    throw new NetError(err.cause?.code ?? err.cause?.message ?? err.message);
  }
  return { status: res.status, body: Buffer.from(await res.arrayBuffer()) };
};
export const realClock: Clock = { now: () => Date.now() / 1000, sleep: (s) => new Promise((done) => setTimeout(done, s * 1000)) };

export type Watch = Record<string, unknown> & { query: string };
export type Settings = Record<string, unknown>;
export type Listing = Record<string, unknown>;

// eBay's errors in words people can act on (the error id stays at the end, for retries and support).
const EBAY_ERRORS: Record<number, string> = {
  12023: 'this search matches too much to return; add a model, a year range or a price limit',
  12506: "eBay can't check which parts fit in this category",
  1001: "the eBay keys aren't accepted; check them in Admin > eBay",
};

/** One short sentence for a source's HTTP error instead of its raw JSON. */
function readableError(code: number, host: string, body: string): string {
  let errors: unknown[] = [];
  try {
    const parsed = JSON.parse(body) as { errors?: unknown[] };
    errors = (parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed.errors : undefined) || [];
    if (!Array.isArray(errors)) errors = [];
  } catch {
    errors = [];
  }
  if (errors.length && errors[0] && typeof errors[0] === 'object' && !Array.isArray(errors[0])) {
    const first = errors[0] as Record<string, unknown>;
    const eid = first.errorId;
    const what = (typeof eid === 'number' && EBAY_ERRORS[eid]) || [...String(first.message || '').split('. ')[0]!].slice(0, 160).join('');
    return `${host.includes('ebay') ? 'eBay' : host}: ${what} (error ${pyStr(eid)})`;
  }
  return `HTTP ${code} from ${host}: ${[...body].slice(0, 160).join('')}`;
}

const netloc = (url: string) => new URL(url).host;
const decode = (b: Buffer) => new TextDecoder('utf-8').decode(b);
const sliceChars = (s: string, n: number) => [...s].slice(0, n).join('');

// Ordinary browser headers, for the sites with bot protection.
const KSL_HEADERS = {
  'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36',
  Accept: 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
  'Accept-Language': 'en-US,en;q=0.9',
};
const KSL_GAP_SECONDS = 4;
const KSL_CARS_PAGES = 5;   // about 20 listings a page, newest first
// KSL's body styles ("pickup" as a keyword would be read as the old Toyota Pickup model).
const KSL_BODY: Record<string, string> = { pickup: 'Truck', suv: 'SUV', van: 'Van', minivan: 'Minivan', sedan: 'Sedan',
  coupe: 'Coupe', convertible: 'Convertible', hatchback: 'Hatchback', wagon: 'Wagon' };
const EBAY_CARS_TRUCKS = '6001';   // eBay Motors > Cars & Trucks
// The best-selling models of each body style, most common first, for eBay keyword searches.
const EBAY_BODY_MODELS: Record<string, string[]> = {
  pickup: ['f-150', 'silverado', 'ram', 'tacoma', 'sierra', 'tundra', 'ranger', 'colorado'],
  suv: ['rav4', 'cr-v', 'equinox', 'explorer', '4runner', 'tahoe', 'wrangler', 'highlander'],
  van: ['sprinter', 'transit', 'express', 'promaster'],
  minivan: ['odyssey', 'sienna', 'pacifica', 'grand caravan'],
};
const EBAY_MOTORS_PARTS = '6028';   // eBay Motors > Parts & Accessories
const EBAY_MOTORS_TREE = '100';   // eBay's category tree for eBay Motors US
// Reddit rate-limits anonymous RSS hard, and every Reddit source shares the limit: space all requests out, and after
// a 429 leave Reddit alone for a while instead of retrying into it.
const REDDIT_GAP_SECONDS = 8;
const REDDIT_COOLDOWN_SECONDS = 600;
const ATOM = { a: 'http://www.w3.org/2005/Atom' };
const PRICE_RE = pyRe('\\$\\s?(\\d{1,5}(?:,\\d{3})*(?:\\.\\d{2})?)|(\\d{1,5}(?:\\.\\d{2})?)\\s?(?:usd|shipped|obo)\\b', 'i');
const DEAL_PRICE_RE = pyRe('\\$\\s?(\\d{1,3}(?:,\\d{3})+|\\d+)(\\.\\d{1,2})?');
const DEAL_CONDITIONS: [string, string][] = [['open box', 'open box'], ['open-box', 'open box'], ['refurb', 'refurbished'],
  ['renewed', 'refurbished'], ['resale', 'used'], ['used', 'used'], ['pre-owned', 'used']];
const POSHMARK_GAP_SECONDS = 3;
const POSHMARK_CONDITIONS: Record<string, string> = { nwt: 'new with tags', ret: 'new (retail)', uln: 'like new', ug: 'used, good',
  uf: 'used, fair' };
const CRAIGSLIST_URL = 'https://sapi.craigslist.org/web/v8/postings/search/full';
// Craigslist's cars+trucks "type" filter (auto_bodytype); pickups are listed as pickup or truck.
const CRAIGSLIST_BODY: Record<string, string[]> = { pickup: ['7', '9'], suv: ['10'], van: ['12'], minivan: ['5'], sedan: ['8'],
  coupe: ['3'], convertible: ['2'], hatchback: ['4'], wagon: ['11'] };
const CRAIGSLIST_GAP_SECONDS = 3;
const OFFERUP_RADII = [5, 10, 20, 30, 50];
const OFFERUP_VEHICLE_RADII = [25, 80, 100, 200];   // vehicle searches offer different distances (else nationwide)
const OFFERUP_MILEAGE = [25000, 50000, 75000, 100000, 125000, 150000, 175000, 200000];
const OFFERUP_GAP_SECONDS = 4;

const dict = (v: unknown): Record<string, unknown> => (v && typeof v === 'object' && !Array.isArray(v) ? v as Record<string, unknown> : {});
/** Python's `d.get(k) or {}`, then reading it as a dict. */
const orDict = (v: unknown): Record<string, unknown> => (truthy(v) ? dict(v) : {});
const orList = (v: unknown): unknown[] => (truthy(v) && Array.isArray(v) ? v : []);
/** Python's `", ".join(x for x in (...) if x)`. */
const joinTruthy = (sep: string, values: unknown[]) => values.filter(truthy).map((x) => pyStr(x)).join(sep);
/** Python's str(float): whole numbers keep ".0" (prices and limits come from REAL columns, so they're floats). */
const floatRepr = (n: number) => (Number.isInteger(n) && Math.abs(n) < 1e16 ? n.toFixed(1) : String(n));
/** Python's int(float(v or 50)) for the local radius. */
const radiusOf = (settings: Settings) => Math.trunc(pyFloat(truthy(settings.local_radius_miles) ? settings.local_radius_miles : 50));
const zipOf = (settings: Settings) => pyStrip(truthy(settings.zip_code) ? pyStr(settings.zip_code) : '');

/** A simple lock: one Reddit request at a time, as Python's threading.Lock kept it. */
class Lock {
  private tail = Promise.resolve();
  run<T>(fn: () => Promise<T>): Promise<T> {
    const result = this.tail.then(fn);
    this.tail = result.then(() => undefined, () => undefined);
    return result;
  }
}

/** Every source, with the state Python kept in module globals: tokens, caches and when each site was last asked. */
export class Sources {
  private ebayToken = { value: null as string | null, expires: 0, client: null as string | null };
  private partsCategoryCache = new Map<string, string | null>();
  private redditCache: { at: number; posts: Record<string, unknown>[]; subs?: string } = { at: 0, posts: [] };
  private redditState = { last: 0, blockedUntil: 0 };
  private redditLock = new Lock();
  private kslLast = 0;
  private kslAction: string | null = null;
  private poshmarkLast = 0;
  private craigslistLast = 0;
  private places = new Map<string, { lat: number; lon: number; city: string; state: string }>();   // ZIP -> where (OfferUp needs it)
  private offerupLast = 0;

  constructor(private readonly net: Net = realNet, private readonly clock: Clock = realClock) {}

  readonly all: Record<string, (watch: Watch, settings: Settings) => Promise<Listing[]>> = {
    ebay: (w, s) => this.ebaySearch(w, s, false), ebay_local: (w, s) => this.ebaySearch(w, s, true),
    reddit: (w, s) => this.reddit(w, s), bestbuy: (w, s) => this.bestbuy(w, s), slickdeals: (w, s) => this.slickdeals(w, s),
    buildapcsales: (w, s) => this.buildapcsales(w, s), ksl: (w, s) => this.ksl(w, s), craigslist: (w, s) => this.craigslist(w, s),
    offerup: (w, s) => this.offerup(w, s), ksl_cars: (w, s) => this.kslCars(w, s), poshmark: (w, s) => this.poshmark(w, s),
  };

  private async request(url: string, headers: Record<string, string>, timeout: number, body: string | null = null): Promise<NetResponse> {
    return this.net({ url, method: body === null ? 'GET' : 'POST', headers, body, timeout });
  }

  /** sources.py's _http: JSON from an API, with its errors in words. */
  private async http(url: string, options: { data?: string; headers?: Record<string, string>; timeout?: number } = {}): Promise<unknown> {
    let res: NetResponse;
    try {
      res = await this.request(url, { 'User-Agent': USER_AGENT, ...(options.headers ?? {}) }, options.timeout ?? 20, options.data ?? null);
    } catch (e) {
      if (e instanceof NetError) throw new SourceError(`Network error: ${e.reason}`);
      throw e;
    }
    if (res.status >= 400) throw new SourceError(readableError(res.status, netloc(url), sliceChars(decodeReplace(res.body), 2000)));
    return JSON.parse(decode(res.body));
  }

  private async sleepGap(last: number, gap: number) {
    const wait = gap - (this.clock.now() - last);
    if (wait > 0) await this.clock.sleep(wait);
  }

  // ---- eBay (official Browse API) -------------------------------------------------------------------------

  private async ebayAccessToken(settings: Settings): Promise<string> {
    const cid = settings.ebay_client_id as string, secret = settings.ebay_client_secret as string;
    if (!cid || !secret) throw new SourceError('eBay keys not set (Admin > eBay)');
    if (this.ebayToken.value && this.ebayToken.client === cid && this.clock.now() < this.ebayToken.expires - 60) return this.ebayToken.value;
    const basic = Buffer.from(`${cid}:${secret}`).toString('base64');
    const body = urlencode({ grant_type: 'client_credentials', scope: 'https://api.ebay.com/oauth/api_scope' });
    const tok = await this.http('https://api.ebay.com/identity/v1/oauth2/token', { data: body, headers: {
      Authorization: `Basic ${basic}`, 'Content-Type': 'application/x-www-form-urlencoded' } }) as Record<string, unknown>;
    this.ebayToken = { value: tok.access_token as string, expires: this.clock.now() + ('expires_in' in tok ? Number(tok.expires_in) : 7200), client: cid };
    return tok.access_token as string;
  }

  /** The leaf parts category eBay suggests for a search ("brake pads" -> Brake Pads). eBay only checks fitment in leaf
   * categories, not in Parts & Accessories as a whole. */
  private async ebayPartsCategory(query: string, token: string): Promise<string | null> {
    if (this.partsCategoryCache.has(query)) return this.partsCategoryCache.get(query)!;
    let found: string | null = null;
    try {
      const data = dict(await this.http(`https://api.ebay.com/commerce/taxonomy/v1/category_tree/${EBAY_MOTORS_TREE}/`
        + 'get_category_suggestions?' + urlencode({ q: query }), { headers: { Authorization: `Bearer ${token}` } }));
      for (const sug of orList(data.categorySuggestions) as Record<string, unknown>[]) {
        const ancestors = new Set((orList(sug.categoryTreeNodeAncestors) as Record<string, unknown>[]).map((a) => a.categoryId));
        if (ancestors.has(EBAY_MOTORS_PARTS)) {
          found = (orDict(sug.category).categoryId as string | undefined) ?? null;
          break;
        }
      }
    } catch (e) {
      if (!(e instanceof SourceError)) throw e;
      found = null;   // no suggestion: search without the fitment check rather than fail
    }
    this.partsCategoryCache.set(query, found);
    return found;
  }

  private async ebaySearch(watch: Watch, settings: Settings, local: boolean): Promise<Listing[]> {
    const zipCode = zipOf(settings);
    if (local && !zipCode) throw new SourceError('set your ZIP code in Settings > Local area');
    const token = await this.ebayAccessToken(settings);
    const [groups] = parseQuery(watch.query);
    const vehicle = watch.kind === 'vehicle';
    // A body style isn't in eBay titles, so a vehicle watch searches its most common models instead. eBay refuses
    // searches whose answer is too large (error 12023), so each retry asks for fewer models.
    const [body, rest] = vehicle ? bodyStyle(watch.query) : [null, ''];
    // (models, results) to try in turn while eBay answers 12023 "too large": first fewer results, then fewer models,
    // then just the body word.
    const attempts: [number | null, string][] = body ? [[8, '200'], [8, '50'], [4, '50'], [0, '50']] : [[null, vehicle ? '200' : '50'], [null, '50']];
    const keywords = (n: number | null) => {
      let gs = body ? parseQuery(rest)[0] : groups;
      if (body) gs = [...gs, n ? (EBAY_BODY_MODELS[body] ?? [body]).slice(0, n) : [body]];
      // eBay keyword syntax: (a,b) means a OR b.
      return gs.map((g) => (g.length === 1 ? g[0] : '(' + g.join(',') + ')')).join(' ');
    };
    const q = keywords(attempts[0]![0]);
    const filters: string[] = [];
    const lo = watch.min_price as number | null | undefined, hi = watch.max_price as number | null | undefined;
    if ((lo !== null && lo !== undefined) || (hi !== null && hi !== undefined)) {
      filters.push(`price:[${lo ? floatRepr(lo) : '0'}..${hi !== null && hi !== undefined ? floatRepr(hi) : ''}]`);
      filters.push('priceCurrency:USD');
    }
    const cond = ({ used: 'USED', new: 'NEW' } as Record<string, string>)[('condition' in watch ? watch.condition : 'any') as string];
    if (cond) filters.push(`conditions:{${cond}}`);
    if (!truthy(watch.include_auctions)) {
      // eBay Motors sells many vehicles as classified ads (contact the seller) rather than Buy It Now.
      filters.push('buyingOptions:{FIXED_PRICE|BEST_OFFER' + (vehicle ? '|CLASSIFIED_AD}' : '}'));
    }
    if (local) {
      // eBay requires all five pickup filters together.
      const radius = Math.max(1, radiusOf(settings));
      filters.push('deliveryOptions:{SELLER_ARRANGED_LOCAL_PICKUP}', 'pickupCountry:US', `pickupPostalCode:${zipCode}`,
        `pickupRadius:${radius}`, 'pickupRadiusUnit:mi');
    }
    const params = new Map<string, string>([['q', q], ['limit', attempts[0]![1]], ['sort', 'newlyListed']]);
    if (vehicle) params.set('category_ids', EBAY_CARS_TRUCKS);   // vehicles only, not parts that mention the model
    const fits = watch.fits as Record<string, unknown> | null | undefined;   // a parts watch linked to one of the person's vehicles
    if (truthy(fits)) {
      const leaf = await this.ebayPartsCategory(q, token);
      params.set('category_ids', leaf || EBAY_MOTORS_PARTS);
      if (leaf) params.set('compatibility_filter', `Year:${pyStr(fits!.year)};Make:${pyStr(fits!.make)};Model:${pyStr(fits!.model)}`);
    }
    if (filters.length) params.set('filter', filters.join(','));
    const headers: Record<string, string> = { Authorization: `Bearer ${token}`,
      'X-EBAY-C-MARKETPLACE-ID': (settings.ebay_marketplace as string) || 'EBAY_US' };
    if (zipCode) headers['X-EBAY-C-ENDUSERCTX'] = `contextualLocation=country%3DUS%2Czip%3D${zipCode}`;
    const url = 'https://api.ebay.com/buy/browse/v1/item_summary/search?';
    const tries = attempts.slice(1)[Symbol.iterator]();
    let data: Record<string, unknown>;
    for (;;) {
      try {
        data = dict(await this.http(url + urlencode([...params]), { headers }));
        break;
      } catch (e) {
        if (!(e instanceof SourceError)) throw e;
        if (params.has('compatibility_filter') && e.message.includes('12506')) {
          // 12506: "The category ID submitted does not support fitment." Search without the check.
          params.delete('compatibility_filter');
          this.partsCategoryCache.set(q, null);
          continue;
        }
        const smaller = e.message.includes('12023') ? tries.next().value : undefined;
        if (!smaller) throw e;
        params.set('q', keywords(smaller[0]));   // 12023: "...too large to return"
        params.set('limit', smaller[1]);
      }
    }
    const out: Listing[] = [];
    for (const it of ('itemSummaries' in data ? data.itemSummaries : []) as Record<string, unknown>[]) {
      const price = truthy(it.price) ? dict(it.price) : truthy(it.currentBidPrice) ? dict(it.currentBidPrice) : {};
      let ship: number | null = null;
      for (const opt of orList(it.shippingOptions) as Record<string, unknown>[]) {
        const cost = orDict(opt.shippingCost).value;
        if (cost !== null && cost !== undefined) {
          ship = pyFloat(cost);
          break;
        }
      }
      const loc = orDict(it.itemLocation);
      const fitsMatch = truthy(fits) && it.compatibilityMatch === 'EXACT' ? ' · fits your ' + pyStr(fits!.model) : '';
      const mayFit = truthy(fits) && it.compatibilityMatch === 'POSSIBLE' ? ' · may fit' : '';
      out.push({
        source: local ? 'ebay_local' : 'ebay',
        source_id: it.itemId,
        title: 'title' in it ? it.title : '',
        price: truthy(price.value) ? pyFloat(price.value) : null,
        shipping: ship,
        currency: 'currency' in price ? price.currency : 'USD',
        url: it.itemWebUrl ?? null,
        image: orDict(it.image).imageUrl ?? null,
        location: joinTruthy(', ', [loc.city, loc.stateOrProvince, loc.country]),
        condition: 'condition' in it ? it.condition : '',
        buying: (local ? 'local pickup · ' : '') + (('buyingOptions' in it ? it.buyingOptions : []) as string[]).join('/').toLowerCase().replace(/_/g, ' ')
          + fitsMatch + mayFit,
        // eBay keeps "For parts or not working" in the condition, not the title; junk words check this too.
        text: 'condition' in it ? it.condition : '',
      });
    }
    return out;
  }

  // ---- Reddit (r/hardwareswap, r/homelabsales) -------------------------------------------------------------

  private redditFeed(sub: string): Promise<Record<string, unknown>[]> {
    // Reddit blocks its JSON API for scripts but still serves RSS.
    const url = `https://www.reddit.com/r/${sub}/new/.rss?limit=100`;
    return this.redditLock.run(async () => {
      let root: Element;
      {
        const waitOut = this.redditState.blockedUntil - this.clock.now();
        if (waitOut > 0) throw new SourceError(`Reddit asked us to slow down; trying again in ${Math.trunc(waitOut / 60) + 1} min`);
        await this.sleepGap(this.redditState.last, REDDIT_GAP_SECONDS);
        let res: NetResponse;
        try {
          res = await this.request(url, { 'User-Agent': USER_AGENT }, 20);
        } catch (e) {
          if (e instanceof NetError) throw new SourceError(`couldn't read feed: <urlopen error ${e.reason}>`);
          throw e;
        } finally {
          this.redditState.last = this.clock.now();
        }
        if (res.status === 429) {
          this.redditState.blockedUntil = this.clock.now() + REDDIT_COOLDOWN_SECONDS;
          throw new SourceError('Reddit asked us to slow down (HTTP 429); trying again in 10 min');
        }
        if (res.status >= 400) throw new SourceError(`HTTP ${res.status} from reddit`);
        try {
          root = fromstring(decode(res.body));
        } catch (e) {
          if (e instanceof ParseError) throw new SourceError(`couldn't read feed: ${e.message}`);
          throw e;
        }
      }
      return root.findall('a:entry', ATOM).map((entry) => {
        const link = entry.find('a:link', ATOM);
        const thumb = entry.find('{http://search.yahoo.com/mrss/}thumbnail');
        const cat = entry.find('a:category', ATOM);   // which subreddit, in a combined "a+b+c" feed
        const id = entry.findtext('a:id', '', ATOM)!;
        return {
          id: id.startsWith('t3_') ? id.slice(3) : id,
          title: htmlUnescape(entry.findtext('a:title', '', ATOM)!),
          url: link ? link.get('href') : '',
          body: htmlToText(entry.findtext('a:content', '', ATOM)!),
          image: thumb ? thumb.get('url') : null,
          _sub: (cat && cat.get('term') ? cat.get('term')! : sub).toLowerCase(),
        };
      });
    });
  }

  /** One request per poll cycle for all subreddits ("r/a+b+c"), shared by every watch and Reddit source. */
  private async redditPosts(settings: Settings): Promise<Record<string, unknown>[]> {
    const combined = redditSubs(settings).join('+');
    if (!combined) return [];
    if (this.redditCache.subs === combined && this.clock.now() - this.redditCache.at < 300) return this.redditCache.posts;
    let posts: Record<string, unknown>[];
    try {
      posts = await this.redditFeed(combined);
    } catch (e) {
      if (e instanceof SourceError && this.redditCache.subs === combined && this.redditCache.posts.length) {
        return this.redditCache.posts;   // a recent good fetch beats an error while Reddit cools off
      }
      throw e;
    }
    this.redditCache = { at: this.clock.now(), posts, subs: combined };
    return posts;
  }

  private async reddit(watch: Watch, settings: Settings): Promise<Listing[]> {
    const wanted = new Set(((truthy(settings.reddit_subs) ? settings.reddit_subs : []) as string[]).map((s) => removePrefix(pyStrip(s).toLowerCase(), 'r/')));
    const out: Listing[] = [];
    for (const p of await this.redditPosts(settings)) {
      if (!wanted.has(p._sub as string)) continue;
      const selling = sellingPart((p.title as string) ?? '');
      if (selling === null) continue;
      const loc = /^\s*\[([A-Z]{2,3}(?:-[A-Z]{2})?)\]/u.exec((p.title as string) ?? '');
      out.push({
        source: 'reddit', source_id: p.id, title: p.title,
        match_text: selling,   // only the [H] half must match the query
        price: guessPrice(p.body as string, watch.query), shipping: null, currency: 'USD', url: p.url, image: p.image,
        location: loc ? loc[1] : '', condition: 'used', buying: `r/${p._sub as string}`,
        text: '',   // bodies list many items; junk words there usually refer to other items
      });
    }
    return out;
  }

  // ---- Best Buy (open-box via official API; experimental) ---------------------------------------------------

  private async bestbuy(watch: Watch, settings: Settings): Promise<Listing[]> {
    const key = settings.bestbuy_api_key as string;
    if (!truthy(key)) throw new SourceError('Best Buy API key not set (Admin > Best Buy)');
    const words = pySplit(searchTerms(watch.query)).map((w) => w.replace(/[^A-Za-z0-9]/g, '')).filter(Boolean);
    if (!words.length) return [];
    const search = words.map((w) => `search=${quote(w)}`).join('&');
    const prods = dict(await this.http(`https://api.bestbuy.com/v1/products((${search}))?apiKey=${key}&format=json&pageSize=50&show=sku,name`));
    const skus = (('products' in prods ? prods.products : []) as Record<string, unknown>[]).map((p) => pyStr(p.sku)).slice(0, 50);
    if (!skus.length) return [];
    await this.clock.sleep(0.3);   // Best Buy allows ~5 requests/second
    const ob = dict(await this.http(`https://api.bestbuy.com/beta/products/openBox(sku%20in(${skus.join(',')}))?apiKey=${key}&pageSize=100`));
    const out: Listing[] = [];
    for (const r of ('results' in ob ? ob.results : []) as Record<string, unknown>[]) {
      for (const offer of ('offers' in r ? r.offers : []) as Record<string, unknown>[]) {
        const price = orDict(offer.prices).current;
        out.push({
          source: 'bestbuy', source_id: `${pyStr(r.sku)}-${pyStr(offer.condition)}`, title: 'title' in orDict(r.names) ? orDict(r.names).title : '',
          price: truthy(price) ? pyFloat(price) : null, shipping: 0.0, currency: 'USD', url: orDict(r.links).web ?? null,
          image: orDict(r.images).standard ?? null, location: '', condition: `open box (${'condition' in offer ? pyStr(offer.condition) : '?'})`,
          buying: 'best buy', text: '',
        });
      }
    }
    return out;
  }

  // ---- Retail deal feeds (Slickdeals, r/buildapcsales) --------------------------------------------------------
  // Posts about store deals (Amazon, Newegg, Walmart, Best Buy...). The price is somewhere in the title.

  private async slickdeals(watch: Watch, settings: Settings): Promise<Listing[]> {
    const terms = searchTerms(watch.query);
    if (!terms) return [];
    const url = 'https://slickdeals.net/newsearch.php?searcharea=deals&searchin=first&rss=1&q=' + quotePlus(terms);
    let root: Element;
    let res: NetResponse;
    try {
      res = await this.request(url, { 'User-Agent': USER_AGENT }, 20);
    } catch (e) {
      if (e instanceof NetError) throw new SourceError(`couldn't read Slickdeals: <urlopen error ${e.reason}>`);
      throw e;
    }
    if (res.status >= 400) throw new SourceError(`HTTP ${res.status} from slickdeals`);
    try {
      root = fromstring(decode(res.body));
    } catch (e) {
      if (e instanceof ParseError) throw new SourceError(`couldn't read Slickdeals: ${e.message}`);
      throw e;
    }
    // Search results reach back years; only recent deals might still be live.
    const maxAge = pyFloat(truthy(settings.deal_max_age_days) ? settings.deal_max_age_days : 14) * 86400;
    const out: Listing[] = [];
    for (const it of root.findall('./channel/item')) {
      let posted: number;
      try {
        posted = parsedateTimestamp(it.findtext('pubDate') || '');
      } catch (e) {
        if (!(e instanceof DateValueError)) throw e;
        posted = this.clock.now();
      }
      if (this.clock.now() - posted > maxAge) continue;
      const title = pyStrip(htmlUnescape(it.findtext('title') || ''));
      const content = it.findtext('{http://purl.org/rss/1.0/modules/content/}encoded') || '';
      const img = /<img[^>]+src="([^"]+)"/.exec(content);
      out.push({
        source: 'slickdeals', source_id: removePrefix(it.findtext('guid') || it.findtext('link') || title, 'thread-'), title,
        price: firstPrice(title), shipping: null, currency: 'USD', url: it.findtext('link'), image: img ? htmlUnescape(img[1]!) : null,
        location: '', condition: dealCondition(title), buying: 'slickdeals', text: '',
      });
    }
    return out;
  }

  private async buildapcsales(_watch: Watch, settings: Settings): Promise<Listing[]> {
    return (await this.redditPosts(settings)).filter((p) => p._sub === 'buildapcsales').map((p) => ({
      source: 'buildapcsales', source_id: p.id, title: p.title,
      price: firstPrice(p.title as string),   // "[GPU] Zotac RTX 3060 12GB - $249.99 (Newegg)"
      shipping: null, currency: 'USD', url: p.url, image: p.image, location: '', condition: dealCondition(p.title as string),
      buying: 'r/buildapcsales', text: '',
    }));
  }

  // ---- KSL Classifieds (Utah; local pickup) -------------------------------------------------------------------
  // No API or RSS. The search page carries its results as JSON inside the Next.js payload. KSL runs bot protection,
  // so: one request per watch per cycle, a few seconds apart, with ordinary browser headers.

  /** A page from KSL, KSL Cars, Poshmark or OfferUp, with their shared error wording. */
  private async page(url: string, headers: Record<string, string>, who: string, last: (at: number) => void, timeout = 25): Promise<string> {
    let res: NetResponse;
    try {
      res = await this.request(url, headers, timeout);
    } catch (e) {
      last(this.clock.now());
      if (e instanceof NetError) throw new SourceError(`Network error reaching ${who}: ${e.reason}`);
      throw e;
    }
    last(this.clock.now());
    if (res.status >= 400) {
      const site = who === 'KSL Cars' ? 'KSL' : who;
      if (res.status === 403 || res.status === 429) {
        throw new SourceError(`${site}'s bot protection blocked the request (HTTP ${res.status}); it usually clears on a later check`);
      }
      throw new SourceError(`HTTP ${res.status} from ${who}`);
    }
    return decodeReplace(res.body);
  }

  private async ksl(watch: Watch, settings: Settings): Promise<Listing[]> {
    const zipCode = zipOf(settings);
    if (!zipCode) throw new SourceError('set your ZIP code in Settings > Local area');
    const terms = searchTerms(watch.query);
    if (!terms) return [];
    const radius = Math.max(1, radiusOf(settings));
    const url = `https://classifieds.ksl.com/search/keyword/${quote(terms, '')}/zip/${quote(zipCode, '')}/miles/${radius}`;
    await this.sleepGap(this.kslLast, KSL_GAP_SECONDS);
    const page = await this.page(url, KSL_HEADERS, 'KSL', (at) => { this.kslLast = at; });
    if (page.includes('Access to this page has been denied')) throw new SourceError("KSL's bot protection blocked the request; it usually clears on a later check");
    const out: Listing[] = [];
    for (const r of kslResults(page)) {
      if (r.marketType !== 'Sale') continue;   // skip wanted / rental / service posts
      const loc = orDict(r.location);
      const seller = (truthy(r.sellerType) ? pyStr(r.sellerType) : '').toLowerCase();
      out.push({
        source: 'ksl', source_id: pyStr(r.id), title: truthy(r.title) ? r.title : '', price: truthy(r.price) ? pyFloat(r.price) : null,
        shipping: 0.0, currency: 'USD', url: `https://classifieds.ksl.com/listing/${pyStr(r.id)}`, image: orDict(r.primaryImage).url ?? null,
        location: joinTruthy(', ', [loc.city, loc.state]), condition: 'used', buying: seller ? `KSL · ${seller} seller` : 'KSL', text: '',
      });
    }
    return out;
  }

  // ---- KSL Cars (Utah; dealers and private sellers) ---------------------------------------------------------

  /** The id of the page's "more results" server action, found in its JavaScript (changes when KSL deploys). */
  private async kslActionId(page: string): Promise<string | null> {
    if (this.kslAction) return this.kslAction;
    const sources = [...new Set([...page.matchAll(/(https:\/\/[a-z.-]*ksl\.com\/_next\/static\/chunks\/[\w./-]+\.js)/g)].map((m) => m[1]!))];
    for (const src of sources) {
      let js: string;
      try {
        const res = await this.request(src, KSL_HEADERS, 20);
        if (res.status >= 400) continue;
        js = decodeReplace(res.body);
      } catch (e) {
        if (e instanceof NetError) continue;
        throw e;
      }
      const m = /createServerReference\)\("([0-9a-f]{40,44})",[^)]*"search"\)/.exec(js);
      if (m) {
        this.kslAction = m[1]!;
        return m[1]!;
      }
    }
    return null;
  }

  private async kslMore(url: string, action: string, params: unknown, cursor: unknown): Promise<[Record<string, unknown>[], Record<string, unknown>]> {
    const body = pyJsonDumps([null, '0', { featuredCount: 1, featuredPosition: 0, spotlightCount: 1, spotlightPosition: 10, listingCount: 18 },
      params, cursor]);
    const res = await this.request(url, { ...KSL_HEADERS, Accept: 'text/x-component', 'Content-Type': 'text/plain;charset=UTF-8',
      'Next-Action': action, Origin: 'https://cars.ksl.com', Referer: url }, 25, body);
    if (res.status >= 400) throw new NetError(`HTTP Error ${res.status}`);
    for (const line of splitlines(decodeReplace(res.body))) {
      if (line.startsWith('1:')) {
        const data = dict(JSON.parse(line.slice(2)));
        return [(truthy(data.items) ? data.items : []) as Record<string, unknown>[], (truthy(data.pageInfo) ? data.pageInfo : {}) as Record<string, unknown>];
      }
    }
    throw new JSONDecodeError("no results in KSL's answer");
  }

  private async kslCars(watch: Watch, settings: Settings): Promise<Listing[]> {
    const zipCode = zipOf(settings);
    if (!zipCode) throw new SourceError('set your ZIP code in Settings > Local area');
    const [body, rest] = bodyStyle(watch.query);
    const terms = searchTerms(body ? rest : watch.query);
    if (!terms && !body) return [];
    const radius = Math.max(1, radiusOf(settings));
    const parts: [string, string][] = terms ? [['keyword', terms]] : [];
    if (body && Object.hasOwn(KSL_BODY, body)) parts.push(['body', KSL_BODY[body]!]);
    for (const [key, field] of [['yearFrom', 'year_min'], ['yearTo', 'year_max'], ['mileageTo', 'max_miles'], ['priceFrom', 'min_price'], ['priceTo', 'max_price']]) {
      if (truthy(watch[field!])) parts.push([key!, String(Math.ceil(Number(watch[field!])))]);
    }
    parts.push(['zip', zipCode], ['miles', String(radius)]);
    const url = 'https://cars.ksl.com/search/' + parts.map(([k, v]) => `${k}/${quote(v, '')}`).join('/');
    await this.sleepGap(this.kslLast, KSL_GAP_SECONDS);
    const page = await this.page(url, KSL_HEADERS, 'KSL Cars', (at) => { this.kslLast = at; });   // shares the gap with KSL Classifieds
    let rows = kslResults(page);
    // Later pages come from the page's own "more results" action. If KSL changes it, page 1 still counts.
    let [params, info] = kslPageInfo(kslFlight(page));
    for (let n = 0; n < KSL_CARS_PAGES - 1; n++) {
      if (!(truthy(params) && truthy(info.hasNextPage) && truthy(info.endCursor))) break;
      const action = await this.kslActionId(page);
      if (!action) break;
      await this.clock.sleep(KSL_GAP_SECONDS);
      let more: Record<string, unknown>[];
      try {
        [more, info] = await this.kslMore(url, action, params, info.endCursor);
      } catch (e) {
        this.kslLast = this.clock.now();
        if (e instanceof NetError || e instanceof SyntaxError || e instanceof JSONDecodeError) {
          this.kslAction = null;   // look it up again next time (KSL redeployed?)
          break;
        }
        throw e;
      }
      this.kslLast = this.clock.now();
      rows = [...rows, ...more];
    }
    const out: Listing[] = [];
    const seen = new Set<unknown>();
    for (const r of rows) {
      if (!truthy(r.id) || seen.has(r.id) || ('listingType' in r ? r.listingType : 'CAR') !== 'CAR') continue;
      seen.add(r.id);
      const loc = orDict(r.location);
      const dealer = orDict(r.dealer).name;
      const seller = (truthy(r.sellerType) ? pyStr(r.sellerType) : '').toLowerCase().startsWith('dealer') ? 'dealer' : 'private seller';
      const title = truthy(r.title) ? r.title as string : joinTruthy(' ', [r.makeYear, r.make, r.model, r.trim]);
      out.push({
        source: 'ksl_cars', source_id: pyStr(r.id), title,
        match_text: body ? `${title} ${body}` : title,   // KSL already filtered by body style
        price: truthy(r.price) ? pyFloat(r.price) : null, shipping: 0.0, currency: 'USD', url: `https://cars.ksl.com/listing/${pyStr(r.id)}`,
        image: orDict(r.primaryImage).url ?? null, location: joinTruthy(', ', [loc.city, loc.state]),
        condition: (truthy(r.newUsed) ? pyStr(r.newUsed) : 'used').toLowerCase(),
        buying: truthy(dealer) ? `KSL Cars · ${pyStr(dealer)}` : `KSL Cars · ${seller}`, text: '',
        year: r.makeYear ?? null, make: truthy(r.make) ? r.make : '', miles: truthy(r.mileage) ? r.mileage : null,   // private sellers sometimes leave 0
      });
    }
    return out;
  }

  // ---- Poshmark (clothes, shoes; ships anywhere) ----------------------------------------------------------------

  private async poshmark(watch: Watch, _settings: Settings): Promise<Listing[]> {
    const terms = searchTerms(watch.query);
    if (!terms) return [];
    const params: [string, string][] = [['query', terms], ['type', 'listings'], ['sort_by', 'added_desc']];
    if (truthy(watch.department)) params.push(['department', pyTitle(String(watch.department))]);
    const size = normSize(watch.size as string | null);
    if (size) params.push(['size[]', size.split('x')[0]!]);   // Poshmark lists pants by waist
    await this.sleepGap(this.poshmarkLast, POSHMARK_GAP_SECONDS);
    const page = await this.page('https://poshmark.com/search?' + urlencode(params), KSL_HEADERS, 'Poshmark', (at) => { this.poshmarkLast = at; });
    const out: Listing[] = [];
    for (const r of poshmarkListings(page)) {
      if (!truthy(r.id) || ('status' in orDict(r.inventory) ? orDict(r.inventory).status : 'available') !== 'available') continue;
      let price: number | null;
      try {
        const val = orDict(r.price_amount).val;
        if (val === null || val === undefined) throw new TypeError();
        price = pyFloat(val);
      } catch {
        price = null;
      }
      const dept = truthy(orDict(r.department).display) ? orDict(r.department).display : '';
      out.push({
        source: 'poshmark', source_id: r.id, title: truthy(r.title) ? r.title : '', price,
        shipping: null,   // Poshmark adds its flat shipping at checkout
        currency: 'USD', url: `https://poshmark.com/listing/${pyStr(r.id)}`, image: r.picture_url ?? null, location: '',
        condition: Object.hasOwn(POSHMARK_CONDITIONS, r.condition as string) ? POSHMARK_CONDITIONS[r.condition as string] : 'used',
        buying: joinTruthy(' · ', ['Poshmark', truthy(r.brand) ? r.brand : '', dept]), text: '', size: truthy(r.size) ? r.size : '',
      });
    }
    return out;
  }

  // ---- Craigslist (local; the search page's own JSON API) -----------------------------------------------------
  // sapi.craigslist.org answers the search page with packed rows: offsets from a base id/date, the price,
  // "area:place~lat~lon", then tagged lists ([4, images...], [6, slug], [13, uuid]) and the title last. Searches spill
  // into nearby areas, so rows farther than the radius are dropped here.

  private async craigslistSearch(zipCode: string, radius: number, query: string, lo: unknown = null, hi: unknown = null,
    vehicle: Record<string, unknown> | null = null): Promise<Record<string, unknown>> {
    const params = new Map<string, string | string[]>([['batch', '1-0-360-0-0'], ['cc', 'US'], ['lang', 'en'],
      ['searchPath', vehicle ? 'cta' : 'sss'], ['postal', zipCode], ['search_distance', String(radius)], ['query', query]]);
    for (const [key, field] of [['min_auto_year', 'year_min'], ['max_auto_year', 'year_max'], ['max_auto_miles', 'max_miles']]) {
      if (vehicle && truthy(vehicle[field!])) params.set(key!, pyStr(vehicle[field!]));
    }
    if (vehicle && Object.hasOwn(CRAIGSLIST_BODY, vehicle._body as string)) params.set('auto_bodytype', CRAIGSLIST_BODY[vehicle._body as string]!);
    if (!query) params.delete('query');
    if (truthy(lo)) params.set('min_price', String(Math.trunc(Number(lo))));
    if (truthy(hi)) params.set('max_price', String(Math.ceil(Number(hi))));
    await this.sleepGap(this.craigslistLast, CRAIGSLIST_GAP_SECONDS);
    let raw: unknown;
    try {
      raw = await this.http(CRAIGSLIST_URL + '?' + urlencode([...params], true),
        { headers: { ...KSL_HEADERS, Accept: 'application/json', Referer: 'https://www.craigslist.org/' } });
    } finally {
      this.craigslistLast = this.clock.now();
    }
    const data = orDict(dict(raw).data);
    const loc = orDict(data.location);
    if (loc.lat !== null && loc.lat !== undefined) {
      this.places.set(zipCode, { lat: loc.lat as number, lon: loc.lon as number, city: ('city' in loc ? loc.city : '') as string,
        state: ('region' in loc ? loc.region : '') as string });
    }
    return data;
  }

  /** Where a ZIP is (lat/lon, city, state), asked of Craigslist once and remembered. */
  async zipPlace(zipCode: string) {
    if (!this.places.has(zipCode)) await this.craigslistSearch(zipCode, 1, '');
    const place = this.places.get(zipCode);
    if (!place) throw new SourceError(`couldn't find where ZIP ${zipCode} is`);
    return place;
  }

  private async craigslist(watch: Watch, settings: Settings): Promise<Listing[]> {
    const zipCode = zipOf(settings);
    if (!zipCode) throw new SourceError('set your ZIP code in Settings > Local area');
    let terms = searchTerms(watch.query);
    if (!terms) return [];
    const radius = Math.max(1, radiusOf(settings));
    let vehicle: Record<string, unknown> | null = null;
    if (watch.kind === 'vehicle') {   // cars+trucks section, year/miles/body filters
      const [body, rest] = bodyStyle(watch.query);
      vehicle = { ...watch, _body: body };
      if (body) terms = searchTerms(rest);
    }
    const data = await this.craigslistSearch(zipCode, radius, terms, watch.min_price, watch.max_price, vehicle);
    const decodeInfo = orDict(data.decode), home = orDict(data.location);
    if (Object.keys(data).length && !('minPostingId' in decodeInfo) && truthy(data.items)) {
      throw new SourceError("couldn't read Craigslist's results (did the site change?)");
    }
    const out: Listing[] = [];
    for (const raw of (truthy(data.items) ? data.items : []) as unknown[][]) {
      const r = craigslistRow(raw, decodeInfo);
      if (!r || !r.title) continue;
      if (home.lat !== null && home.lat !== undefined && miles(home.lat as number, home.lon as number, r.lat, r.lon) > radius) continue;
      // Python's images[0].split(":", 1)[-1]: what follows the first colon, or all of it without one.
      const first = r.images.length ? String(r.images[0]) : '';
      const img = r.images.length ? (first.includes(':') ? first.slice(first.indexOf(':') + 1) : first) : null;
      out.push({
        source: 'craigslist', source_id: String(r.id), title: r.title,
        match_text: vehicle && truthy(vehicle._body) ? `${r.title} ${vehicle._body as string}` : r.title,
        price: r.price !== null ? Number(r.price) : null, shipping: 0.0, currency: 'USD',
        url: r.uuid ? `https://www.craigslist.org/view/d/${pyStr(r.slug)}/${pyStr(r.uuid)}` : `https://www.craigslist.org/search/sss?query=${quote(terms)}`,
        image: img ? `https://images.craigslist.org/${img}_300x300.jpg` : null,
        location: [...r.place].length <= 40 ? r.place : '',   // some sellers put ads in this field
        condition: 'used',
        buying: 'Craigslist' + (vehicle ? ({ 145: ' · owner', 146: ' · dealer' } as Record<number, string>)[raw[2] as number] ?? '' : ''),
        text: '',
        miles: Number.isInteger(r.odometer) ? r.odometer : null,
      });
    }
    return out;
  }

  // ---- OfferUp (local; the search page's Next.js data) --------------------------------------------------------
  // No public API. The search page carries its results in __NEXT_DATA__; the area comes from the ou.location cookie
  // (otherwise OfferUp guesses from the IP), and radius only takes 5/10/20/30/50 miles.

  private async offerup(watch: Watch, settings: Settings): Promise<Listing[]> {
    const zipCode = zipOf(settings);
    if (!zipCode) throw new SourceError('set your ZIP code in Settings > Local area');
    const terms = searchTerms(watch.query);
    if (!terms) return [];
    const place = await this.zipPlace(zipCode);
    let radius = radiusOf(settings);
    const vehicle = watch.kind === 'vehicle';
    const radii = vehicle ? OFFERUP_VEHICLE_RADII : OFFERUP_RADII;
    radius = radii.find((r) => r >= radius) ?? radii.at(-1)!;
    const params = new Map<string, string>([['q', terms], ['radius', String(radius)], ['sort', '-posted']]);
    if (vehicle) {
      if (truthy(watch.year_min)) params.set('veh_year_min', pyStr(watch.year_min));
      if (truthy(watch.year_max)) params.set('veh_year_max', pyStr(watch.year_max));
      const bucket = OFFERUP_MILEAGE.find((m) => truthy(watch.max_miles) && m >= Number(watch.max_miles));
      if (bucket) params.set('veh_mileage', String(bucket));
    }
    if (truthy(watch.min_price)) params.set('price_min', String(Math.trunc(Number(watch.min_price))));
    if (truthy(watch.max_price)) params.set('price_max', String(Math.ceil(Number(watch.max_price))));
    const cookie = pyJsonDumps({ city: place.city, state: place.state, zipCode, longitude: place.lon, latitude: place.lat, source: 'manual' }, [',', ':']);
    await this.sleepGap(this.offerupLast, OFFERUP_GAP_SECONDS);
    const page = await this.page('https://offerup.com/search?' + urlencode([...params]),
      { ...KSL_HEADERS, Cookie: 'ou.location=' + quote(cookie) }, 'OfferUp', (at) => { this.offerupLast = at; });
    const out: Listing[] = [];
    for (const r of offerupListings(page)) {
      let price: number | null;
      try {
        price = r.price !== null && r.price !== undefined && r.price !== '' ? pyFloat(r.price) : null;
      } catch {
        price = null;
      }
      if (price !== null && price <= 1) price = null;   // "$1" / "$0" is a placeholder for "make an offer"
      const flags = (truthy(r.flags) ? r.flags : []) as string[];
      out.push({
        source: 'offerup', source_id: r.listingId, title: truthy(r.title) ? r.title : '', price,
        shipping: flags.includes('LOCAL_PICKUP') ? 0.0 : null, currency: 'USD', url: `https://offerup.com/item/detail/${pyStr(r.listingId)}`,
        image: orDict(r.image).url ?? null, location: truthy(r.locationName) ? r.locationName : '',
        condition: (truthy(r.conditionText) ? pyStr(r.conditionText) : 'used').toLowerCase(),
        buying: 'OfferUp' + (flags.includes('SHIPPING') ? ' · ships' : ''), text: '', miles: r.vehicleMiles ?? null,
      });
    }
    return out;
  }
}

// ---- reading pages (no network) ------------------------------------------------------------------------------

const removePrefix = (s: string, prefix: string) => (s.startsWith(prefix) ? s.slice(prefix.length) : s);
/** bytes.decode("utf-8", "replace"). */
const decodeReplace = (b: Buffer) => new TextDecoder('utf-8', { fatal: false, ignoreBOM: true }).decode(b);

function htmlToText(fragment: string): string {
  const text = fragment.replace(/<br\s*\/?>|<\/p>|<\/li>|<\/tr>/gi, '\n').replace(/<[^>]+>/g, ' ');
  return htmlUnescape(text);
}

/** Every subreddit any Reddit source needs, so one combined request covers them all. */
export function redditSubs(settings: Settings): string[] {
  const subs = ((truthy(settings.reddit_subs) ? settings.reddit_subs : []) as string[]).filter((s) => pyStrip(s))
    .map((s) => removePrefix(pyStrip(s).toLowerCase(), 'r/'));
  if ((truthy(settings.sources_enabled) ? dict(settings.sources_enabled) : {}).buildapcsales ?? true) subs.push('buildapcsales');
  return [...new Set(subs)];
}

/** The part of a swap title describing what's for sale, or null if it's a buy post. */
export function sellingPart(title: string): string | null {
  const t = title.toLowerCase();
  const m = /\[h\](.*?)\[w\](.*)/.exec(t);
  if (m) return /paypal|cash|venmo|zelle|money|\$/.test(m[2]!) ? m[1]! : null;   // [W] PayPal / cash: selling what's in [H]
  if (t.includes('[fs]') || t.includes('[for sale]')) return t;
  return null;
}

/** A price on the line of the post body that mentions the searched item. */
export function guessPrice(body: string, query: string): number | null {
  const [groups] = parseQuery(query);
  const key = groups.length ? groups[0]!.map((a) => a.toLowerCase()) : [];
  for (const line of splitlines(body)) {
    const low = line.toLowerCase().replace(/-/g, '').replace(/ /g, '');
    if (key.length && !key.some((k) => low.includes(k.replace(/-/g, '').replace(/ /g, '')))) continue;
    const m = PRICE_RE.exec(line);
    if (m) return pyFloat((m[1] ?? m[2]!).replace(/,/g, ''));
  }
  return null;
}

export function firstPrice(text: string): number | null {
  const m = DEAL_PRICE_RE.exec(text);
  return m ? pyFloat(m[1]!.replace(/,/g, '') + (m[2] ?? '')) : null;
}

export function dealCondition(title: string): string {
  const low = title.toLowerCase();
  return DEAL_CONDITIONS.find(([word]) => low.includes(word))?.[1] ?? 'new';
}

const NEXT_PUSH = /self\.__next_f\.push\(\[1,"([\s\S]*?)"\]\)/g;

function kslResults(page: string): Record<string, unknown>[] {
  for (const [, chunk] of page.matchAll(NEXT_PUSH)) {
    if (!chunk!.includes('SearchStoreProvider')) continue;
    const text = JSON.parse(`"${chunk}"`) as string;   // it's a JS string literal holding the payload
    const at = text.indexOf('"results":');
    if (at < 0) continue;
    const [results] = rawDecode(text, at + '"results":'.length);
    // A list of pages, each a list of listings.
    return (results as unknown[]).flatMap((p) => (Array.isArray(p) ? p : [p])) as Record<string, unknown>[];
  }
  throw new SourceError("couldn't find listings on the KSL page (did the site change?)");
}

/** The page's Next.js payload, joined back into one string. */
function kslFlight(page: string): string {
  const out: string[] = [];
  for (const [, chunk] of page.matchAll(NEXT_PUSH)) {
    try {
      out.push(JSON.parse(`"${chunk}"`) as string);
    } catch {
      continue;
    }
  }
  return out.join('');
}

/** [searchParams for asking for more pages, pageInfo {hasNextPage, endCursor, total}]. */
function kslPageInfo(flight: string): [unknown, Record<string, unknown>] {
  let params: unknown = null;
  let info: Record<string, unknown> = {};
  let at = flight.indexOf('"searchParams":');
  if (at >= 0) {
    try {
      [params] = rawDecode(flight, at + '"searchParams":'.length);
    } catch {
      params = null;
    }
  }
  at = flight.indexOf('"pageInfo":');
  if (at >= 0) {
    try {
      const [pi] = rawDecode(flight, at + '"pageInfo":'.length);
      const first = Array.isArray(pi) && pi.length ? pi[0] : pi;
      info = (truthy(first) ? first : {}) as Record<string, unknown>;
    } catch {
      info = {};
    }
  }
  return [params, info];
}

function poshmarkListings(page: string): Record<string, unknown>[] {
  const at = page.indexOf('window.__INITIAL_STATE__');
  if (at < 0) throw new SourceError("couldn't find listings on the Poshmark page (did the site change?)");
  try {
    const brace = page.indexOf('{', at);
    if (brace < 0) throw new JSONDecodeError('substring not found');
    const [state] = rawDecode(page, brace);
    const data = (((state as Record<string, unknown>).$_search as Record<string, unknown>).gridData as Record<string, unknown>).data;
    return (truthy(data) ? data : []) as Record<string, unknown>[];
  } catch {
    throw new SourceError("couldn't read the Poshmark page (did the site change?)");
  }
}

interface CraigslistRow { id: number; price: unknown; lat: number; lon: number; place: string; images: unknown[]; slug: unknown; uuid: unknown;
  odometer: unknown; title: string }

/** One packed Craigslist row, or null for a row in a shape this doesn't know (skipped rather than failing the search). */
function craigslistRow(row: unknown[], decodeInfo: Record<string, unknown>): CraigslistRow | null {
  try {
    if (!Array.isArray(row)) return null;
    const tags = new Map<unknown, unknown[]>();
    for (const r of row.slice(6, -1)) if (Array.isArray(r) && r.length) tags.set(r[0], r.slice(1));
    if (typeof row[4] !== 'string') return null;
    const m = /^(\d+):(\d+)~([-\d.]+)~([-\d.]+)/.exec(row[4]);
    if (!m) return null;
    const places = (truthy(decodeInfo.locationDescriptions) ? decodeInfo.locationDescriptions : []) as unknown[];
    const add = (base: unknown, offset: unknown) => {
      if (typeof base !== 'number' || typeof offset !== 'number') throw new TypeError();
      return base + offset;
    };
    if (!('minPostingId' in decodeInfo) || !('minPostedDate' in decodeInfo)) return null;
    const id = add(decodeInfo.minPostingId, row[0]);
    add(decodeInfo.minPostedDate, row[1]);
    if (typeof row[3] !== 'number') return null;
    const placeIndex = Number(m[2]);
    return {
      id, price: row[3] >= 0 ? row[3] : null, lat: pyFloat(m[3]!), lon: pyFloat(m[4]!),
      place: placeIndex < places.length && truthy(places[placeIndex]) ? places[placeIndex] as string : '',
      images: tags.get(4) ?? [], slug: (truthy(tags.get(6)) ? tags.get(6)! : [''])[0], uuid: (truthy(tags.get(13)) ? tags.get(13)! : [''])[0],
      odometer: (truthy(tags.get(9)) ? tags.get(9)! : [null])[0],   // cars and trucks only
      title: typeof row.at(-1) === 'string' ? row.at(-1) as string : '',
    };
  } catch {
    return null;
  }
}

function miles(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const rad = Math.PI / 180;
  const p1 = lat1 * rad, p2 = lat2 * rad;
  const a = Math.sin((p2 - p1) / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin((lon2 - lon1) * rad / 2) ** 2;
  return 3958.8 * 2 * Math.asin(Math.sqrt(a));
}

function offerupListings(page: string): Record<string, unknown>[] {
  const m = /<script id="__NEXT_DATA__"[^>]*>([\s\S]*?)<\/script>/.exec(page);
  if (!m) throw new SourceError("couldn't find listings on the OfferUp page (did the site change?)");
  const feed = dict(dict(dict(JSON.parse(m[1]!)).props).pageProps).searchFeedResponse;
  if (feed === null || feed === undefined) throw new SourceError("couldn't find listings on the OfferUp page (did the site change?)");
  const found = new Map<unknown, Record<string, unknown>>();
  const walk = (node: unknown) => {
    if (node && typeof node === 'object' && !Array.isArray(node)) {
      const n = node as Record<string, unknown>;
      if (n.__typename === 'ModularFeedListing' && truthy(n.listingId) && !found.has(n.listingId)) found.set(n.listingId, n);
      for (const v of Object.values(n)) walk(v);
    } else if (Array.isArray(node)) {
      for (const v of node) walk(v);
    }
  };
  walk(feed);   // listings sit in looseTiles and inside modules
  return [...found.values()];
}

// Sources that see the same items under the same ids; a listing is stored once per family.
const SOURCE_FAMILY: Record<string, string> = { ebay_local: 'ebay' };

export function family(source: string): string[] {
  const root = SOURCE_FAMILY[source] ?? source;
  return [root, ...Object.entries(SOURCE_FAMILY).filter(([, r]) => r === root).map(([s]) => s)];
}

export const SOURCE_NAMES = ['ebay', 'ebay_local', 'reddit', 'bestbuy', 'slickdeals', 'buildapcsales', 'ksl', 'craigslist', 'offerup', 'ksl_cars', 'poshmark'];
