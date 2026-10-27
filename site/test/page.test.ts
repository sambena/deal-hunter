// The page (client/): the Python app's page moved onto the framework. These read its files as they are, and build the
// app to ask its routes and policy; nothing is fetched. Each check runs beside a planted failure it must catch.
import { readFileSync } from 'node:fs';
import { loadConfig } from '@cougarcave/server';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { ID, RETIRED } from '../src/api.js';
import { build } from '../src/app.js';

const read = (name: string) => readFileSync(new URL(`../client/${name}`, import.meta.url), 'utf8');
const PAGE = read('index.html');
const HELP = read('api.html');
const SCRIPT = read('app.js');

/** Inline styles the page policy would block: style attributes and <style> elements, in markup or in HTML built by
 * the script. (Styles set through the CSSOM, el.style.x = …, are allowed and aren't matched.) */
const inlineStyles = (text: string) => [...text.matchAll(/<style\b|\sstyle\s*=\s*["'`$]/gi)].map((m) => m[0].trim());

/** Every API call the script makes: method and path, with ${…} parts as the routes' id. */
function calls(text: string): [string, string][] {
  return [...text.matchAll(/api\("(GET|POST|PUT|DELETE)", (?:"([^"]*)"|`([^`]*)`)/g)]
    .map((m) => [m[1]!, (m[2] ?? m[3]!).replace(/\$\{(?:[^{}]|\{[^}]*\})*\}/g, ID).split('?')[0]!] as [string, string]);
}

let app: Awaited<ReturnType<typeof build>>;
beforeAll(async () => {
  app = await build(loadConfig({ APP_ORIGIN: 'http://127.0.0.1:8780', DEV_LOGIN: '1', DATA_DIR: ':memory:' }), { logger: false });
}, 60_000);
afterAll(async () => app?.close());

describe('the page', () => {
  it('has no inline styles for the policy to block; a planted one is caught', () => {
    expect([PAGE, HELP, SCRIPT].flatMap(inlineStyles)).toEqual([]);
    expect(inlineStyles('<span class="dot" style="background:${c}"></span>')).toEqual(['style="']);
    expect(inlineStyles('<style>a{}</style>')).toEqual(['<style']);
    expect(inlineStyles('el.style.background = d.bg')).toEqual([]);   // the CSSOM is how paint() sets them
  });

  it('only calls routes that exist, never a retired one; a retired or unknown call is caught', () => {
    const found = calls(SCRIPT);
    expect(found.length).toBeGreaterThan(30);
    const retired = new Set(RETIRED.map(([m, u]) => `${m} ${u}`));
    const wrong = (list: [string, string][]) => list.filter(([m, u]) => retired.has(`${m} ${u}`) || !app.hasRoute({ method: m as 'GET', url: u }))
      .map(([m, u]) => `${m} ${u}`);
    expect(wrong(found)).toEqual([]);
    expect(wrong(calls('api("POST", "/api/api-key"); api("GET", `/api/tokens`); api("GET", "/api/nope"); api("GET", "/api/state")')))
      .toEqual(['POST /api/api-key', 'GET /api/tokens', 'GET /api/nope']);
  });

  it("marks every write as the page's own (X-Cave-Request) and reads the framework's error message", () => {
    const helper = SCRIPT.slice(SCRIPT.indexOf('async function api('), SCRIPT.indexOf('\n}\n', SCRIPT.indexOf('async function api(')));
    expect(helper).toMatch(/headers: write \? \{ "Content-Type": "application\/json", "X-Cave-Request": "1" \}/);
    expect(helper).toContain('data.message || data.error');
    expect(helper).toContain('new CustomEvent("cave-signed-out")');   // a 401 shows the sign-in card, not a reload
    expect(helper).not.toContain('location.reload');
    // Every request goes through that helper: no other fetch in the script.
    expect([...SCRIPT.matchAll(/\bfetch\(/g)].length).toBe(1);
  });

  it('signs in through Cougar Cave only: the kit bar and card, no own account form', () => {
    expect(PAGE).toMatch(/<cave-bar [^>]*session="\/auth\/me" signin="\/auth\/login" signout="\/auth\/signout" devices="\/auth\/devices"/);
    expect(PAGE).toContain('<cave-signin id="signin"');
    expect(PAGE).not.toMatch(/type="password" autocomplete="(current|new)-password"|id="acct-|id="invite-|id="api-key-/);
    expect(PAGE).not.toContain('https://cougarcave.dev/cave/v1/cave-bar.js');   // the kit's bar, served here
    expect(HELP).not.toMatch(/\/api\/auth\/login|Old single key/);
  });

  it("allows the shops' photos from anywhere, but still no inline styles or scripts", async () => {
    const policy = (await app.inject({ url: '/healthz' })).headers['content-security-policy'] as string;
    expect(policy).toMatch(/img-src [^;]* \*(;|$)/);
    expect(policy).toMatch(/style-src 'self'[^;]*;/);
    expect(policy).not.toContain('unsafe-inline');
    expect(policy).toMatch(/script-src 'self'[^;]*;/);
  });
});
