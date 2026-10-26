// Addresses that come from people's settings (Ollama, Home Assistant, Discord) are fetched by the server, so a member
// could point them at the home network. The admin's own settings may be local (the home Ollama and Home Assistant
// are); everyone else's must be public internet addresses. A port of the Python app's netguard.py, with Python's own
// rule for "public" (ipaddress.is_global, the IANA special-purpose registries as Python 3.13 has them).
import { lookup } from 'node:dns/promises';
import { isGlobal, isMulticast } from './ip-ranges.js';
import { pyStrip } from './py.js';

export class BlockedURL extends Error {}

const DISCORD_HOSTS = new Set(['discord.com', 'discordapp.com', 'ptb.discord.com', 'canary.discord.com']);

/** The addresses a hostname points to. Tests pass their own. */
export type Resolve = (host: string) => Promise<string[]>;
export const resolveHost: Resolve = async (host) => (await lookup(host, { all: true, verbatim: true })).map((a) => a.address);

/** urllib.parse.urlsplit's scheme, hostname, port and path, for the parts these checks read. */
function split(url: string): { scheme: string; hostname: string; path: string } {
  const m = /^([A-Za-z][A-Za-z0-9+.-]*):(.*)$/s.exec(url);
  const scheme = m ? m[1]!.toLowerCase() : '';
  const rest = m ? m[2]! : url;
  if (!rest.startsWith('//')) return { scheme, hostname: '', path: rest };
  const end = rest.slice(2).search(/[/?#]/);
  const netloc = end < 0 ? rest.slice(2) : rest.slice(2, 2 + end);
  const path = end < 0 ? '' : rest.slice(2 + end).replace(/[?#].*$/s, '');
  const host = netloc.slice(netloc.lastIndexOf('@') + 1);
  const hostname = host.startsWith('[') ? host.slice(1, host.indexOf(']') < 0 ? undefined : host.indexOf(']'))
    : host.replace(/:[^:]*$/, '');
  return { scheme, hostname: hostname.toLowerCase(), path };
}

/** The URL, if it may be fetched. Hostnames are resolved and every address they point to is checked. */
export async function check(url: string, localOk: boolean, what = 'address', resolve: Resolve = resolveHost): Promise<string> {
  const parts = split(pyStrip(url || ''));
  if (!['http', 'https'].includes(parts.scheme) || !parts.hostname) throw new BlockedURL(`The ${what} must start with http:// or https://`);
  if (localOk) return url;
  let addresses: string[];
  try {
    addresses = await resolve(parts.hostname);
  } catch {
    throw new BlockedURL(`Can't find the ${what}'s host`);
  }
  for (const address of addresses) {
    const ip = address.split('%')[0]!;
    if (!isGlobal(ip) || isMulticast(ip)) {
      throw new BlockedURL(`The ${what} must be a public internet address, not one on a private network`);
    }
  }
  return url;
}

/** Discord webhooks only: https on Discord's own hosts, for everyone (the admin too). */
export function checkDiscord(url: string): string {
  const parts = split(pyStrip(url || ''));
  if (parts.scheme !== 'https' || !DISCORD_HOSTS.has(parts.hostname) || !parts.path.startsWith('/api/webhooks/')) {
    throw new BlockedURL("That isn't a Discord webhook address (https://discord.com/api/webhooks/...)");
  }
  return url;
}
