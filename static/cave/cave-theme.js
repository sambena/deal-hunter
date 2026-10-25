// Applies the viewer's Cougar Cave theme before the page draws. Load it in <head> as a plain (non-module) script,
// before the stylesheets finish: <script src="{CAVE}/cave-theme.js"></script>
// The choice is a cookie on .cougarcave.dev, so picking a theme in one app changes every app on this device.
// On <html>, an app may set data-cave-original="<theme>": the theme it used before Cougar Cave ("original look").
(function () {
  var THEMES = [
    // name, label, which modes it has, and the mode it uses when not following the device
    { name: 'cougar', label: 'Cougar', modes: ['dark', 'light'], fixed: 'dark' },
    { name: 'cave', label: 'Cave', modes: ['dark'], fixed: 'dark' },
    { name: 'gilded', label: 'Gilded', modes: ['dark'], fixed: 'dark' },
    { name: 'slate', label: 'Slate', modes: ['dark'], fixed: 'dark' },
    { name: 'roster', label: 'Roster', modes: ['light', 'dark'], fixed: 'light' },
    { name: 'ember', label: 'Ember', modes: ['light', 'dark'], fixed: 'light' },
  ];
  var ORIGINAL = { name: 'original', label: "Each app's original look" };
  var root = document.documentElement;
  var media = window.matchMedia ? window.matchMedia('(prefers-color-scheme: light)') : null;

  function cookie(name) {
    var m = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'));
    return m ? decodeURIComponent(m[1]) : '';
  }
  function save(name, value) {
    var domain = /(^|\.)cougarcave\.dev$/.test(location.hostname) ? '; domain=.cougarcave.dev' : '';
    var secure = location.protocol === 'https:' ? '; secure' : '';
    document.cookie = name + '=' + encodeURIComponent(value) + '; path=/; max-age=31536000; samesite=lax' + domain + secure;
  }
  function find(name) {
    for (var i = 0; i < THEMES.length; i++) if (THEMES[i].name === name) return THEMES[i];
    return THEMES[0];
  }
  function get() {
    var theme = cookie('cave_theme') || 'cougar';
    if (theme !== 'original' && !THEMES.some(function (t) { return t.name === theme; })) theme = 'cougar';
    return { theme: theme, follow: cookie('cave_mode') === 'device' };
  }

  // The app's accent class (cave-accent-*) is kept in data-cave-accent so "original look" can remove and restore it.
  if (!root.hasAttribute('data-cave-accent')) {
    var accent = (root.className.match(/\bcave-accent-[a-z]+\b/) || [''])[0];
    root.setAttribute('data-cave-accent', accent);
  }

  function apply() {
    var choice = get();
    var original = choice.theme === 'original';
    var theme = find(original ? (root.getAttribute('data-cave-original') || 'cougar') : choice.theme);
    var mode = theme.fixed;
    if (choice.follow && media) {
      var want = media.matches ? 'light' : 'dark';
      if (theme.modes.indexOf(want) >= 0) mode = want;
    }
    var keep = root.className.split(/\s+/).filter(function (c) {
      return c && !/^cave-theme-/.test(c) && !/^cave-accent-/.test(c) && c !== 'wa-dark' && c !== 'wa-light';
    });
    keep.push('wa-' + mode);
    if (theme.name !== 'cougar') keep.push('cave-theme-' + theme.name);
    var accent = root.getAttribute('data-cave-accent');
    if (accent && !original) keep.push(accent);
    root.className = keep.join(' ');
    var meta = document.querySelector('meta[name="color-scheme"]');
    if (meta) meta.setAttribute('content', mode);
  }

  window.caveTheme = {
    themes: THEMES.concat([ORIGINAL]),
    get: get,
    set: function (theme, follow) {
      if (theme) save('cave_theme', theme);
      if (typeof follow === 'boolean') save('cave_mode', follow ? 'device' : 'fixed');
      apply();
      document.dispatchEvent(new CustomEvent('cave-theme-change', { detail: get() }));
    },
    apply: apply,
  };
  apply();
  if (media && media.addEventListener) media.addEventListener('change', apply);
})();
