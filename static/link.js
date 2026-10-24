// The link page (link.html): after an unlinked Cougar Cave sign-in, prove which account is yours, once.
"use strict";
const form = document.querySelector("#link-form");
const error = document.querySelector("#auth-error");

form.addEventListener("submit", async e => {
  e.preventDefault();
  error.textContent = "";
  const go = document.querySelector("#go");
  go.disabled = true;
  try {
    const res = await fetch("/auth/link", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: form.email.value.trim(), password: form.password.value }) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    location.replace("/");
  } catch (err) {
    error.textContent = err.message;
  } finally {
    go.disabled = false;
  }
});
