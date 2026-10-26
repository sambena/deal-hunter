// Python's ipaddress.is_global and is_multicast (Python 3.13): which addresses count as public internet ones. The
// ranges are the IANA special-purpose registries as Python lists them (the documentation and private-use blocks).
import { isIP } from 'node:net';

const V4_PRIVATE = ['0.0.0.0/8', '10.0.0.0/8', '127.0.0.0/8', '169.254.0.0/16', '172.16.0.0/12', '192.0.0.0/24',
  '192.0.0.170/31', '192.0.2.0/24', '192.168.0.0/16', '198.18.0.0/15', '198.51.100.0/24', '203.0.113.0/24',
  '240.0.0.0/4', '255.255.255.255/32'];
const V4_PRIVATE_EXCEPTIONS = ['192.0.0.9/32', '192.0.0.10/32'];
const V4_SHARED = '100.64.0.0/10';   // neither private nor global
const V4_MULTICAST = '224.0.0.0/4';
const V6_PRIVATE = ['::1/128', '::/128', '::ffff:0.0.0.0/96', '64:ff9b:1::/48', '100::/64', '2001::/23', '2001:db8::/32',
  '2002::/16', '3fff::/20', 'fc00::/7', 'fe80::/10'];
const V6_PRIVATE_EXCEPTIONS = ['2001:1::1/128', '2001:1::2/128', '2001:3::/32', '2001:4:112::/48', '2001:20::/28', '2001:30::/28'];
const V6_MULTICAST = 'ff00::/8';

function v4(address: string): bigint {
  return address.split('.').reduce((n, part) => (n << 8n) | BigInt(Number(part)), 0n);
}

function v6(address: string): bigint {
  let text = address;
  const dotted = /(\d+\.\d+\.\d+\.\d+)$/.exec(text);
  if (dotted) {
    const n = v4(dotted[1]!);
    text = text.slice(0, dotted.index) + `${(n >> 16n).toString(16)}:${(n & 0xffffn).toString(16)}`;
  }
  const [head, tail] = text.split('::') as [string, string?];
  const left = head ? head.split(':') : [];
  const right = tail ? tail.split(':') : [];
  const groups = tail === undefined ? left : [...left, ...Array(8 - left.length - right.length).fill('0'), ...right];
  return groups.reduce((n, g) => (n << 16n) | BigInt(parseInt(g || '0', 16)), 0n);
}

function within(value: bigint, bits: number, cidr: string): boolean {
  const [base, length] = cidr.split('/') as [string, string];
  const start = bits === 32 ? v4(base) : v6(base);
  const shift = BigInt(bits - Number(length));
  return value >> shift === start >> shift;
}

const v4Private = (n: bigint) => V4_PRIVATE.some((c) => within(n, 32, c)) && !V4_PRIVATE_EXCEPTIONS.some((c) => within(n, 32, c));

/** The IPv4 address an IPv4-mapped IPv6 address (::ffff:a.b.c.d) stands for, else null. */
function mapped(n: bigint): bigint | null {
  return n >> 32n === 0xffffn ? n & 0xffffffffn : null;
}

export function isGlobal(address: string): boolean {
  const family = isIP(address);
  if (family === 4) { const n = v4(address); return !within(n, 32, V4_SHARED) && !v4Private(n); }
  if (family === 6) {
    const n = v6(address);
    const four = mapped(n);
    if (four !== null) return !within(four, 32, V4_SHARED) && !v4Private(four);
    return !(V6_PRIVATE.some((c) => within(n, 128, c)) && !V6_PRIVATE_EXCEPTIONS.some((c) => within(n, 128, c)));
  }
  return false;
}

export function isMulticast(address: string): boolean {
  const family = isIP(address);
  if (family === 4) return within(v4(address), 32, V4_MULTICAST);
  if (family === 6) {
    const n = v6(address);
    const four = mapped(n);
    return four !== null ? within(four, 32, V4_MULTICAST) : within(n, 128, V6_MULTICAST);
  }
  return false;
}
