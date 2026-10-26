// The part of Python's xml.etree.ElementTree the feeds use (Reddit's Atom, Slickdeals' RSS): parse a document into
// elements whose tags carry their namespace as "{uri}name", and find children by simple paths ("a:entry",
// "./channel/item", "{uri}thumbnail"). Like ElementTree: an element's text is only what comes before its first child,
// comments and processing instructions are dropped, and a document that isn't well-formed is a ParseError.

export class ParseError extends Error {}

export class Element {
  text: string | null = null;
  tail: string | null = null;
  readonly children: Element[] = [];
  constructor(readonly tag: string, readonly attrib: Record<string, string>) {}

  get(key: string, fallback: string | null = null): string | null {
    return Object.hasOwn(this.attrib, key) ? this.attrib[key]! : fallback;
  }

  /** Every element the path leads to: steps separated by /, "." for this one, "prefix:name" or "{uri}name". */
  findall(path: string, namespaces: Record<string, string> = {}): Element[] {
    let current: Element[] = [this];
    // Steps are split on "/", but not inside a "{uri}", which has slashes of its own.
    for (const step of path.match(/(\{[^}]*\}[^/]*|[^/]+)/g) ?? []) {
      if (step === '.' || step === '') continue;
      const m = /^([^:{}]+):(.+)$/.exec(step);
      const tag = m ? `{${namespaces[m[1]!] ?? (() => { throw new SyntaxError(`prefix ${m[1]} not found in prefix map`); })()}}${m[2]}` : step;
      current = current.flatMap((e) => e.children.filter((c) => c.tag === tag));
    }
    return current;
  }

  find(path: string, namespaces: Record<string, string> = {}): Element | null {
    return this.findall(path, namespaces)[0] ?? null;
  }

  /** The first match's text ("" when it has none), or the default when nothing matches. */
  findtext(path: string, fallback: string | null = null, namespaces: Record<string, string> = {}): string | null {
    const e = this.find(path, namespaces);
    return e ? e.text ?? '' : fallback;
  }
}

const NAME = /^[A-Za-z_:À-￿][\w.:·À-￿-]*/;
const PREDEFINED: Record<string, string> = { lt: '<', gt: '>', amp: '&', apos: "'", quot: '"' };

function decodeEntities(s: string): string {
  return s.replace(/&([^;&]*);?/g, (whole, ref: string) => {
    if (!whole.endsWith(';')) throw new ParseError('not well-formed (invalid token)');
    if (ref.startsWith('#x')) return String.fromCodePoint(parseInt(ref.slice(2), 16));
    if (ref.startsWith('#')) return String.fromCodePoint(parseInt(ref.slice(1), 10));
    if (Object.hasOwn(PREDEFINED, ref)) return PREDEFINED[ref]!;
    throw new ParseError(`undefined entity &${ref};`);
  });
}

/** ET.fromstring(text): the document's root element. */
export function fromstring(text: string): Element {
  let i = 0;
  const stack: { element: Element; ns: Record<string, string> }[] = [];
  let root: Element | null = null;
  const addText = (raw: string) => {
    if (!raw) return;
    const top = stack.at(-1);
    if (!top) {
      if (raw.trim()) throw new ParseError('not well-formed (invalid token)');
      return;
    }
    const value = raw;
    const last = top.element.children.at(-1);
    if (last) last.tail = (last.tail ?? '') + value;
    else top.element.text = (top.element.text ?? '') + value;
  };
  const qualify = (name: string, ns: Record<string, string>, attribute: boolean) => {
    const at = name.indexOf(':');
    if (at < 0) return attribute || !ns[''] ? name : `{${ns['']}}${name}`;
    const prefix = name.slice(0, at);
    if (prefix === 'xml') return `{http://www.w3.org/XML/1998/namespace}${name.slice(at + 1)}`;
    if (!Object.hasOwn(ns, prefix)) throw new ParseError('unbound prefix');
    return `{${ns[prefix]}}${name.slice(at + 1)}`;
  };
  while (i < text.length) {
    const lt = text.indexOf('<', i);
    if (lt < 0) { addText(decodeEntities(text.slice(i))); break; }
    if (lt > i) addText(decodeEntities(text.slice(i, lt)));
    if (text.startsWith('<!--', lt)) {
      const end = text.indexOf('-->', lt + 4);
      if (end < 0) throw new ParseError('unclosed token');
      i = end + 3;
    } else if (text.startsWith('<![CDATA[', lt)) {
      const end = text.indexOf(']]>', lt + 9);
      if (end < 0) throw new ParseError('unclosed CDATA section');
      addText(text.slice(lt + 9, end));
      i = end + 3;
    } else if (text.startsWith('<?', lt)) {
      const end = text.indexOf('?>', lt + 2);
      if (end < 0) throw new ParseError('unclosed token');
      i = end + 2;
    } else if (text.startsWith('<!', lt)) {   // a DOCTYPE: skipped, with its internal subset
      let depth = 0;
      let j = lt + 2;
      for (; j < text.length; j++) {
        if (text[j] === '[') depth++;
        else if (text[j] === ']') depth--;
        else if (text[j] === '>' && depth <= 0) break;
      }
      i = j + 1;
    } else if (text[lt + 1] === '/') {
      const end = text.indexOf('>', lt);
      if (end < 0) throw new ParseError('unclosed token');
      const name = text.slice(lt + 2, end).trim();
      const top = stack.pop();
      if (!top || qualify(name, top.ns, false) !== top.element.tag) throw new ParseError('mismatched tag');
      i = end + 1;
    } else {
      const m = NAME.exec(text.slice(lt + 1));
      if (!m) throw new ParseError('not well-formed (invalid token)');
      let j = lt + 1 + m[0].length;
      const raw: [string, string][] = [];
      for (;;) {
        const ws = /^\s*/.exec(text.slice(j))![0];
        j += ws.length;
        if (text.startsWith('/>', j) || text[j] === '>') break;
        const a = /^([^\s=/>]+)\s*=\s*("([^"]*)"|'([^']*)')/.exec(text.slice(j));
        if (!a || !ws) throw new ParseError('not well-formed (invalid token)');
        if (raw.some(([k]) => k === a[1])) throw new ParseError('duplicate attribute');
        raw.push([a[1]!, decodeEntities(a[3] ?? a[4] ?? '')]);
        j += a[0].length;
      }
      const ns = { ...(stack.at(-1)?.ns ?? {}) };
      for (const [k, v] of raw) {
        if (k === 'xmlns') ns[''] = v;
        else if (k.startsWith('xmlns:')) ns[k.slice(6)] = v;
      }
      const attrib: Record<string, string> = {};
      for (const [k, v] of raw) if (k !== 'xmlns' && !k.startsWith('xmlns:')) attrib[qualify(k, ns, true)] = v;
      const element = new Element(qualify(m[0], ns, false), attrib);
      const parent = stack.at(-1);
      if (parent) parent.element.children.push(element);
      else if (root) throw new ParseError('junk after document element');
      else root = element;
      const selfClosing = text.startsWith('/>', j);
      if (!selfClosing) stack.push({ element, ns });
      i = j + (selfClosing ? 2 : 1);
    }
  }
  if (!root || stack.length) throw new ParseError(root ? 'no element found' : 'no element found');
  return root;
}
