// The sign-in page (login.html): setup, sign-in, and invite or password-reset links.
"use strict";
const $ = s => document.querySelector(s);
const form = $("#auth-form");
const link = (location.hash.match(/^#(invite|reset)=([\w-]+)$/) || []).slice(1);
let mode = "login";

async function call(method, path, body) {
  const res = await fetch(path, { method, headers: body ? { "Content-Type": "application/json" } : {},
                                  body: body ? JSON.stringify(body) : undefined });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

function show(m, intro = "") {
  mode = m;
  const fresh = m === "setup" || m === "invite";
  document.querySelectorAll("[data-new]").forEach(el => (el.hidden = !fresh));
  document.querySelector("[data-confirm]").hidden = m === "login";
  document.querySelector("[data-email]").hidden = m === "reset";
  form.email.required = m !== "reset";
  form.password.autocomplete = m === "login" ? "current-password" : "new-password";
  $("#title").textContent = { login: "Sign in to Deal Hunter", setup: "Set up your Deal Hunter account",
    invite: "Join Deal Hunter", reset: "Choose a new password" }[m];
  $("#go").textContent = { login: "Sign in", setup: "Create my account", invite: "Create my account",
    reset: "Save new password" }[m];
  $("#intro").textContent = intro;
  form.hidden = false;
}

async function offerApp(code) {
  // Invited friends can install the Android app before they have an account.
  try {
    const app = await call("GET", `/api/app/android?code=${encodeURIComponent(code)}`);
    if (!app.available) return;
    $("#app-link").href = `/download/android?code=${encodeURIComponent(code)}`;
    $("#app-note").textContent = app.version ? `(version ${app.version})` : "";
    $("#app").hidden = false;
  } catch {}
}

form.addEventListener("submit", async e => {
  e.preventDefault();
  if (mode !== "login" && form.password.value !== form.password2.value) return ($("#auth-error").textContent = "The passwords don't match");
  const body = { name: form.name.value.trim(), email: form.email.value.trim(), password: form.password.value };
  try {
    if (mode === "invite" || mode === "reset") {
      await call("POST", "/api/auth/accept", { ...body, code: link[1] });
      history.replaceState(null, "", location.pathname);
    } else {
      await call("POST", mode === "setup" ? "/api/auth/setup" : "/api/auth/login", body);
    }
    location.reload();
  } catch (err) { $("#auth-error").textContent = err.message; }
});

(async () => {
  if (link.length) {
    try {
      const info = await call("GET", `/api/auth/link?code=${encodeURIComponent(link[1])}`);
      if (info.kind === "invite") {
        show("invite", "You've been invited to Deal Hunter. Pick a name, your email and a password; your watches and finds are yours alone.");
        offerApp(link[1]);
      } else {
        show("reset", `For ${info.email}. You'll be signed out everywhere else.`);
      }
      return;
    } catch (err) {
      history.replaceState(null, "", location.pathname);
      $("#auth-error").textContent = err.message;
    }
  }
  const status = await call("GET", "/api/auth/status").catch(() => ({}));
  show(status.setup_needed ? "setup" : "login", status.setup_needed
    ? "Welcome. You'll be the admin: pick your name, the email you'll sign in with, and a password." : "");
})();
