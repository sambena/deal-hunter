// Deal Hunter's page. The kit (/cave/cave-ui.js, served by the server from @cougarcave/ui; the build leaves the import
// as it is) gives the bar, the sign-in card, the account menu, phones and the settings drawer. Everything else is the
// Python app's page, moved over as it was (app.js).
import '/cave/cave-ui.js';
import type { CaveBar } from '/cave/cave-ui.js';
import { start } from './app.js';

// Signed in or not: the bar asks /auth/me and says so with cave-session.
const bar = document.querySelector('cave-bar') as CaveBar;
const signin = document.getElementById('signin') as HTMLElement;
const parts = ['dh-nav', 'dh-main'].map((id) => document.getElementById(id) as HTMLElement);

function show(account: unknown): void {
  for (const el of parts) el.hidden = !account;
  signin.hidden = Boolean(account);
  bar.noSignin = !account;   // the card has the sign-in button; don't show two
  if (account) void start();
}

bar.addEventListener('cave-session', (event) => show((event as CustomEvent<{ account: unknown }>).detail.account));
if (bar.loaded) show(bar.account);
// Any API call that comes back 401 (the session ended elsewhere) brings the sign-in card back.
document.addEventListener('cave-signed-out', () => { bar.account = null; show(null); });
