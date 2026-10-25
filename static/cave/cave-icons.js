// Web Awesome's built-in "system" icons (dialog close, menu checkmarks, button carets) are data: URLs that
// wa-icon fetches, which a strict CSP (connect-src 'self') refuses. Serve the same icons from a same-origin
// sprite instead, so no app has to loosen its policy. system-icons.svg is generated from the vendored kit.
// Load it in every app, after the Web Awesome loader: <script type="module" src="{CAVE}/cave-icons.js"></script>
import { registerIconLibrary } from './webawesome/webawesome.loader.js';

const sprite = new URL('system-icons.svg', import.meta.url).pathname;

registerIconLibrary('system', {
  resolver: (name, _family, variant = 'solid') => `${sprite}#${variant === 'regular' ? 'regular' : 'solid'}-${name}`,
  spriteSheet: true,
  mutator: (svg) => {
    // A sprite icon's outer <svg> has no viewBox, so it would render at 300x150. Give it a square one;
    // each symbol keeps its own viewBox and is centred inside (Font Awesome icons are at most 512 units wide).
    svg.setAttribute('viewBox', '0 0 512 512');
    if (!svg.hasAttribute('fill')) svg.setAttribute('fill', 'currentColor');
  },
});
