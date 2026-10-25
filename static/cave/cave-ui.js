// Cougar Cave shared components, built on Web Awesome. Load after the Web Awesome loader:
//   <script type="module" src="{CAVE}/cave-ui.js"></script>
// Apps hand these components data and listen for their events; how they look lives only here and in cave-theme.css.
//
//   <cave-account>  the account menu in the bar.   el.data = { name, email, items, signOut }
//                   events: cave-account-select { value }  (sign-out values and app items without an href)
//   <cave-signin>   the sign-in card.              el.render({ app, options, message })
//                   events: cave-signin-submit { method, values, form }  (method: password | code | pair-type)
//   <cave-settings> the settings drawer. Children <section data-section data-label data-icon> become its sections;
//                   an Appearance section (themes) is always added. el.show(sectionId)
// Everything user-supplied is set with textContent, never as HTML. No inline styles (strict CSPs refuse them).

const icon = (name) => new URL(`icons/${name}.svg`, import.meta.url).pathname;

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') node.className = v;
    else if (k === 'text') node.textContent = v;
    else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children.flat(Infinity)) if (c !== null && c !== undefined && c !== false) node.append(c);
  return node;
}

const initialsOf = (name) => (name || '').split(/\s+/).filter(Boolean).map((w) => w[0]).join('').slice(0, 2).toUpperCase();

function settingsElement() {
  let drawer = document.querySelector('cave-settings');
  if (!drawer) {
    drawer = el('cave-settings');
    document.body.append(drawer);
  }
  return drawer;
}

// ---------- Account menu ----------
class CaveAccount extends HTMLElement {
  set data(value) {
    this._data = value;
    this.render();
  }
  get data() { return this._data; }

  render() {
    const { name = '', email = '', items = [], signOut = [{ label: 'Sign out', value: 'signout' }] } = this._data || {};
    const dropdown = el('wa-dropdown', { placement: 'bottom-end' },
      el('wa-button', { slot: 'trigger', appearance: 'plain', size: 's', 'with-caret': true },
        el('wa-avatar', { slot: 'start', initials: initialsOf(name), label: 'Your account' }),
        el('span', { class: 'cave-name', text: name })),
      el('wa-dropdown-item', { disabled: true }, el('span', { text: name }), el('span', { slot: 'details', text: email })),
      el('wa-divider'),
      el('wa-dropdown-item', { href: 'https://auth.cougarcave.dev/if/user/' },
        el('wa-icon', { slot: 'icon', src: icon('user') }), 'Cougar Cave account'),
      items.map((item) => el('wa-dropdown-item', { href: item.href, value: item.value },
        el('wa-icon', { slot: 'icon', src: icon(item.icon || 'settings') }), item.label)),
      el('wa-dropdown-item', { value: 'cave-appearance' }, el('wa-icon', { slot: 'icon', src: icon('palette') }), 'Appearance'),
      el('wa-divider'),
      signOut.map((item) => el('wa-dropdown-item', { value: item.value },
        el('wa-icon', { slot: 'icon', src: icon('logout') }), item.label)));
    dropdown.addEventListener('wa-select', (event) => {
      const value = event.detail.item.value;
      if (!value) return;
      if (value === 'cave-appearance') settingsElement().show('appearance');
      else this.dispatchEvent(new CustomEvent('cave-account-select', { bubbles: true, detail: { value } }));
    });
    this.replaceChildren(dropdown);
  }
}

// ---------- Sign-in card ----------
const DIVIDERS = {
  password: (app) => `Or with your ${app} email and password`,
  code: () => 'Or with a sign-in code',
  pair: () => 'Or approve this screen from one that is signed in',
};

class CaveSignin extends HTMLElement {
  // app: { name, icon (kit icon name), lede }
  // options (from the server; anything missing is not offered):
  //   central:  URL that starts Cougar Cave sign-in
  //   password: { label: 'Email', reset: URL of "forgot password" or null }
  //   code:     { nameLabel, namePlaceholder, codePlaceholder }
  //   pair:     { code, status, typeInstead: true|false }
  //   signup:   footnote text about joining
  // message: { variant: success|warning|danger|neutral, text }
  render({ app = {}, options = {}, message = null } = {}) {
    this._busy = {};
    let primaryTaken = false;
    const submit = (label) => {
      const primary = !primaryTaken;
      primaryTaken = true;
      return primary
        ? el('wa-button', { type: 'submit', variant: 'brand', class: 'cave-signin-primary' }, label)
        : el('wa-button', { type: 'submit', variant: 'neutral', appearance: 'outlined' }, label);
    };
    const form = (method, ...fields) => {
      const f = el('form', { 'data-method': method, novalidate: false }, ...fields, el('button', { type: 'submit', hidden: true }));
      f.addEventListener('submit', (event) => {
        event.preventDefault();
        this.dispatchEvent(new CustomEvent('cave-signin-submit', { bubbles: true,
          detail: { method, values: Object.fromEntries(new FormData(f)), form: f } }));
      });
      return f;
    };

    const ways = [];
    if (options.central) {
      primaryTaken = true;
      ways.push([null, el('wa-button', { class: 'cave-brand cave-signin-primary', variant: 'brand', href: options.central },
        el('img', { slot: 'start', src: icon('cougar'), alt: '', width: 16, height: 16 }), 'Sign in with Cougar Cave')]);
    }
    if (options.password) {
      const p = options.password;
      ways.push(['password', [form('password',
        el('label', {}, p.label || 'Email', el('input', { name: 'email', type: p.type || 'email', autocomplete: 'username', required: true })),
        el('label', {}, 'Password', el('input', { name: 'password', type: 'password', autocomplete: 'current-password', required: true })),
        submit('Sign in')),
        p.reset ? el('p', { class: 'cave-signin-note' }, el('a', { href: p.reset, text: 'Forgot your password?' })) : null]]);
    }
    if (options.code) {
      const c = options.code;
      ways.push(['code', form('code',
        el('label', {}, c.nameLabel || "This browser's name",
          el('input', { name: 'name', autocomplete: 'off', placeholder: c.namePlaceholder || '', required: true })),
        el('label', {}, 'Sign-in code',
          el('input', { name: 'code', autocomplete: 'one-time-code', placeholder: c.codePlaceholder || '', required: true })),
        submit('Sign in with code'))]);
    }
    if (options.pair) {
      const p = options.pair;
      ways.push(['pair', el('div', { class: 'cave-pair' },
        el('p', { text: 'On a screen that is signed in, open Devices and type this code.' }),
        el('div', { class: 'cave-pair-code', text: p.code || '' }),
        el('p', { class: 'cave-signin-note', role: 'status', text: p.status || '' }),
        p.typeInstead ? el('wa-button', { appearance: 'plain', size: 's', onclick: () =>
          this.dispatchEvent(new CustomEvent('cave-signin-submit', { bubbles: true, detail: { method: 'pair-type', values: {} } })) },
        'Type a 6-digit code instead') : null)]);
    }

    const card = el('wa-card', { class: 'cave-signin' },
      message ? el('wa-callout', { variant: message.variant || 'neutral' }, el('span', { role: 'alert', text: message.text })) : null,
      el('h1', { class: 'cave-signin-app' }, app.icon ? el('wa-icon', { src: icon(app.icon) }) : null, el('span', { text: app.name || '' })),
      app.lede ? el('p', { text: app.lede }) : null,
      ways.length ? null : el('wa-callout', { variant: 'warning' }, 'Signing in is turned off here right now. Ask Sam.'),
      ways.map(([kind, content], i) => [i > 0 && kind ? el('div', { class: 'cave-or', text: DIVIDERS[kind](app.name || 'this app') }) : null, content]),
      options.signup ? el('p', { class: 'cave-signin-note', text: options.signup }) : null);
    this.replaceChildren(card);
  }

  setMessage(message) {
    const card = this.querySelector('.cave-signin');
    if (!card) return;
    card.querySelector(':scope > wa-callout')?.remove();
    if (message) card.prepend(el('wa-callout', { variant: message.variant || 'neutral' }, el('span', { role: 'alert', text: message.text })));
  }

  setBusy(method, busy) {
    const button = this.querySelector(`form[data-method="${method}"] wa-button[type="submit"]`);
    if (button) button.loading = !!busy;
  }
}

// ---------- Settings drawer ----------
class CaveSettings extends HTMLElement {
  connectedCallback() {
    if (this._drawer) return;
    const sections = [...this.querySelectorAll(':scope > section[data-section]')];
    sections.push(this.appearanceSection());
    this._nav = el('nav', { class: 'cave-settings-nav', 'aria-label': 'Settings sections' });
    this._pane = el('div', { class: 'cave-settings-pane' });
    for (const section of sections) {
      const id = section.dataset.section;
      this._nav.append(el('button', { type: 'button', 'data-section': id, onclick: () => this.select(id) },
        el('wa-icon', { src: icon(section.dataset.icon || 'settings') }), el('span', { text: section.dataset.label || id })));
      this._pane.append(section);
    }
    this._drawer = el('wa-drawer', { class: 'cave-settings', label: this.getAttribute('label') || 'Settings', placement: 'end' }, this._nav, this._pane);
    this.replaceChildren(this._drawer);
    this.select(sections[0].dataset.section);
  }

  appearanceSection() {
    const theme = window.caveTheme;
    const section = el('section', { 'data-section': 'appearance', 'data-label': 'Appearance', 'data-icon': 'palette' },
      el('h3', { text: 'Appearance' }),
      el('p', { text: 'Applies to every Cougar Cave app on this device.' }));
    if (!theme) return section;
    const grid = el('div', { class: 'cave-themes', role: 'group', 'aria-label': 'Theme' });
    const follow = el('wa-switch', {}, "Match this device's light or dark setting");
    const sync = () => {
      const now = theme.get();
      grid.querySelectorAll('button').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.theme === now.theme)));
      follow.checked = now.follow;
    };
    for (const t of theme.themes) {
      const swatch = el('span', { class: `swatch cave-swatch-${t.name}` }, el('span'), el('span'), el('span'),
        t.name === 'original' ? [el('span'), el('span')] : null);
      grid.append(el('button', { type: 'button', 'data-theme': t.name, onclick: () => theme.set(t.name) },
        swatch, el('span', { text: t.name === 'cougar' ? 'Cougar (default)' : t.label })));
    }
    follow.addEventListener('change', () => theme.set(null, follow.checked));
    document.addEventListener('cave-theme-change', sync);
    section.append(grid, follow);
    sync();
    return section;
  }

  select(id) {
    this._pane.querySelectorAll(':scope > [data-section]').forEach((s) => s.toggleAttribute('data-active', s.dataset.section === id));
    this._nav.querySelectorAll('button').forEach((b) => b.setAttribute('aria-current', String(b.dataset.section === id)));
  }

  show(id) {
    if (!this._drawer) this.connectedCallback();
    if (id) this.select(id);
    this._drawer.open = true;
  }

  hide() { if (this._drawer) this._drawer.open = false; }
}

// Only one menu open at a time: on touch screens a tap on a second menu doesn't count as a click outside the first.
document.addEventListener('wa-show', (event) => {
  if (event.target.localName !== 'wa-dropdown') return;
  document.querySelectorAll('wa-dropdown[open]').forEach((other) => { if (other !== event.target && !other.contains(event.target)) other.open = false; });
});

customElements.define('cave-account', CaveAccount);
customElements.define('cave-signin', CaveSignin);
customElements.define('cave-settings', CaveSettings);
