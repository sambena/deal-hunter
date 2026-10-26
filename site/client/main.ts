// Deal Hunter's page. Components are Lit, from the kit's copy (one Lit per page): import everything from
// /cave/cave-ui.js, which the server serves from @cougarcave/ui. The build leaves that import as it is.
import type { CaveBar } from '/cave/cave-ui.js';

// Signed in or not: the bar asks /auth/me and says so with cave-session.
const bar = document.querySelector('cave-bar') as CaveBar;
const signin = document.getElementById('signin') as HTMLElement;
const home = document.getElementById('home') as HTMLElement;

function show(account: unknown): void {
  home.hidden = !account;
  signin.hidden = Boolean(account);
  bar.noSignin = !account;  // the card has the sign-in button; don't show two
}

bar.addEventListener('cave-session', (event) => show((event as CustomEvent<{ account: unknown }>).detail.account));
if (bar.loaded) show(bar.account);
// Any API call that comes back 401 (the session ended elsewhere) brings the sign-in card back.
document.addEventListener('cave-signed-out', () => { bar.account = null; show(null); });
