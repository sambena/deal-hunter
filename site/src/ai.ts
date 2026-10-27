// The optional AI helpers: a port of the Python app's ai.py and shared_ai.py. Only runs when someone presses an AI
// button, so it never spends anything in the background. Every paid request goes through one gate:
//   1. estimate the cost from the prompt size and the model's price,
//   2. refuse if the worst case would push this month's spend past the limit,
//   3. call the provider and record what it actually billed.
// Whose AI: the admin's AI (provider, model, key and monthly limit) comes only from the owner console's shared settings
// file, never from the database or the environment; with no file or no entry it is off. Other people either use their
// own key ("own") or the admin's AI ("shared"), within limits the admin sets per person: requests a day, and dollars a
// month of paid AI. The admin's monthly limit covers everything run on the admin's key, by anyone.
// Providers: Claude (Anthropic's Messages API), OpenAI and Google Gemini, over the injected network, so tests never call a paid API.
import { readFileSync, statSync } from 'node:fs';
import type { Db } from '@cougarcave/server';
import { sql } from 'drizzle-orm';
import { pyJsonDumps } from './logic/py-text.js';
import { pyFixed, pyRepr, pyRound, pyStr, pyStrip, truthy } from './logic/py.js';
import { type Net, NetError } from './logic/sources.js';
import type { Store } from './store.js';

type Row = Record<string, unknown>;
const rowsOf = (result: unknown): Row[] => (Array.isArray(result) ? result : (result as { rows?: Row[] }).rows ?? []);

export class AIError extends Error {}

interface Model { provider: string; id: string; family: string; gen: number[]; label: string; in: number; out: number; effort: boolean;
  long?: { over: number; in: number; out: number }; promo?: { until: string; in: number; out: number } }

// US dollars per million tokens, checked 2026-10-04. Thinking/reasoning tokens bill as output. `family` and `gen`
// (compared as numbers, newest wins): how the owner console's tier picks a model (resolve()). `effort`: the model
// takes a reasoning-effort setting (the console's, or the lowest useful one).
export const MODELS: Model[] = [
  { provider: 'claude', id: 'claude-opus-5-5', family: 'opus', gen: [5, 5], label: 'Claude Opus 5.5 (best)', in: 4.00, out: 20.00, effort: true },
  { provider: 'claude', id: 'claude-sonnet-5-5', family: 'sonnet', gen: [5, 5], label: 'Claude Sonnet 5.5', in: 2.00, out: 10.00, effort: true },
  // Checked 2026-10-25 on Anthropic's pricing page: a prompt over 100,000 tokens pays the higher row.
  { provider: 'claude', id: 'claude-haiku-5-5', family: 'haiku', gen: [5, 5], label: 'Claude Haiku 5.5 (cheapest)', in: 0.10, out: 0.50, effort: true,
    long: { over: 100_000, in: 0.50, out: 2.50 } },
  { provider: 'claude', id: 'claude-haiku-4-5', family: 'haiku', gen: [4, 5], label: 'Claude Haiku 4.5', in: 1.00, out: 5.00, effort: false },
  { provider: 'claude', id: 'claude-opus-5', family: 'opus', gen: [5], label: 'Claude Opus 5 (older)', in: 5.00, out: 25.00, effort: true },
  { provider: 'openai', id: 'gpt-6-astra', family: 'astra', gen: [6], label: 'GPT-6 Astra (best)', in: 10.00, out: 50.00, effort: true },
  { provider: 'openai', id: 'gpt-6.1-sol', family: 'sol', gen: [6, 1], label: 'GPT-6.1 Sol', in: 2.00, out: 10.00, effort: true },
  { provider: 'openai', id: 'gpt-6-luna', family: 'luna', gen: [6], label: 'GPT-6 Luna (cheapest)', in: 0.10, out: 0.50, effort: true },
  { provider: 'gemini', id: 'gemini-3.1-pro-preview', family: 'pro', gen: [3, 1], label: 'Gemini 3.1 Pro (best, preview)', in: 2.00, out: 12.00, effort: true },
  // Half price until the end of 2026, then $1.50 / $7.50.
  { provider: 'gemini', id: 'gemini-3.8-flash', family: 'flash', gen: [3, 8], label: 'Gemini 3.8 Flash', in: 1.50, out: 7.50, effort: true,
    promo: { until: '2027-01-01', in: 0.75, out: 3.75 } },
  { provider: 'gemini', id: 'gemini-3.5-flash-lite', family: 'flash-lite', gen: [3, 5], label: 'Gemini 3.5 Flash-Lite (cheapest)', in: 0.30, out: 2.50, effort: true },
];

export const PROVIDERS: Record<string, { label: string; key: string; model: string }> = {
  claude: { label: 'Claude (Anthropic)', key: 'anthropic_api_key', model: 'claude_model' },
  openai: { label: 'OpenAI (ChatGPT)', key: 'openai_api_key', model: 'openai_model' },
  gemini: { label: 'Google Gemini', key: 'gemini_api_key', model: 'gemini_model' },
};

// Token allowances per button. `max_out` is a hard cap sent to the provider, so it also caps the cost.
export const ACTIONS: Record<string, { label: string; typical_out: number; max_out: number }> = {
  // typical_out: first real Haiku answer was 60 tokens; 250 leaves room for models that think a little.
  judge: { label: 'Ask AI', typical_out: 250, max_out: 3000 },
  draft: { label: 'Suggest watches', typical_out: 2500, max_out: 8000 },
  upgrades: { label: 'Find upgrades with AI', typical_out: 3000, max_out: 8000 },
  specs: { label: 'Read specs with AI', typical_out: 300, max_out: 2000 },
  radar: { label: 'Deal radar', typical_out: 2500, max_out: 8000 },
  // Up to REVIEW_MAX finds in one request, about 60 tokens of answer each.
  review: { label: 'AI review of finds', typical_out: 2500, max_out: 9000 },
  tune: { label: 'Improve a watch with AI', typical_out: 500, max_out: 3000 },
};
export const REVIEW_MAX = 40;

export const AI_KEYS = ['ai_provider', 'anthropic_api_key', 'claude_model', 'openai_api_key', 'openai_model', 'gemini_api_key',
  'gemini_model', 'gemini_free_tier'];
// Which family the console's "balanced" and "best" tiers mean. OpenAI's mini and plain GPT models aren't in MODELS, so
// for OpenAI those tiers fall back to cheapest.
const TIERS: Record<string, Record<string, string>> = { claude: { balanced: 'sonnet', best: 'opus' }, openai: { balanced: 'mini', best: 'plain' },
  gemini: { balanced: 'flash', best: 'pro' } };
const MANAGED_OFF = "The admin's AI is off in the owner console.";

// ---- the owner console's shared settings file (shared_ai.py) -----------------------------------------------------

const SHARED_APP = 'deals';
const SHARED_PROVIDERS: Record<string, string> = { anthropic: 'claude', openai: 'openai', gemini: 'gemini' };
const EFFORTS = ['low', 'medium', 'high'];

export interface Shared { provider: string; model: string | null; tier: string | null; effort: string | null; monthly_usd: number; key: string }

/** This app's AI from the shared file, or null (off). Read again only when the file changes. The same rules as
 * shared_ai.py: version 1, an entry with ai: true, a known provider, a monthly limit of 0-1000 and a key; an unknown
 * tier or effort is ignored, never an error. */
export function loadShared(where: string, log: (line: string) => void = () => {}): Shared | null {
  let reason = '';
  let result: Shared | null = null;
  let data: unknown;
  try {
    statSync(where);
    try {
      data = JSON.parse(readFileSync(where, 'utf8'));
    } catch (e) {
      reason = e instanceof SyntaxError ? "the AI settings file isn't valid JSON" : "the AI settings file can't be read";
    }
  } catch {
    reason = 'the AI settings file is missing';
  }
  if (!reason) {
    const file = data as Row;
    const apps = file && typeof file === 'object' && !Array.isArray(file) && file.apps && typeof file.apps === 'object' && !Array.isArray(file.apps) ? file.apps as Row : {};
    const entry = apps[SHARED_APP] as Row | undefined;
    const keys = file && typeof file === 'object' && file.keys && typeof file.keys === 'object' && !Array.isArray(file.keys) ? file.keys as Row : {};
    if (!file || typeof file !== 'object' || Array.isArray(file) || file.version !== 1) reason = 'the AI settings file has an unknown version';
    else if (!entry || typeof entry !== 'object' || Array.isArray(entry)) reason = `the AI settings file has no entry for ${SHARED_APP}`;
    else if (entry.ai !== true) reason = `AI is off for ${SHARED_APP} in the AI settings file`;
    else if (!Object.hasOwn(SHARED_PROVIDERS, entry.provider as string)) reason = `${SHARED_APP} has an unknown provider in the AI settings file`;
    else if (typeof entry.monthly_usd !== 'number' || !(entry.monthly_usd >= 0 && entry.monthly_usd <= 1000)) reason = `${SHARED_APP} has no valid monthly_usd in the AI settings file`;
    else if (typeof keys[entry.provider as string] !== 'string' || !pyStrip(keys[entry.provider as string] as string)) {
      reason = `the AI settings file has no ${entry.provider as string} key for ${SHARED_APP}`;
    } else {
      result = { provider: SHARED_PROVIDERS[entry.provider as string]!, model: typeof entry.model === 'string' && entry.model ? entry.model : null,
        tier: typeof entry.tier === 'string' ? entry.tier : null, effort: EFFORTS.includes(entry.effort as string) ? entry.effort as string : null,
        monthly_usd: entry.monthly_usd, key: pyStrip(keys[entry.provider as string] as string) };
    }
  }
  if (reason) log(`AI settings: ${reason}; the admin's AI is off`);
  return result;
}

// ---- prices -------------------------------------------------------------------------------------------------------

/** Today's date here, as time.strftime("%Y-%m-%d") gives it. */
const localDate = (now: number) => { const d = new Date(now * 1000); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; };

export function priceToday(m: Model, now: number): { in: number; out: number } {
  if (m.promo && localDate(now) < m.promo.until) return { in: m.promo.in, out: m.promo.out };
  return { in: m.in, out: m.out };
}

const genCompare = (a: number[], b: number[]) => {
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    if (i >= a.length) return -1;
    if (i >= b.length) return 1;
    if (a[i] !== b[i]) return a[i]! - b[i]!;
  }
  return 0;
};

/** The model a console tier means for a provider, only ever one with a price here. "cheapest" (the default) is the
 * lowest input + output price today, the newest winning a tie; "balanced" and "best" are the newest of their family. */
export function resolveModel(provider: string, tier: string | null, now: number, models: Model[] = MODELS): string | null {
  const priced = models.filter((m) => m.provider === provider && m.in !== null && m.in !== undefined && m.out !== null && m.out !== undefined);
  const family = tier ? TIERS[provider]?.[tier] : undefined;
  const inFamily = priced.filter((m) => family && m.family === family);
  if (inFamily.length) return inFamily.reduce((best, m) => (genCompare(m.gen, best.gen) > 0 ? m : best)).id;
  if (!priced.length) return null;
  const total = (m: Model) => { const p = priceToday(m, now); return p.in + p.out; };
  // min by (price, newest first): Python's min keeps the first of equal keys.
  return priced.reduce((best, m) => (total(m) < total(best) || (total(m) === total(best) && genCompare(m.gen, best.gen) > 0) ? m : best)).id;
}

export function cost(info: { in: number; out: number; long?: Model['long'] }, tokensIn: number, tokensOut: number): number {
  const rates = info.long && tokensIn > info.long.over ? { in: info.long.in, out: info.long.out } : info;
  return (tokensIn * rates.in + tokensOut * rates.out) / 1_000_000;
}

/** About 3.5 characters per token for English, plus the provider's own wrapping of the schema; on the high side. */
export const estimateInputTokens = (prompt: string, schema: object) => Math.trunc(([...prompt].length + pyJsonDumps(schema).length) / 3.5) + 400;

const monthStart = (now: number) => { const d = new Date(now * 1000); return new Date(d.getFullYear(), d.getMonth(), 1).getTime() / 1000; };
const dayStart = (now: number) => { const d = new Date(now * 1000); return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime() / 1000; };

export interface Scope { source: 'own' | 'shared'; user_id: number; paid_by: number; trusted: boolean; managed?: boolean; effort?: string | null;
  blocked?: string; owner?: string; daily_cap?: number; pool_limit?: number }
export type AiSettings = Row & { _ai: Scope };
export interface ModelInfo { provider: string; id: string; label: string; in: number; out: number; effort: boolean; free: boolean; long?: Model['long'] }

/** One lock for paid calls, so two clicks can't both slip under the limit. */
class Lock {
  private tail = Promise.resolve();
  run<T>(fn: () => Promise<T>): Promise<T> {
    const result = this.tail.then(fn);
    this.tail = result.then(() => undefined, () => undefined);
    return result;
  }
}

export interface AiOptions { db: Db; store: Store; net: Net; now: () => number; settingsFile: string;
  /** The person's name from Cougar Cave, for "Pat's AI". */
  nameOf: (memberId: number) => Promise<string>; log?: (line: string) => void; sleep?: (seconds: number) => Promise<void> }

export class Ai {
  private readonly lock = new Lock();
  constructor(private readonly o: AiOptions) {}

  private async query(q: ReturnType<typeof sql>): Promise<Row[]> {
    return rowsOf(await this.o.db.execute(q));
  }

  /** The admin's AI from the owner console: {provider, model, monthly_usd, key, effort}, or {off: reason}. A pinned
   * model wins over the tier. A model with no price here is off too, so its cost can always be capped. */
  managed(): (Shared & { off?: undefined }) | { off: string } {
    const m = loadShared(this.o.settingsFile, this.o.log);
    if (m === null) return { off: MANAGED_OFF };
    const model = m.model || resolveModel(m.provider, m.tier, this.o.now());
    if (!model || !MODELS.some((x) => x.provider === m.provider && x.id === model)) {
      return { off: `The owner console picked a model Deal Hunter has no price for (${model || 'none'}).` };
    }
    return { ...m, model };
  }

  /** The admin's AI settings, from the shared file only: every key the database holds is blanked, and no free tier. */
  private adminAi(adminSettings: Row): [Row, ReturnType<Ai['managed']>] {
    const m = this.managed();
    const out: Row = Object.fromEntries(AI_KEYS.map((k) => [k, adminSettings[k] ?? null]));
    for (const p of Object.values(PROVIDERS)) if (p.key) out[p.key] = '';
    out.gemini_free_tier = false;
    if (m.off !== undefined) Object.assign(out, { ai_provider: 'off', ai_monthly_limit: 0.0 });
    else {
      const p = PROVIDERS[m.provider]!;
      Object.assign(out, { ai_provider: m.provider, [p.model]: m.model, [p.key]: m.key, ai_monthly_limit: m.monthly_usd });
    }
    return [out, m];
  }

  /** The settings a person's AI requests run with: their own, or the admin's AI on the person's limits. */
  async settingsFor(memberId: number): Promise<AiSettings> {
    const s = await this.o.store.getSettings(memberId);
    const [user] = await this.query(sql`SELECT admin, ai_shared, ai_allowance, ai_daily_cap FROM deals.members WHERE id = ${memberId}`);
    if (user!.admin) {
      // The admin's AI is managed in the owner console. "trusted": the addresses are the admin's.
      const [adminAi, m] = this.adminAi(s);
      Object.assign(s, adminAi);
      const scope: Scope = { source: 'own', user_id: memberId, paid_by: memberId, trusted: true, managed: true, effort: m.off === undefined ? m.effort : null };
      if (m.off !== undefined) scope.blocked = m.off;
      return { ...s, _ai: scope };
    }
    if (s.ai_source === 'own') return { ...s, _ai: { source: 'own', user_id: memberId, paid_by: memberId, trusted: false } };
    const adminId = (await this.o.store.adminId()) ?? 0;
    const [adminAi] = this.adminAi(await this.o.store.getSettings(adminId));
    for (const k of AI_KEYS) s[k] = adminAi[k];
    s.ai_monthly_limit = Number(user!.ai_allowance || 0);
    const owner = (await this.o.nameOf(adminId)) || 'the admin';
    const scope: Scope = { source: 'shared', user_id: memberId, paid_by: adminId, owner, trusted: true, managed: true,
      daily_cap: Math.trunc(Number(user!.ai_daily_cap || 0)), pool_limit: Number(adminAi.ai_monthly_limit || 0) };
    if (!user!.ai_shared) {
      s.ai_provider = 'off';
      scope.blocked = `${owner} hasn't shared their AI with you. Use your own key in Settings > AI.`;
    } else if (!Object.hasOwn(PROVIDERS, s.ai_provider as string)) {
      scope.blocked = `${owner}'s AI is off. Use your own key in Settings > AI.`;
    }
    return { ...s, _ai: scope };
  }

  /** Whether Claude may fall back to ANTHROPIC_API_KEY: never for the admin's AI, whose only key is the console's. */
  private envKeyOk(settings: AiSettings): boolean {
    return Boolean(process.env.ANTHROPIC_API_KEY) && !settings._ai.managed;
  }

  /** The AIs a person can pick for one request: every model of each provider they have a key for. On the admin's
   * shared AI, only the model the admin chose. */
  choices(settings: AiSettings): Row[] {
    const sc = settings._ai;
    if (sc.managed) {
      let info: ModelInfo;
      try {
        info = this.modelInfo(settings);
      } catch (e) {
        if (e instanceof AIError) return [];
        throw e;
      }
      const label = sc.source === 'shared' ? `${info.label} (${sc.owner}'s)` : info.label;
      return [{ provider: info.provider, model: info.id, label, free: info.free, current: true }];
    }
    const out: Row[] = [];
    const provider = settings.ai_provider as string;
    const current = JSON.stringify([settings.ai_provider ?? null, Object.hasOwn(PROVIDERS, provider) ? settings[PROVIDERS[provider]!.model] ?? null : null]);
    for (const [name, p] of Object.entries(PROVIDERS)) {
      if (!(truthy(settings[p.key]) || (name === 'claude' && this.envKeyOk(settings)))) continue;
      for (const m of MODELS) {
        if (m.provider !== name) continue;
        const free = name === 'gemini' && truthy(settings.gemini_free_tier);
        out.push({ provider: name, model: m.id, free, label: m.label + (free ? ', free tier' : ''), current: current === JSON.stringify([name, m.id]) });
      }
    }
    return out;
  }

  /** These settings, set to use the AI picked for one request (it must be one of choices()). */
  withChoice(settings: AiSettings, provider: unknown, model: unknown): AiSettings {
    if (!truthy(provider)) return settings;
    if (!this.choices(settings).some((c) => c.provider === provider && c.model === model)) throw new AIError("That AI isn't available to you; pick one from the list");
    return { ...settings, ai_provider: provider, [PROVIDERS[provider as string]!.model]: model };
  }

  /** The reasoning effort to send: the owner console's for the admin's AI, else "low"; none for a model without one. */
  private effortFor(settings: AiSettings, info: ModelInfo): string | null {
    if (!info.effort) return null;
    return settings._ai.effort || 'low';
  }

  modelInfo(settings: AiSettings): ModelInfo {
    const provider = 'ai_provider' in settings ? settings.ai_provider as string : 'off';
    if (!Object.hasOwn(PROVIDERS, provider)) throw new AIError(settings._ai.blocked || 'AI is off. Turn it on in Settings.');
    const model = pyStrip((settings[PROVIDERS[provider]!.model] as string) || '');
    for (const m of MODELS) {
      if (m.provider === provider && m.id === model) {
        // Google bills nothing on a key without billing; its replies don't say which tier, so the person does.
        if (provider === 'gemini' && truthy(settings.gemini_free_tier)) return { ...m, label: `${m.label}, free tier`, in: 0.0, out: 0.0, free: true };
        return { ...m, ...priceToday(m, this.o.now()), free: false };
      }
    }
    throw new AIError(`Pick a model from the list in Settings > AI ("${model}" has no known price, so its cost can't be capped).`);
  }

  private async spent(where: ReturnType<typeof sql>, since: number): Promise<number> {
    const [row] = await this.query(sql`SELECT COALESCE(SUM(cost), 0) AS s FROM deals.ai_usage WHERE at >= ${since} AND ${where}`);
    return Number(row!.s);
  }

  /** Own key: everything run on it this month (for the admin, that includes people sharing it). Shared: what this
   * person ran on the admin's AI, against the allowance the admin gave them. */
  async budget(settings: AiSettings): Promise<Row> {
    const sc = settings._ai;
    const since = monthStart(this.o.now());
    const spent = sc.source === 'own' ? await this.spent(sql`paid_by = ${sc.paid_by}`, since)
      : await this.spent(sql`member_id = ${sc.user_id} AND paid_by = ${sc.paid_by}`, since);
    const limit = Math.max(0.0, Number(settings.ai_monthly_limit || 0));
    const out: Row = { spent: pyRound(spent, 6), limit, remaining: pyRound(Math.max(0.0, limit - spent), 6), source: sc.source };
    if (sc.source === 'shared') {
      const [today] = await this.query(sql`SELECT COUNT(*)::int AS n FROM deals.ai_usage WHERE at >= ${dayStart(this.o.now())}
        AND member_id = ${sc.user_id} AND paid_by = ${sc.paid_by}`);
      Object.assign(out, { owner: sc.owner, daily_cap: sc.daily_cap, today: Number(today!.n), blocked: sc.blocked ?? '' });
      try {
        const info = this.modelInfo(settings);
        Object.assign(out, { model_label: info.label, free: info.free });
      } catch (e) {
        if (!(e instanceof AIError)) throw e;
      }
    }
    return out;
  }

  async estimate(settings: AiSettings, action: string, prompt: string, schema: object): Promise<Row> {
    const info = this.modelInfo(settings);
    const a = ACTIONS[action]!;
    const tokensIn = estimateInputTokens(prompt, schema);
    const typical = cost(info, tokensIn, a.typical_out), worst = cost(info, tokensIn, a.max_out);
    const b = await this.budget(settings);
    const sc = settings._ai;
    const shared = sc.source === 'shared';
    let allowed = true, reason = '';
    const keySetting = PROVIDERS[info.provider]!.key;
    const envKey = info.provider === 'claude' && this.envKeyOk(settings);
    const label = PROVIDERS[info.provider]!.label;
    if (keySetting && !(truthy(settings[keySetting]) || envKey)) {
      allowed = false;
      reason = shared ? `${sc.owner}'s AI has no ${label} key yet.` : `Add your ${label} API key in Settings > AI first.`;
    } else if (shared && (b.today as number) >= (b.daily_cap as number)) {
      allowed = false;
      reason = `You've used today's ${pyStr(b.daily_cap)} AI requests on ${sc.owner}'s AI. More tomorrow, or use your own key in Settings > AI.`;
    } else if (!info.free && (b.spent as number) + worst > (b.limit as number)) {
      allowed = false;
      reason = shared
        ? `${sc.owner} gave you $${pyFixed(b.limit as number, 2)} a month of paid AI; $${pyFixed(b.spent as number, 2)} is used and this could cost up to $${pyFixed(worst, 3)}. Ask ${sc.owner} for more, or use your own key.`
        : `Monthly AI limit: $${pyFixed(b.spent as number, 2)} of $${pyFixed(b.limit as number, 2)} used, and this could cost up to $${pyFixed(worst, 3)}. Raise the limit or pick a cheaper model in Settings > AI.`;
    } else if (shared && !info.free && (await this.spent(sql`paid_by = ${sc.paid_by}`, monthStart(this.o.now()))) + worst > sc.pool_limit!) {
      allowed = false;
      reason = `${sc.owner}'s AI budget for this month is used up. Try again next month, or use your own key.`;
    }
    return { action, action_label: a.label, provider: info.provider, model: info.id, model_label: info.label, free: info.free,
      typical: pyRound(typical, 5), max: pyRound(worst, 5), ...b, allowed, reason };
  }

  /** The gate: estimate, refuse over the limit, call, record what it billed, and read the answer. */
  async run(settings: AiSettings, action: string, prompt: string, schema: object): Promise<Row> {
    const [text, info, problem] = await this.lock.run(async () => {
      const est = await this.estimate(settings, action, prompt, schema);
      if (!est.allowed) throw new AIError(est.reason as string);
      const info = this.modelInfo(settings);
      const [text, tokensIn, tokensOut, problem] = await this.call(settings, info, prompt, schema, ACTIONS[action]!.max_out);
      // Record before parsing: a cut-off or refused answer is still billed.
      const spent = info.free ? 0.0 : cost(info, tokensIn, tokensOut);
      const sc = settings._ai;
      await this.o.db.execute(sql`INSERT INTO deals.ai_usage (at, provider, model, action, input_tokens, output_tokens, cost, member_id, paid_by)
        VALUES (${this.o.now()}, ${info.provider}, ${info.id}, ${action}, ${tokensIn}, ${tokensOut}, ${spent}, ${sc.user_id}, ${sc.paid_by})`);
      return [text, info, problem] as const;
    });
    if (problem) throw new AIError(problem);
    try {
      return JSON.parse(text) as Row;
    } catch {
      throw new AIError(`${info.label} returned an unreadable answer`);
    }
  }

  async usageSummary(settings: AiSettings): Promise<Row> {
    const sc = settings._ai;
    const where = sc.source === 'own' ? sql`paid_by = ${sc.paid_by}` : sql`member_id = ${sc.user_id} AND paid_by = ${sc.paid_by}`;
    const breakdown = (await this.query(sql`SELECT model, action, COUNT(*)::int AS n, SUM(cost) AS cost FROM deals.ai_usage
      WHERE at >= ${monthStart(this.o.now())} AND ${where} GROUP BY model, action ORDER BY cost DESC, model COLLATE "C", action COLLATE "C"`))
      .map((r) => ({ model: r.model, action: r.action, n: r.n, cost: Number(r.cost) }));
    return { ...(await this.budget(settings)), breakdown };
  }

  /** Everything the Settings page needs to show models with their prices. */
  catalog(): Row {
    return { providers: Object.fromEntries(Object.entries(PROVIDERS).map(([k, v]) => [k, v.label])),
      models: MODELS.map((m) => ({ provider: m.provider, id: m.id, label: m.label, ...priceToday(m, this.o.now()) })), actions: ACTIONS };
  }

  // ---- providers: each returns [json text, input tokens, output tokens, problem or null] -----------------------------

  private call(settings: AiSettings, info: ModelInfo, prompt: string, schema: object, maxOut: number): Promise<[string, number, number, string | null]> {
    if (info.provider === 'claude') return this.claude(settings, info, prompt, schema, maxOut);
    if (info.provider === 'openai') return this.openai(settings, info, prompt, schema, maxOut);
    return this.gemini(settings, info, prompt, schema, maxOut);
  }

  /** Claude through Anthropic's Messages API, as the Python SDK sent it, with the SDK's two retries on a busy or
   * failing service (no server-side fallback: it can re-run a declined request on a pricier model, past the budget). */
  private async claude(settings: AiSettings, info: ModelInfo, prompt: string, schema: object, maxOut: number): Promise<[string, number, number, string | null]> {
    const key = (settings.anthropic_api_key as string) || (this.envKeyOk(settings) ? process.env.ANTHROPIC_API_KEY! : null);
    if (!key) throw new AIError('Claude has no API key');
    const outputConfig: Row = { format: { type: 'json_schema', schema } };
    const effort = this.effortFor(settings, info);
    if (effort) outputConfig.effort = effort;
    const body = JSON.stringify({ model: info.id, max_tokens: maxOut, messages: [{ role: 'user', content: prompt }], output_config: outputConfig });
    let res;
    for (let attempt = 0; ; attempt++) {
      try {
        res = await this.o.net({ url: 'https://api.anthropic.com/v1/messages', method: 'POST', body, timeout: 180,
          headers: { 'x-api-key': key, 'anthropic-version': '2023-06-01', 'content-type': 'application/json', accept: 'application/json' } });
      } catch (e) {
        if (!(e instanceof NetError)) throw e;
        if (attempt < 2) { await (this.o.sleep ?? ((s) => new Promise((done) => setTimeout(done, s * 1000))))(0.5 * 2 ** attempt); continue; }
        throw new AIError("Can't reach the Claude API");
      }
      if ((res.status === 408 || res.status === 409 || res.status === 429 || res.status >= 500) && attempt < 2) {
        await (this.o.sleep ?? ((s) => new Promise((done) => setTimeout(done, s * 1000))))(0.5 * 2 ** attempt);
        continue;
      }
      break;
    }
    const text = new TextDecoder('utf-8').decode(res.body);
    if (res.status === 401) throw new AIError('Claude API key is invalid');
    if (res.status === 429) throw new AIError('Claude rate limit hit; try again in a minute');
    if (res.status >= 400) {
      let shown: string;
      try {
        shown = pyReprValue(JSON.parse(text));
      } catch {
        shown = text;
      }
      throw new AIError(`Claude API error ${res.status}: Error code: ${res.status} - ${shown}`);
    }
    const data = JSON.parse(text) as Row;
    const u = (data.usage ?? {}) as Row;
    const tokensIn = Number(u.input_tokens ?? 0) + Number(u.cache_creation_input_tokens || 0) + Number(u.cache_read_input_tokens || 0);
    let problem: string | null = null;
    if (data.stop_reason === 'refusal') problem = 'Claude declined this request';
    else if (data.stop_reason === 'max_tokens') problem = "Claude's answer was cut off; try again";
    const block = ((data.content ?? []) as Row[]).find((b) => b.type === 'text');
    return [block ? String(block.text) : '', tokensIn, Number(u.output_tokens ?? 0), problem];
  }

  private async postJson(url: string, headers: Record<string, string>, body: Row, name: string): Promise<Row> {
    let res;
    try {
      res = await this.o.net({ url, method: 'POST', body: pyJsonDumps(body), headers: { 'Content-Type': 'application/json', ...headers }, timeout: 180 });
    } catch (e) {
      if (e instanceof NetError) throw new AIError(`Can't reach the ${name} API (${e.reason})`);
      throw e;
    }
    const text = new TextDecoder('utf-8').decode(res.body);
    if (res.status >= 400) {
      let detail: string;
      try {
        const message = ((JSON.parse(text) as Row).error as Row).message;
        if (message === undefined) throw new Error();
        detail = pyStr(message);
      } catch {
        detail = [...text].slice(0, 200).join('');
      }
      if (res.status === 401 || res.status === 403) throw new AIError(`${name} rejected the API key (${detail})`);
      if (res.status === 429) throw new AIError(`${name} rate limit or quota hit; try again later (${detail})`);
      throw new AIError(`${name} API error ${res.status}: ${detail}`);
    }
    return JSON.parse(text) as Row;
  }

  private requireKey(settings: AiSettings, provider: string, name: string): string {
    const key = pyStrip((settings[PROVIDERS[provider]!.key!] as string) || '');
    if (!key) throw new AIError(`Add your ${name} API key in Settings > AI`);
    return key;
  }

  private async openai(settings: AiSettings, info: ModelInfo, prompt: string, schema: object, maxOut: number): Promise<[string, number, number, string | null]> {
    const key = this.requireKey(settings, 'openai', 'OpenAI');
    const body: Row = { model: info.id, input: [{ role: 'user', content: prompt }],
      max_output_tokens: maxOut,   // includes reasoning tokens
      text: { format: { type: 'json_schema', name: 'answer', strict: true, schema } } };
    const effort = this.effortFor(settings, info);
    if (effort) body.reasoning = { effort };
    const data = await this.postJson('https://api.openai.com/v1/responses', { Authorization: `Bearer ${key}` }, body, 'OpenAI');
    const usage = (truthy(data.usage) ? data.usage : {}) as Row;
    let text = '', problem: string | null = null;
    for (const item of (truthy(data.output) ? data.output : []) as Row[]) {
      if (item.type !== 'message') continue;   // reasoning items
      for (const part of (truthy(item.content) ? item.content : []) as Row[]) {
        if (part.type === 'output_text') text += 'text' in part ? String(part.text) : '';
        else if (part.type === 'refusal') problem = 'OpenAI declined this request';
      }
    }
    if (data.status === 'incomplete') {
      const reason = ((truthy(data.incomplete_details) ? data.incomplete_details : {}) as Row).reason;
      problem = reason === 'max_output_tokens' ? "OpenAI's answer was cut off; try again" : `OpenAI stopped early (${pyStr(reason)})`;
    }
    return [text, Number(usage.input_tokens ?? 0), Number(usage.output_tokens ?? 0), problem];
  }

  private async gemini(settings: AiSettings, info: ModelInfo, prompt: string, schema: object, maxOut: number): Promise<[string, number, number, string | null]> {
    const key = this.requireKey(settings, 'gemini', 'Gemini');
    const url = `https://generativelanguage.googleapis.com/v1beta/models/${info.id}:generateContent`;
    const config: Row = { responseMimeType: 'application/json', responseJsonSchema: schema, maxOutputTokens: maxOut };
    const effort = this.effortFor(settings, info);
    if (effort) config.thinkingConfig = { thinkingLevel: effort };
    const data = await this.postJson(url, { 'x-goog-api-key': key }, { contents: [{ role: 'user', parts: [{ text: prompt }] }], generationConfig: config }, 'Gemini');
    const usage = (truthy(data.usageMetadata) ? data.usageMetadata : {}) as Row;
    // Thinking tokens bill as output but aren't in candidatesTokenCount.
    const tokensOut = Number(usage.candidatesTokenCount ?? 0) + Number(usage.thoughtsTokenCount ?? 0);
    const tokensIn = Number(usage.promptTokenCount ?? 0);
    const candidates = (truthy(data.candidates) ? data.candidates : []) as Row[];
    if (!candidates.length) {
      const feedback = (truthy(data.promptFeedback) ? data.promptFeedback : {}) as Row;
      return ['', tokensIn, tokensOut, `Gemini declined this request (${'blockReason' in feedback ? pyStr(feedback.blockReason) : 'no answer'})`];
    }
    const c = candidates[0]!;
    const parts = ((truthy(c.content) ? c.content : {}) as Row).parts as Row[] | undefined;
    const text = (parts ?? []).filter((p) => !truthy(p.thought)).map((p) => ('text' in p ? String(p.text) : '')).join('');
    let problem: string | null = null;
    if (c.finishReason === 'MAX_TOKENS') problem = "Gemini's answer was cut off; try again";
    else if (c.finishReason !== undefined && c.finishReason !== null && c.finishReason !== 'STOP') problem = `Gemini stopped early (${pyStr(c.finishReason)})`;
    return [text, tokensIn, tokensOut, problem];
  }
}

/** Python's repr() of a JSON value (the Anthropic SDK's error messages print the error body this way). */
export function pyReprValue(v: unknown): string {
  if (v === null || v === undefined) return 'None';
  if (v === true) return 'True';
  if (v === false) return 'False';
  if (typeof v === 'string') return pyRepr(v);
  if (typeof v === 'number') return Number.isInteger(v) && Math.abs(v) < 1e16 ? String(v) : String(v);
  if (Array.isArray(v)) return '[' + v.map(pyReprValue).join(', ') + ']';
  return '{' + Object.entries(v as Row).map(([k, x]) => `${pyRepr(k)}: ${pyReprValue(x)}`).join(', ') + '}';
}
