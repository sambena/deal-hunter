// The link page (link.html): after an unlinked Cougar Cave sign-in, prove which account is yours, once.
// The card is the shared Cougar Cave one (<cave-signin>, cave/cave-ui.js), showing only the email and password form.
"use strict";
const card = document.querySelector("cave-signin");

card.addEventListener("cave-signin-submit", async e => {
  const { method, values } = e.detail;
  if (method !== "password") return;
  card.setMessage(null);
  card.setBusy("password", true);
  try {
    const res = await fetch("/auth/link", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: (values.email || "").trim(), password: values.password }) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    location.replace("/");
  } catch (err) {
    card.setMessage({ variant: "danger", text: err.message });
    card.setBusy("password", false);
  }
});

(async () => {
  await customElements.whenDefined("cave-signin");
  card.render({ app: { name: "Link your Deal Hunter account", icon: "tag",
    lede: "You signed in with Cougar Cave. To use it for your existing Deal Hunter account, enter that account's " +
      "email and password once. Your watches, finds and settings stay as they are." },
    options: { password: { label: "Email", reset: null } } });
  card.querySelector('form[data-method="password"] wa-button[type=submit]').textContent = "Link and sign in";
  const p = document.createElement("p"), a = document.createElement("a");
  p.className = "cave-signin-note";
  a.href = "/";
  a.textContent = "Cancel";
  p.append(a);
  card.querySelector(".cave-signin").append(p);
})();
