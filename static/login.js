// The sign-in page (login.html, and oidc.py's sign-in messages): setup, sign-in, and invite or password-reset links.
// The card is the shared Cougar Cave one (<cave-signin>, cave/cave-ui.js); this only supplies what the server has
// switched on and runs Deal Hunter's own sign-in API.
"use strict";
const card = document.querySelector("cave-signin");
const link = (location.hash.match(/^#(invite|reset)=([\w-]+)$/) || []).slice(1);
const APP = { name: "Deal Hunter", icon: "tag",
  lede: "Deal Hunter watches eBay, KSL, Craigslist and more for the things you want, and tells you about the good deals." };
let mode = "login";

async function call(method, path, body) {
  const res = await fetch(path, { method, headers: body ? { "Content-Type": "application/json" } : {},
                                  body: body ? JSON.stringify(body) : undefined });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

function field(label, name, type, autocomplete) {
  const l = document.createElement("label");
  const input = document.createElement("input");
  Object.assign(input, { name, type, autocomplete, required: true });
  l.append(label, input);
  return l;
}

// Setup, invite and reset use the card's email and password form with the extra fields they need.
function adapt(form) {
  const fresh = mode === "setup" || mode === "invite";
  const email = form.querySelector("[name=email]").closest("label");
  const password = form.querySelector("[name=password]");
  if (mode === "reset") email.remove();
  if (fresh) email.before(field("Your name", "name", "text", "name"));
  if (mode !== "login") {
    password.autocomplete = "new-password";
    password.closest("label").after(field("Password again", "password2", "password", "new-password"));
  }
  form.querySelector("wa-button[type=submit]").textContent =
    { login: "Sign in", setup: "Create my account", invite: "Create my account", reset: "Save new password" }[mode];
}

function render(options, message, lede) {
  const shown = mode === "login" ? options : { password: options.password };
  const title = { login: "Deal Hunter", setup: "Set up your Deal Hunter account", invite: "Join Deal Hunter",
    reset: "Choose a new password" }[mode];
  card.render({ app: { ...APP, name: title, lede: lede || APP.lede }, options: shown, message });
  const form = card.querySelector('form[data-method="password"]');
  if (form) adapt(form);
}

async function offerApp(code) {
  // Invited friends can install the Android app before they have an account.
  try {
    const app = await call("GET", `/api/app/android?code=${encodeURIComponent(code)}`);
    if (!app.available) return;
    const p = document.createElement("p"), a = document.createElement("a");
    p.className = "cave-signin-note";
    a.href = `/download/android?code=${encodeURIComponent(code)}`;
    a.textContent = "Get the Android app";
    p.append(a, app.version ? ` (version ${app.version})` : "");
    card.querySelector(".cave-signin")?.append(p);
  } catch {}
}

card.addEventListener("cave-signin-submit", async e => {
  const { method, values } = e.detail;
  if (method !== "password") return;
  if (mode !== "login" && values.password !== values.password2)
    return card.setMessage({ variant: "danger", text: "The passwords don't match" });
  const body = { name: (values.name || "").trim(), email: (values.email || "").trim(), password: values.password };
  card.setBusy("password", true);
  try {
    if (mode === "invite" || mode === "reset") await call("POST", "/api/auth/accept", { ...body, code: link[1] });
    else await call("POST", mode === "setup" ? "/api/auth/setup" : "/api/auth/login", body);
    location.replace("/");  // also from oidc.py's message pages, which live under /auth/
  } catch (err) {
    card.setMessage({ variant: "danger", text: err.message });
    card.setBusy("password", false);
  }
});

(async () => {
  await customElements.whenDefined("cave-signin");
  const status = await call("GET", "/api/auth/status").catch(() => ({}));
  const options = status.signin || { password: { label: "Email" } };
  // A message from the server (oidc.py: sign-in refused, or signing in) or from signing out.
  let message = card.dataset.message ? { variant: card.dataset.variant || "warning", text: card.dataset.message } : null;
  try {
    if (!message && sessionStorage.getItem("dh-signed-out")) message = { variant: "success", text: "You're signed out." };
    sessionStorage.removeItem("dh-signed-out");
  } catch {}
  if (link.length) {
    try {
      const info = await call("GET", `/api/auth/link?code=${encodeURIComponent(link[1])}`);
      if (info.kind === "invite") {
        mode = "invite";
        render(options, message, "You've been invited to Deal Hunter. Pick a name, your email and a password; your watches and finds are yours alone.");
        offerApp(link[1]);
      } else {
        mode = "reset";
        render(options, message, `For ${info.email}. You'll be signed out everywhere else.`);
      }
      return;
    } catch (err) {
      history.replaceState(null, "", location.pathname);
      message = { variant: "danger", text: err.message };
    }
  }
  mode = status.setup_needed ? "setup" : "login";
  render(options, message, status.setup_needed
    ? "Welcome. You'll be the admin: pick your name, the email you'll sign in with, and a password." : "");
})();
