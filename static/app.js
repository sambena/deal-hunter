"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
let state = { watches: [], machines: [], settings: {}, poller: {} };

// Source keys, in form order, and how they're shown.
const SOURCES = { ebay: "eBay", ebay_local: "eBay local pickup", ksl: "KSL Classifieds", craigslist: "Craigslist", offerup: "OfferUp", reddit: "Reddit", slickdeals: "Slickdeals",
  buildapcsales: "r/buildapcsales", bestbuy: "Best Buy open-box" };
const NEW_WATCH_SOURCES = ["ebay", "ebay_local", "ksl", "craigslist", "offerup", "reddit", "slickdeals", "buildapcsales"];
const sourceName = s => SOURCES[s] || s;
// Each watch's colour: the one picked, else from this palette by id (the Android app uses the same rule).
const WATCH_PALETTE = ["#3B82F6", "#F97316", "#A855F7", "#14B8A6", "#EC4899", "#EAB308", "#22C55E", "#EF4444",
  "#06B6D4", "#8B5CF6"];
const watchColor = w => (w && w.color) || WATCH_PALETTE[((w ? w.id : 0) % 10 + 10) % 10];
const colorOf = id => watchColor(state.watches.find(w => w.id === id) || { id });
function stored(key, fallback) {
  try { const v = localStorage.getItem(key); return v === null ? fallback : JSON.parse(v); } catch { return fallback; }
}
function store(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch {} }
// My hardware covers everything owned. PCs and servers list their parts; anything else is a make/model.
const KINDS = { pc: "PC", server: "Server", tv: "TV", monitor: "Monitor", phone: "Phone", tablet: "Tablet",
  audio: "Speaker / audio", network: "Network", console: "Game console", printer: "Printer", appliance: "Appliance",
  "smart home": "Smart home", vehicle: "Vehicle", other: "Other" };
const kindOptions = cur => Object.entries(KINDS).map(([k, label]) =>
  `<option value="${esc(k)}" ${k === cur ? "selected" : ""}>${esc(label)}</option>`).join("");
// Best Buy's API branding guidelines: their logo, linked, wherever their data appears.
const BESTBUY_CREDIT = `<a class="bby-credit" href="https://developer.bestbuy.com" target="_blank" rel="noopener">
  <img src="https://developer.bestbuy.com/images/bestbuy-logo.png" alt="Best Buy Developer API"></a>`;

const CATEGORIES = ["cpu", "motherboard", "ram", "gpu", "storage", "psu", "cooler", "case", "network", "other"];

// ---- helpers ------------------------------------------------------------------

async function api(method, path, body) {
  const write = method !== "GET";  // the server only accepts JSON writes
  const res = await fetch(path, {
    method,
    headers: write ? { "Content-Type": "application/json" } : {},
    body: write ? JSON.stringify(body || {}) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (res.status === 401) { location.reload(); throw new Error("Please sign in"); }  // the server shows the sign-in page
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

// "Get the Android app": shown wherever .app-link appears, once a build has been published.
async function showAppLinks() {
  try {
    const app = await api("GET", "/api/app/android");
    if (!app.available) return;
    const note = [app.version && `version ${app.version}`, app.size && `${(app.size / 1e6).toFixed(1)} MB`,
      app.published_at && `updated ${new Date(app.published_at * 1000).toLocaleDateString()}`].filter(Boolean).join(" · ");
    $$(".app-link").forEach(p => { $(".muted", p).textContent = note ? `(${note})` : ""; p.hidden = false; });
  } catch {}
}

// ---- people (admin) ---------------------------------------------------------------

async function fillPeople() {
  if (!state.me || state.me.role !== "admin") return;
  const { users, invites } = await api("GET", "/api/admin/users");
  $("#people-list").innerHTML = `<div class="table-wrap"><table class="people">
      <tr><th>Person</th><th>Watches</th><th>Your AI</th><th>Last active</th><th></th></tr>
      ${users.map(u => `<tr data-id="${u.id}">
        <td><b>${esc(u.name)}</b>${u.role === "admin" ? ' <span class="pill">admin</span>' : ""}${u.disabled ? ' <span class="pill">disabled</span>' : ""}<br>
          <span class="muted">${esc(u.email)}</span></td>
        <td>${u.watches} of ${u.role === "admin" ? "∞" : `<input class="limit" type="number" min="0" value="${u.watch_limit ?? ""}" placeholder="∞">`}</td>
        <td>${u.role === "admin" ? usd(u.ai_spent) : `
          <label class="toggle"><input type="checkbox" class="ai-shared" ${u.ai_shared ? "checked" : ""}> shared</label>
          ${u.ai_source === "own" ? '<span class="pill">own key</span>' : ""}<br>
          <span class="muted">today ${u.ai_today} of</span> <input class="ai-cap" type="number" min="0" value="${u.ai_daily_cap}" title="requests a day"><br>
          <span class="muted">${usd(u.ai_spent)} of $</span><input class="ai-allowance" type="number" min="0" step="0.5" value="${u.ai_allowance}" title="paid AI a month, US $"> <span class="muted">a month</span>`}</td>
        <td>${u.last_active ? ago(u.last_active) : "never"}</td>
        <td class="row">${u.role === "admin" ? "" : `
          <button type="button" class="small" data-act="reset">Reset password</button>
          <button type="button" class="small ${u.disabled ? "" : "danger"}" data-act="${u.disabled ? "enable" : "disable"}">${u.disabled ? "Enable" : "Disable"}</button>`}</td>
      </tr>`).join("")}
    </table></div>
    ${invites.length ? `<h3>Waiting to be used</h3>${invites.map(i => `<div class="row device" data-invite="${i.id}">
      <span>${i.kind === "reset" ? "Password reset" : "Invite"}${i.note ? `: ${esc(i.note)}` : ""}<br>
        <span class="muted">expires ${new Date(i.expires_at * 1000).toLocaleDateString()}</span></span>
      <span class="spacer"></span><button type="button" class="small danger" data-act="cancel-invite">Cancel</button></div>`).join("")}` : ""}`;
}

function showLink(link) {
  $("#invite-link").value = link;
  $("#invite-result").hidden = false;
  $("#invite-link").select();
}

$("#invite-new").addEventListener("click", e => busy(e.target, async () => {
  const { link } = await api("POST", "/api/admin/invites",
    { note: $("#invite-note").value.trim(), watch_limit: $("#invite-limit").value });
  showLink(link);
  $("#invite-note").value = "";
  fillPeople();
}));

$("#invite-copy").addEventListener("click", async () => {
  try { await navigator.clipboard.writeText($("#invite-link").value); toast("Link copied"); }
  catch { toast("Select the link and copy it yourself", true); }
});

$("#people-list").addEventListener("click", async e => {
  const btn = e.target.closest("[data-act]");
  if (!btn) return;
  const act = btn.dataset.act;
  if (act === "cancel-invite") {
    await api("DELETE", `/api/admin/invites/${btn.closest("[data-invite]").dataset.invite}`);
    return fillPeople();
  }
  const id = btn.closest("tr[data-id]").dataset.id;
  if (act === "reset") {
    const { link } = await api("POST", `/api/admin/users/${id}/reset`);
    showLink(link);
    toast("Send them this reset link");
  } else {
    if (act === "disable" && !confirm("Disable this person? They're signed out everywhere and can't sign in until you enable them.")) return;
    await api("PUT", `/api/admin/users/${id}`, { disabled: act === "disable" });
  }
  fillPeople();
});

$("#people-list").addEventListener("change", async e => {
  const t = e.target, field = t.matches("input.limit") ? "watch_limit" : t.matches(".ai-shared") ? "ai_shared"
    : t.matches(".ai-cap") ? "ai_daily_cap" : t.matches(".ai-allowance") ? "ai_allowance" : null;
  if (!field) return;
  await api("PUT", `/api/admin/users/${t.closest("tr[data-id]").dataset.id}`,
    { [field]: t.type === "checkbox" ? t.checked : t.value });
  toast(field === "watch_limit" ? "Watch limit saved" : "AI limits saved");
  fillPeople();
});

async function fillAccount() {
  const me = state.me || {};
  $("#account-who").textContent = `${me.name} · ${me.email}${me.role === "admin" ? " · admin" : ""}`;
  $("#acct-name").value = me.name || "";
  $("#acct-email").value = me.email || "";
  $("#acct-current").value = $("#acct-new").value = "";
  const { tokens } = await api("GET", "/api/tokens");
  $("#acct-devices").innerHTML = tokens.length ? tokens.map(t => `<div class="row device" data-id="${t.id}">
      <span><b>${esc(t.name)}</b>${t.current ? " (this one)" : ""}<br><span class="muted">${esc(t.kind)} · signed in ${ago(t.created_at)} · last used ${ago(t.last_used)}</span></span>
      <span class="spacer"></span>${t.current ? "" : `<button type="button" class="small danger" data-act="revoke">Sign out</button>`}</div>`).join("")
    : `<p class="muted">Nowhere else.</p>`;
}

$("#acct-devices").addEventListener("click", async e => {
  const btn = e.target.closest("[data-act=revoke]");
  if (!btn) return;
  await api("DELETE", `/api/tokens/${btn.closest(".device").dataset.id}`);
  toast("Signed out there");
  fillAccount();
});

$("#acct-save").addEventListener("click", e => busy(e.target, async () => {
  const body = { name: $("#acct-name").value.trim() };
  if ($("#acct-email").value.trim() !== (state.me.email || "")) body.email = $("#acct-email").value.trim();
  if ($("#acct-new").value) body.new_password = $("#acct-new").value;
  if (body.email || body.new_password) body.current_password = $("#acct-current").value;
  await api("POST", "/api/me", body);
  await refresh();
  fillAccount();
  toast("Account saved");
}));

$("#sign-out").addEventListener("click", async () => {
  await api("POST", "/api/auth/logout");
  location.reload();
});

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// Listing links come from other sites; only http(s) ones are followed (no javascript: links).
function safeUrl(u) {
  return /^https?:\/\//i.test(u || "") ? u : "#";
}

// Listing images come from other sites; keep them to http(s) and out of the CSS string.
function cssUrl(u) {
  return /^https?:\/\//i.test(u || "") ? `url("${encodeURI(u).replace(/["()\\]/g, c => "%" + c.charCodeAt(0).toString(16))}")` : "none";
}

// Small dollar amounts: "$0.0042", "$0.12", "$3.50".
function usd(v) {
  v = Number(v) || 0;
  if (v === 0) return "$0";
  return "$" + (v < 0.01 ? v.toPrecision(2) : v.toFixed(2));
}

// Every AI button asks the server what the request will cost first. It refuses when the
// worst case would pass the monthly limit, and otherwise shows the cost and asks (Settings > AI).
async function aiGate(action, body = {}) {
  const e = await api("POST", "/api/ai/estimate", { action, ...body });
  if (!e.allowed) throw new Error(e.reason);
  if (e.free || !state.settings.ai_confirm) return true;
  return confirm(`${e.action_label} with ${e.model_label}

` +
    `This will cost about ${usd(e.typical)} (at most ${usd(e.max)}).
` +
    `This month: ${usd(e.spent)} of your ${usd(e.limit)} limit used.

Go ahead?`);
}

function money(v) { return v == null ? "price?" : "$" + Number(v).toFixed(v % 1 ? 2 : 0); }

function ago(ts) {
  if (!ts) return "never";
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return "just now";
  if (s < 3600) return Math.floor(s / 60) + "m ago";
  if (s < 86400) return Math.floor(s / 3600) + "h ago";
  return Math.floor(s / 86400) + "d ago";
}

function toast(msg, isErr = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = isErr ? "err" : "";
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.hidden = true), isErr ? 7000 : 3500);
}

async function busy(btn, fn) {
  const label = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Working…";
  try { return await fn(); }
  catch (e) { toast(e.message, true); }
  finally { btn.disabled = false; btn.textContent = label; }
}

const aiOn = () => state.settings.ai_provider && state.settings.ai_provider !== "off";

// ---- tabs -----------------------------------------------------------------------

function showTab(name) {
  $$("nav button").forEach(b => b.classList.toggle("active", b.dataset.tab === name));
  $$(".tab").forEach(t => (t.hidden = t.id !== "tab-" + name));
  try { localStorage.setItem("tab", name); } catch {}
  if (name === "finds") loadListings();
  if (name === "settings") fillSettings();
}
$$("nav button").forEach(b => b.addEventListener("click", () => showTab(b.dataset.tab)));

// ---- state ----------------------------------------------------------------------

async function refresh() {
  state = await api("GET", "/api/state");
  const newTotal = state.watches.reduce((n, w) => n + w.new_count, 0);
  $("#new-badge").hidden = !newTotal;
  $("#new-badge").textContent = newTotal;
  document.title = newTotal ? `(${newTotal}) Deal Hunter` : "Deal Hunter";
  const p = state.poller;
  $("#poll-status").textContent = p.running ? "Checking…" :
    `Last check ${ago(p.last_cycle)}` + (p.next_cycle ? ` · next in ${Math.max(0, Math.round((p.next_cycle - state.now) / 60))}m` : "");
  const f = p.fetches || {};
  $("#poll-status").title = f.made ? `Last full check: ${f.made} searches sent, ${f.shared || 0} answered from an identical search` : "";
  renderWatchSelects();
  renderWatches();
  $("#ai-draft").hidden = !aiOn();
}

function renderWatchSelects() {
  const sel = $("#f-watch");
  const cur = sel.value;
  sel.innerHTML = `<option value="">All watches</option>` +
    state.watches.map(w => `<option value="${w.id}">${esc(w.name)}${w.new_count ? ` (${w.new_count} new)` : ""}</option>`).join("");
  sel.value = cur;
  const msel = $("#watch-form [name=machine_id]");
  const mcur = msel.value;
  msel.innerHTML = `<option value="">None</option>` + state.machines.map(m => `<option value="${m.id}">${esc(m.name)}</option>`).join("");
  msel.value = mcur;
}

// ---- finds ----------------------------------------------------------------------

async function loadListings() {
  const q = new URLSearchParams({ watch: $("#f-watch").value, status: $("#f-status").value, sort: $("#f-sort").value });
  const { listings } = await api("GET", "/api/listings?" + q);
  const box = $("#listings");
  $("#f-group").checked = stored("groupFinds", true);
  if (!listings.length) {
    box.className = "cards";
    box.innerHTML = `<div class="empty">${state.watches.length ? "Nothing here yet. New matches show up after each check."
      : "No watches yet. Add one on the Watches tab, or add your machines under My hardware to find upgrades."}</div>`;
    return;
  }
  if (!$("#f-group").checked || $("#f-watch").value) {
    box.className = "cards";
    box.innerHTML = listings.map(findCard).join("");
    return;
  }
  // Grouped: one foldable section per watch, in the order the sort puts their first find.
  const groups = new Map();
  for (const l of listings) {
    if (!groups.has(l.watch_id)) groups.set(l.watch_id, []);
    groups.get(l.watch_id).push(l);
  }
  const folded = new Set(stored("foldedWatches", []));
  box.className = "groups";
  box.innerHTML = [...groups].map(([wid, ls]) => {
    const fresh = ls.filter(l => l.status === "new").length;
    const shut = folded.has(wid);
    return `<section class="group" data-group="${wid}">
      <button type="button" class="group-head" aria-expanded="${!shut}">
        <span class="fold">${shut ? "▸" : "▾"}</span><span class="dot" style="background:${colorOf(wid)}"></span>
        <b>${esc(ls[0].watch_name)}</b><span class="meta">${ls.length} find${ls.length === 1 ? "" : "s"}${fresh ? ` · <b class="new-count">${fresh} new</b>` : ""}</span>
      </button>
      <div class="cards"${shut ? " hidden" : ""}>${ls.map(findCard).join("")}</div>
    </section>`;
  }).join("");
}

function findCard(l) {
  const pct = l.deal_pct;
  const label = pct == null ? "" : pct >= 0.25 ? "great" : pct >= 0.10 ? "good" : "";
  const deal = label ? `<span class="deal ${label}">${Math.round(pct * 100)}% under typical</span>` : "";
  const ship = l.shipping ? ` <span class="meta">(${money(l.price)} + ${money(l.shipping)} ship)</span>` : "";
  return `<div class="card ${l.status}" data-id="${l.id}" data-watch="${l.watch_id}" style="border-left-color:${colorOf(l.watch_id)}">
    ${l.image ? `<a href="${esc(safeUrl(l.url))}" target="_blank" rel="noopener" class="img" style="background-image:${esc(cssUrl(l.image))}"></a>`
      : `<a href="${esc(safeUrl(l.url))}" target="_blank" rel="noopener" class="img none">${esc(l.buying || l.source)}</a>`}
    <div class="body">
      <a class="title" href="${esc(safeUrl(l.url))}" target="_blank" rel="noopener">${esc(l.title)}</a>
      <div><span class="price">${l.source === "reddit" && l.total != null ? "≈" : ""}${money(l.total)}</span>${ship}${deal}
        ${l.source === "reddit" ? `<span class="meta">${l.total == null ? "see post" : "guessed from post"}</span>` : ""}</div>
      <div class="meta">${esc(sourceName(l.source))} · ${esc(l.condition || "")} ${l.location ? "· " + esc(l.location) : ""}</div>
      ${l.source === "bestbuy" ? BESTBUY_CREDIT : ""}
      <div class="meta"><a href="#" class="watch-link" data-edit-watch="${l.watch_id}" title="Edit this watch">${esc(l.watch_name)} ✎</a> · found ${ago(l.first_seen)}</div>
      ${l.ai_note ? `<div class="ai-note">${esc(l.ai_note)}</div>` : ""}
    </div>
    <div class="actions">
      <button class="small" data-act="${l.status === "starred" ? "seen" : "starred"}">${l.status === "starred" ? "★ Starred" : "☆ Star"}</button>
      <button class="small" data-act="${l.status === "dismissed" ? "seen" : "dismissed"}">${l.status === "dismissed" ? "Restore" : "Dismiss"}</button>
      ${aiOn() ? `<button class="small" data-act="ai">Ask AI</button>` : ""}
      <button class="small" data-act="exclude" title="Exclude a word from this watch, hiding finds like this one">Not this…</button>
      <button class="small" data-act="maxprice" title="Set this watch's highest price">Max $…</button>
    </div>
  </div>`;
}

$("#listings").addEventListener("click", e => {
  const head = e.target.closest(".group-head");
  if (!head) return;
  const group = head.closest(".group");
  const wid = Number(group.dataset.group);
  const cards = $(".cards", group);
  cards.hidden = !cards.hidden;
  head.setAttribute("aria-expanded", String(!cards.hidden));
  $(".fold", head).textContent = cards.hidden ? "▸" : "▾";
  const folded = new Set(stored("foldedWatches", []));
  cards.hidden ? folded.add(wid) : folded.delete(wid);
  store("foldedWatches", [...folded]);
});
$("#watch-form [name=color]").addEventListener("input", () => { $("#watch-form [name=color_auto]").checked = false; });
$("#f-group").addEventListener("change", e => { store("groupFinds", e.target.checked); loadListings(); });

$("#listings").addEventListener("click", async e => {
  const btn = e.target.closest("button[data-act]");
  const card = e.target.closest(".card");
  if (!card) return;
  const id = card.dataset.id;
  const watch = state.watches.find(w => w.id === Number(card.dataset.watch));
  if (e.target.closest("[data-edit-watch]")) {
    e.preventDefault();
    if (watch) editWatch(watch, "finds");
    return;
  }
  if (btn && btn.dataset.act === "maxprice") {
    if (!watch) return;
    const now = watch.max_price != null ? `Now $${watch.max_price}.` : "No max price yet.";
    const v = prompt(`Highest price for "${watch.name}", shipping included. ${now}\nFinds above it are removed; leave blank for no limit.`,
      watch.max_price ?? "");
    if (v === null) return;
    const max = v.replace(/[$,\s]/g, "");
    if (max && !(Number(max) > 0)) return toast("Enter a price like 400", true);
    const r = await api("PUT", `/api/watches/${watch.id}`, { max_price: max });
    toast((max ? `Max $${max}` : "No max price") + ` · ${r.removed} find${r.removed === 1 ? "" : "s"} removed`);
    await Promise.all([loadListings(), refresh()]);
    return;
  }
  if (btn && btn.dataset.act === "exclude") {
    if (!watch) return;
    const title = $(".title", card).textContent;
    const term = (prompt(`Hide finds like this from "${watch.name}".\n\n${title}\n\nWord or phrase to exclude:`,
      suggestExclude(title, watch)) || "").trim();
    if (!term) return;
    const r = await api("PUT", `/api/watches/${watch.id}`, { exclude: [...watch.exclude, term] });
    toast(`Excluding "${term}" · ${r.removed} find${r.removed === 1 ? "" : "s"} removed`);
    await Promise.all([loadListings(), refresh()]);
    return;
  }
  if (!btn) {
    // Opening a listing marks it seen.
    if (e.target.closest("a") && card.classList.contains("new")) {
      api("POST", `/api/listings/${id}`, { status: "seen" }).then(refresh);
      card.classList.remove("new");
    }
    return;
  }
  if (btn.dataset.act === "ai") {
    await busy(btn, async () => {
      if (!await aiGate("judge", { listing_id: Number(id) })) return;
      await api("POST", `/api/listings/${id}/ask-ai`);
      await Promise.all([loadListings(), refresh()]);
    });
    return;
  }
  await api("POST", `/api/listings/${id}`, { status: btn.dataset.act });
  await Promise.all([loadListings(), refresh()]);
});

["#f-watch", "#f-status", "#f-sort"].forEach(s => $(s).addEventListener("change", loadListings));

$("#mark-seen").addEventListener("click", async () => {
  await api("POST", "/api/listings/mark-seen", { watch: $("#f-watch").value || null });
  await Promise.all([loadListings(), refresh()]);
});

$("#poll-now").addEventListener("click", async () => {
  await api("POST", "/api/poll");
  toast("Checking all watches…");
  setTimeout(() => refresh().then(loadListings), 4000);
});

// A first guess at what makes a find unwanted: a whole-system word if there is one, else the first
// word of the title that isn't part of the watch's search.
const SYSTEM_WORDS = ["tower", "desktop", "workstation", "computer", "laptop", "notebook", "bundle", "combo",
  "motherboard", "build", "system", "server", "pc"];
function suggestExclude(title, watch) {
  const words = title.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
  const query = watch.query.toLowerCase();
  const sys = SYSTEM_WORDS.find(w => words.includes(w) && !query.includes(w));
  if (sys) return sys;
  return words.find(w => w.length > 2 && !/^\d+$/.test(w) && !query.includes(w) && !watch.exclude.includes(w)) || "";
}

// ---- watches --------------------------------------------------------------------

function renderWatches() {
  const box = $("#watch-list");
  if (!state.watches.length) { box.innerHTML = `<p class="muted">No watches yet.</p>`; return; }
  const machineName = id => (state.machines.find(m => m.id === id) || {}).name;
  box.innerHTML = `<div class="table-wrap"><table>
    <tr><th>On</th><th>Name / query</th><th>Price range</th><th>Finds</th><th>Best</th><th>Checked</th><th></th></tr>
    ${state.watches.map(w => `<tr data-id="${w.id}">
      <td><input type="checkbox" data-act="toggle" ${w.enabled ? "checked" : ""}></td>
      <td><span class="dot" style="background:${watchColor(w)}"></span><b>${esc(w.name)}</b><br><code>${esc(w.query)}</code>
        ${w.exclude.length ? `<span class="muted"> not: ${esc(w.exclude.join(", "))}</span>` : ""}
        ${w.machine_id ? `<br><span class="muted">for ${esc(machineName(w.machine_id) || "?")}</span>` : ""}
        ${w.notes ? `<br><span class="muted">${esc(w.notes)}</span>` : ""}
        ${w.last_error ? `<div class="error">${esc(w.last_error)}</div>` : ""}</td>
      <td>${w.min_price != null ? money(w.min_price) : "$0"} – ${w.max_price != null ? money(w.max_price) : "any"}${w.keep_cheapest ? ` · cheapest ${w.keep_cheapest}` : ""}<br>
        <span class="muted">${esc(w.condition)} · ${esc(w.sources.map(sourceName).join(", "))}</span></td>
      <td>${w.new_count ? `<b>${w.new_count} new</b> / ` : ""}${w.total_count}</td>
      <td>${w.best_price != null ? money(w.best_price) : "–"}</td>
      <td>${ago(w.last_polled)}</td>
      <td class="row">
        <button class="small" data-act="view">View</button>
        <button class="small" data-act="run">Check</button>
        <button class="small" data-act="edit">Edit</button>
        <button class="small danger" data-act="delete">Delete</button>
      </td></tr>`).join("")}
  </table></div>`;
}

$("#watch-list").addEventListener("click", async e => {
  const el = e.target.closest("[data-act]");
  const row = e.target.closest("tr[data-id]");
  if (!el || !row) return;
  const id = Number(row.dataset.id);
  const w = state.watches.find(x => x.id === id);
  switch (el.dataset.act) {
    case "toggle":
      await api("PUT", `/api/watches/${id}`, { enabled: el.checked });
      break;
    case "view":
      $("#f-watch").value = id;
      $("#f-status").value = "active";
      showTab("finds");
      return;
    case "run":
      await busy(el, async () => {
        const r = await api("POST", `/api/watches/${id}/run`);
        toast(`${r.new} new` + (r.errors.length ? ` · ${r.errors.join("; ")}` : ""), r.errors.length > 0);
      });
      break;
    case "edit":
      editWatch(w);
      return;
    case "delete":
      if (!confirm(`Delete "${w.name}" and all its finds?`)) return;
      await api("DELETE", `/api/watches/${id}`);
      break;
  }
  refresh();
});

let watchReturn = null;  // the tab to go back to after saving, when editing started from a find

function editWatch(w, returnTo = null) {
  watchReturn = w ? returnTo : null;
  const f = $("#watch-form");
  f.reset();
  f.id.value = w?.id || "";
  if (w) {
    f.name.value = w.name;
    f.query.value = w.query;
    f.exclude.value = w.exclude.join(", ");
    f.min_price.value = w.min_price ?? "";
    f.max_price.value = w.max_price ?? "";
    if (w.keep_cheapest && ![...f.keep_cheapest.options].some(o => o.value === String(w.keep_cheapest)))
      f.keep_cheapest.add(new Option(String(w.keep_cheapest), String(w.keep_cheapest)));  // set from the app
    f.keep_cheapest.value = w.keep_cheapest ? String(w.keep_cheapest) : "";
    f.condition.value = w.condition;
    f.color_auto.checked = !w.color;
    f.color.value = watchColor(w).toLowerCase();
    f.machine_id.value = w.machine_id ?? "";
    f.include_auctions.checked = w.include_auctions;
    Object.keys(SOURCES).forEach(s => (f["src-" + s].checked = w.sources.includes(s)));
  }
  $("#watch-form-title").textContent = w ? `Edit "${w.name}"` : "New watch";
  $("#watch-cancel").hidden = !w;
  showTab("watches");
  f.scrollIntoView({ behavior: "smooth" });
}

$("#watch-cancel").addEventListener("click", () => {
  const back = watchReturn;
  editWatch(null);
  if (back) showTab(back);
});

function formToWatch(f) {
  return {
    name: f.name.value.trim(),
    query: f.query.value.trim(),
    exclude: f.exclude.value.split(",").map(s => s.trim()).filter(Boolean),
    min_price: f.min_price.value,
    max_price: f.max_price.value,
    keep_cheapest: f.keep_cheapest.value ? Number(f.keep_cheapest.value) : null,
    color: f.color_auto.checked ? null : f.color.value,
    condition: f.condition.value,
    machine_id: f.machine_id.value ? Number(f.machine_id.value) : null,
    include_auctions: f.include_auctions.checked,
    sources: Object.keys(SOURCES).filter(s => f["src-" + s].checked),
  };
}

$("#watch-form").addEventListener("submit", async e => {
  e.preventDefault();
  const f = e.target;
  const btn = $("button[type=submit]", f);
  await busy(btn, async () => {
    const data = formToWatch(f);
    const back = watchReturn;
    if (f.id.value) {
      const u = await api("PUT", `/api/watches/${f.id.value}`, data);
      const r = await api("POST", `/api/watches/${f.id.value}/run`);
      toast(`Saved · ${u.removed ? `${u.removed} removed · ` : ""}${r.new} new` +
        (r.errors.length ? ` · ${r.errors.join("; ")}` : ""), r.errors.length > 0);
    } else {
      const r = await api("POST", "/api/watches", data);
      toast(`Watch added · ${r.new} found` + (r.errors.length ? ` · ${r.errors.join("; ")}` : ""), r.errors.length > 0);
    }
    editWatch(null);
    await refresh();
    if (back) showTab(back);
  });
});

// Suggestions (from rules or AI) share one renderer with an "Add watch" button each.
function renderSuggestions(box, suggestions, machineId) {
  if (!suggestions.length) { box.innerHTML += `<p class="muted">No suggestions.</p>`; return; }
  const existing = new Set(state.watches.map(w => w.query.toLowerCase()));
  box.innerHTML += suggestions.map((s, i) => `<div class="suggestion" data-i="${i}">
    <div><b>${esc(s.name)}</b> <span class="pill">${esc(s.category)}</span><br>
      <code>${esc(s.query || "(no query)")}</code>${s.exclude.length ? ` <span class="muted">not: ${esc(s.exclude.join(", "))}</span>` : ""}<br>
      <span class="muted">${esc(s.reason)}</span></div>
    <div class="row">
      <input class="max" type="number" placeholder="max $" value="${s.max_price ?? ""}">
      ${existing.has((s.query || "").toLowerCase()) ? `<span class="muted">already watching</span>` :
        `<button class="small primary" ${s.query ? "" : "disabled"}>Add watch</button>`}
    </div></div>`).join("");
  $$(".suggestion", box).forEach(row => {
    const btn = $("button", row);
    if (!btn) return;
    btn.addEventListener("click", () => busy(btn, async () => {
      const s = suggestions[row.dataset.i];
      const r = await api("POST", "/api/watches", {
        name: s.name, query: s.query, exclude: s.exclude, max_price: $(".max", row).value,
        condition: s.category === "ram" || s.category === "cpu" ? "any" : "any",
        sources: NEW_WATCH_SOURCES, machine_id: machineId ?? null, notes: s.reason,
      });
      btn.replaceWith(Object.assign(document.createElement("span"), { className: "muted", textContent: `added · ${r.new} found` }));
      await refresh();
    }));
  });
}

$("#ai-draft-btn").addEventListener("click", e => busy(e.target, async () => {
  const box = $("#ai-draft-results");
  if (!await aiGate("draft", { description: $("#ai-desc").value })) return;
  box.innerHTML = "";
  const { suggestions } = await api("POST", "/api/ai/draft-watches", { description: $("#ai-desc").value });
  renderSuggestions(box, suggestions, null);
}));

// ---- hardware -------------------------------------------------------------------

function partRow(p = { category: "cpu", model: "" }) {
  return `<div class="part-row">
    <select data-f="category">${CATEGORIES.map(c => `<option ${c === p.category ? "selected" : ""}>${c}</option>`).join("")}</select>
    <input data-f="model" value="${esc(p.model)}" placeholder="e.g. ASUS Prime X299-A II">
    <button class="small danger" data-act="rm-part">✕</button></div>`;
}

function renderMachines() {
  const box = $("#machines"), want = $("#kind-filter").value;
  const order = Object.keys(KINDS);
  const list = state.machines.filter(m => !want || (m.kind || "pc") === want)
    .sort((a, b) => order.indexOf(a.kind || "pc") - order.indexOf(b.kind || "pc") || a.name.localeCompare(b.name));
  box.innerHTML = list.map(m => machineCard(m)).join("") ||
    `<p class="muted">${state.machines.length ? "Nothing of that kind." : "Nothing here yet. Add a device, or import from Home Assistant."}</p>`;
}

$("#kind-filter").insertAdjacentHTML("beforeend", kindOptions(""));
$("#kind-filter").addEventListener("change", renderMachines);
$("#machines").addEventListener("change", e => {
  if (e.target.matches("[data-f=kind]")) e.target.closest(".machine").dataset.kind = e.target.value;
});

// Deal radar: a proposed watch for every device (rules for PCs, one AI request for the rest); the user ticks.
const RADAR_SKIP = ["pc", "server", "smart home", "vehicle"];

$("#radar").addEventListener("click", e => busy(e.target, async () => {
  const box = $("#radar-box");
  const gear = state.machines.filter(m => !RADAR_SKIP.includes(m.kind || "pc") && m.model);
  let useAi = false;
  if (gear.length && aiOn()) {
    // If AI is refused (limit, no key), still show the free PC suggestions.
    try { useAi = await aiGate("radar"); } catch (err) { toast(err.message, true); }
  }
  const { items, skipped } = await api("POST", "/api/radar", { use_ai: useAi });
  if (!items.length && !skipped.length) { box.innerHTML = `<p class="muted">Add some devices first.</p>`; return; }
  box.innerHTML = `<h3>Deal radar</h3>
    <p class="muted">One watch per device: the best drop-in upgrade for PCs, and ${useAi ? "AI's pick" : "nothing yet (AI is off or skipped)"}
      for everything else. Edit the search or price, untick what you don't want, then create them. Each new
      watch searches all your sources from then on.</p>
    ${items.length ? `<div class="table-wrap"><table class="radar">
      <tr><th></th><th>Device</th><th>Watch for</th><th>Search</th><th>Max $</th></tr>
      ${items.map((s, i) => `<tr data-i="${i}">
        <td><input type="checkbox" ${s.already ? "" : "checked"}></td>
        <td>${esc(s.machine_name)}<br><span class="pill">${s.from === "ai" ? "AI" : "rules"}</span></td>
        <td><b>${esc(s.name.replace(/ for .*$/, ""))}</b><br><span class="muted">${esc(s.reason)}</span>
          ${s.already ? `<br><span class="muted">(already watching)</span>` : ""}</td>
        <td><input data-f="query" value="${esc(s.query)}"></td>
        <td><input data-f="max" type="number" min="0" value="${s.max_price ?? ""}"></td></tr>`).join("")}
    </table></div>` : ""}
    ${skipped.length ? `<p class="muted">Skipped: ${skipped.map(k => `${esc(k.name)} (${esc(k.why)})`).join("; ")}</p>` : ""}
    <div class="row">${items.length ? `<button class="primary" id="radar-go">Create ticked watches</button>` : ""}
      <button id="radar-cancel">Close</button></div>`;
  $("#radar-cancel").onclick = () => (box.innerHTML = "");
  const go = $("#radar-go");
  if (!go) return;
  go.onclick = ev => busy(ev.target, async () => {
    const rows = $$("tr[data-i]", box).filter(r => $("input[type=checkbox]", r).checked);
    for (const r of rows) {
      const s = items[r.dataset.i];
      const label = s.name.includes(s.machine_name) ? s.name : `${s.name} for ${s.machine_name}`;
      await api("POST", "/api/watches", {
        name: label, query: $("[data-f=query]", r).value.trim() || s.query, exclude: s.exclude || [],
        max_price: $("[data-f=max]", r).value, condition: "any", sources: NEW_WATCH_SOURCES,
        machine_id: s.machine_id, notes: s.reason, check: false,
      });
    }
    if (rows.length) await api("POST", "/api/poll");  // one background check for all of them
    box.innerHTML = "";
    toast(`Created ${rows.length} watch${rows.length === 1 ? "" : "es"}; checking them now`);
    await refresh();
  });
}));

// Import from Home Assistant: a ticked list of its devices, sorted into kinds; nothing is added until confirmed.
$("#ha-import").addEventListener("click", e => busy(e.target, async () => {
  const box = $("#ha-import-box");
  const { devices } = await api("GET", "/api/import/ha");
  if (!devices.length) { box.innerHTML = `<p class="muted">Everything Home Assistant knows about is already here.</p>`; return; }
  box.innerHTML = `<p class="muted">${devices.length} devices from Home Assistant. Ticked ones look worth tracking;
      smart plugs and sensors are left unticked. Computers are grouped from their network card, ping check and wake
      button; ones you already have are linked rather than added again, and new ones need <b>Get specs</b> afterwards.</p>
    <div class="table-wrap"><table class="import">
      <tr><th></th><th>Name</th><th>Kind</th><th>Make / model</th><th>Area</th></tr>
      ${devices.map((d, i) => `<tr data-i="${i}">
        <td><input type="checkbox" ${d.checked ? "checked" : ""}></td>
        <td><input data-f="name" value="${esc(d.name)}"></td>
        <td><select data-f="kind">${kindOptions(d.kind)}</select></td>
        <td>${d.computer
          ? (d.match_id ? `Computer · links to <b>${esc(d.match_name)}</b>` : `Computer · new, specs needed`) +
            (d.model ? ` · ${esc(d.model)}` : "") +
            (d.parts.length ? `<br><span class="muted">${esc(d.parts.map(p => p.model).join(", "))}</span>` : "")
          : esc([d.make, d.model].filter(Boolean).join(" "))}${d.already ? ` <span class="muted">(already added)</span>` : ""}</td>
        <td class="muted">${esc(d.area)}</td></tr>`).join("")}
    </table></div>
    <div class="row"><button class="primary" id="ha-import-go">Add ticked devices</button>
      <button id="ha-import-cancel">Cancel</button></div>`;
  $("#ha-import-cancel").onclick = () => (box.innerHTML = "");
  $("#ha-import-go").onclick = ev => busy(ev.target, async () => {
    const picked = $$("tr[data-i]", box).filter(r => $("input[type=checkbox]", r).checked).map(r => ({
      ...devices[r.dataset.i], name: $("[data-f=name]", r).value.trim(), kind: $("[data-f=kind]", r).value }));
    const { added, linked } = await api("POST", "/api/import/ha", { devices: picked });
    box.innerHTML = "";
    toast(`Added ${added} device${added === 1 ? "" : "s"}` + (linked ? `, linked ${linked} computer${linked === 1 ? "" : "s"} you already had` : ""));
    await refresh();
    renderMachines();
  });
}));

// A PC or server with no CPU listed yet (e.g. just imported from Home Assistant).
const specsNeeded = m => ["pc", "server"].includes(m.kind || "pc") && !m.parts.some(p => p.category === "cpu");

// The user's own fields on a device (serial number, warranty, where it lives...).
function customRow(f = { label: "", value: "" }) {
  return `<div class="custom-row">
    <input data-f="label" value="${esc(f.label)}" placeholder="Field, e.g. Serial number">
    <input data-f="value" value="${esc(f.value)}" placeholder="Value">
    <button class="small danger" data-act="rm-custom">✕</button></div>`;
}

function machineCard(m) {
  const kind = m.kind || "pc";
  const needed = m.id && specsNeeded(m);
  return `<div class="panel machine" data-id="${m.id ?? ""}" data-kind="${esc(kind)}">
    <div class="grid">
      <label>Name <input data-f="name" value="${esc(m.name)}" placeholder="X299 box, Living room TV…"></label>
      <label>Kind <select data-f="kind">${kindOptions(kind)}</select></label>
      <label>Make <input data-f="make" value="${esc(m.make || "")}" placeholder="LG, Dell, Apple…"></label>
      <label>Model <input data-f="model" value="${esc(m.model || "")}" placeholder="OLED65B2AUA, OptiPlex 7050…"></label>
      <label>Year <input data-f="year" type="number" min="1950" max="2100" value="${m.year ?? ""}" placeholder="2022"></label>
      <label>MSRP ($) <input data-f="msrp" type="number" min="0" step="0.01" value="${m.msrp ?? ""}"></label>
      <label>Purchased <input data-f="purchased" type="date" value="${esc(m.purchased || "")}"></label>
      <label>Price paid ($) <input data-f="price_paid" type="number" min="0" step="0.01" value="${m.price_paid ?? ""}"></label>
      <label>Notes <input data-f="notes" value="${esc(m.notes)}" placeholder="use, size, PSU wattage, limits…"></label>
    </div>
    <div class="custom">${(m.custom || []).map(customRow).join("")}</div>
    ${needed ? `<p class="specs-needed parts-only">Specs needed: press <b>Get specs</b> to fill in this computer's parts.</p>` : ""}
    <div class="parts parts-only">${(m.parts.length ? m.parts : [{ category: "cpu", model: "" }, { category: "motherboard", model: "" }, { category: "ram", model: "" }]).map(partRow).join("")}</div>
    <div class="row">
      <button class="small parts-only" data-act="add-part">+ Part</button>
      <button class="small" data-act="add-custom">+ Custom field</button>
      <span class="spacer"></span>
      <button data-act="save">Save</button>
      <button class="parts-only ${needed ? "primary" : ""}" data-act="get-specs">Get specs</button>
      <button class="primary parts-only" data-act="suggest">Find upgrades</button>
      <button class="primary gear-only" data-act="watch-model">Watch this model</button>
      ${aiOn() ? `<button data-act="suggest-ai">Find upgrades with AI</button>` : ""}
      ${m.id ? `<button class="danger" data-act="delete">Delete</button>` : ""}
    </div>
    <div class="specs-box" hidden></div>
    <div class="results"></div>
  </div>`;
}

// Get specs: a one-line command to run on the computer, whose output (or any text) is pasted back.
let specCommands = null;

async function openSpecs(card) {
  const box = $(".specs-box", card);
  if (!box.hidden) { box.hidden = true; return; }
  specCommands = specCommands || await api("GET", "/api/specs/commands");
  const id = card.dataset.id || "new";
  const name = $("[data-f=name]", card).value.trim() || "this computer";
  box.innerHTML = `<p class="muted">Run one of these on <b>${esc(name)}</b>, then paste what it prints below.
      Nothing is installed and nothing connects back to Deal Hunter.</p>
    <div class="row os-pick">
      <label><input type="radio" name="os-${esc(id)}" value="windows" checked> Windows (paste into PowerShell)</label>
      <label><input type="radio" name="os-${esc(id)}" value="linux"> Linux / Bazzite / SteamOS (paste into a terminal)</label>
    </div>
    <pre class="cmd"></pre>
    <div class="row"><button class="small" data-act="copy-cmd">Copy command</button></div>
    <label>Paste the output here, or any text about this computer (System Information, a receipt, your notes)
      <textarea class="specs-text" rows="6"></textarea></label>
    <div class="row"><button class="primary" data-act="fill-specs">Fill in parts</button></div>`;
  const show = () => ($(".cmd", box).textContent = specCommands[$("input[type=radio]:checked", box).value]);
  $$("input[type=radio]", box).forEach(r => r.addEventListener("change", show));
  show();
  box.hidden = false;
}

async function fillSpecs(card, btn) {
  const text = $(".specs-text", card).value.trim();
  if (!text) return toast("Paste the command's output first", true);
  await busy(btn, async () => {
    const id = await saveMachine(card);
    let res;
    try {
      res = await api("POST", `/api/machines/${id}/specs`, { text });
    } catch (e) {
      if (e.message !== "need_ai") throw e;
      if (!aiOn()) throw new Error("That isn't the command's output. Run the command, or turn on AI in Settings to read other text.");
      if (!await aiGate("specs", { machine_id: id, text })) return;
      res = await api("POST", `/api/machines/${id}/specs`, { text, use_ai: true });
    }
    toast(`Filled in ${res.found} part${res.found === 1 ? "" : "s"}${res.method === "ai" ? " (read by AI)" : ""}`);
    await refresh();
    renderMachines();
  });
}

function machineFromCard(card) {
  return {
    name: $("[data-f=name]", card).value.trim() || "Untitled",
    kind: $("[data-f=kind]", card).value,
    make: $("[data-f=make]", card).value.trim(),
    model: $("[data-f=model]", card).value.trim(),
    year: $("[data-f=year]", card).value,
    msrp: $("[data-f=msrp]", card).value,
    purchased: $("[data-f=purchased]", card).value,
    price_paid: $("[data-f=price_paid]", card).value,
    custom: $$(".custom-row", card).map(r => ({ label: $("[data-f=label]", r).value.trim(), value: $("[data-f=value]", r).value.trim() }))
      .filter(f => f.label),
    notes: $("[data-f=notes]", card).value.trim(),
    parts: $$(".part-row", card).map(r => ({ category: $("[data-f=category]", r).value, model: $("[data-f=model]", r).value.trim() })),
  };
}

async function saveMachine(card) {
  const data = machineFromCard(card);
  if (card.dataset.id) await api("PUT", `/api/machines/${card.dataset.id}`, data);
  else card.dataset.id = (await api("POST", "/api/machines", data)).id;
  await refresh();
  return Number(card.dataset.id);
}

$("#add-machine").addEventListener("click", () => {
  $("#machines").insertAdjacentHTML("afterbegin", machineCard({ name: "", notes: "", parts: [] }));
});

$("#machines").addEventListener("click", async e => {
  const btn = e.target.closest("button[data-act]");
  const card = e.target.closest(".machine");
  if (!btn || !card) return;
  const act = btn.dataset.act;
  if (act === "get-specs") return openSpecs(card).catch(err => toast(err.message, true));
  if (act === "copy-cmd") {
    try { await navigator.clipboard.writeText($(".cmd", card).textContent); toast("Command copied"); }
    catch { toast("Couldn't copy; select the command and copy it yourself", true); }
    return;
  }
  if (act === "fill-specs") return fillSpecs(card, btn);
  if (act === "watch-model") {
    const data = machineFromCard(card);
    if (!data.model) return toast("Add the model first", true);
    const fullName = [data.make, data.model].filter(Boolean).join(" ");
    const id = await saveMachine(card);
    editWatch(null);
    const f = $("#watch-form");
    f.name.value = fullName;
    f.query.value = fullName.toLowerCase().replace(/[^a-z0-9 ]+/g, " ").replace(/\s+/g, " ").trim();
    f.machine_id.value = id;
    toast("Check the query and price, then save the watch");
    return;
  }
  if (act === "add-custom") return $(".custom", card).insertAdjacentHTML("beforeend", customRow());
  if (act === "rm-custom") return btn.closest(".custom-row").remove();
  if (act === "add-part") return $(".parts", card).insertAdjacentHTML("beforeend", partRow({ category: "other", model: "" }));
  if (act === "rm-part") return btn.closest(".part-row").remove();
  if (act === "delete") {
    if (!confirm("Delete this device? Its watches are kept.")) return;
    await api("DELETE", `/api/machines/${card.dataset.id}`);
    await refresh();
    return renderMachines();
  }
  await busy(btn, async () => {
    const id = await saveMachine(card);
    if (act === "save") return toast("Saved");
    if (act === "suggest-ai" && !await aiGate("upgrades", { machine_id: id })) return;
    const res = await api("POST", `/api/machines/${id}/suggest`, { use_ai: act === "suggest-ai" });
    const box = $(".results", card);
    box.innerHTML = `<p class="muted">${res.platform ? `Platform: <b>${esc(res.platform)}</b> (${esc(res.explanation)})` : esc(res.explanation)}</p>`;
    if (!res.platform && act === "suggest" && !res.suggestions.length) {
      box.innerHTML += `<p class="muted">Put the exact motherboard or CPU model in, ${aiOn() ? "or try AI." : "or turn on AI in Settings."}</p>`;
    }
    renderSuggestions(box, res.suggestions, id);
  });
});

$$("nav button").find(b => b.dataset.tab === "hardware").addEventListener("click", renderMachines);

// ---- settings -------------------------------------------------------------------

// Model dropdowns show each model's price and what a typical button press costs with it.
function fillAiChoices() {
  const ai = state.ai, f = $("#settings-form");
  const sel = f.ai_provider;
  sel.innerHTML = `<option value="off">Off (rules only)</option>` +
    Object.entries(ai.providers).map(([k, label]) => `<option value="${k}">${esc(label)}</option>`).join("");
  const typical = (m, action, tokensIn) => (tokensIn * m.in + ai.actions[action].typical_out * m.out) / 1e6;
  $$("select[data-models]", f).forEach(ms => {
    const models = ai.models.filter(m => m.provider === ms.dataset.models);
    ms.innerHTML = `<option value="">Choose a model…</option>` + models.map(m =>
      `<option value="${esc(m.id)}">${esc(m.label)}: $${m.in} in / $${m.out} out per 1M tokens · ` +
      `Ask AI ≈ ${usd(typical(m, "judge", 800))}, suggestions ≈ ${usd(typical(m, "draft", 1200))}</option>`).join("");
  });
}

function showAiProvider() {
  const f = $("#settings-form"), p = f.ai_provider.value;
  // Members on the admin's shared AI have nothing to set up: the provider, key and model are the admin's.
  const admin = state.me && state.me.role === "admin";
  $$("[data-member]", f).forEach(el => (el.hidden = admin));
  $("#ai-own").hidden = !admin && f.ai_source.value === "shared";
  $$("[data-provider]", f).forEach(g => (g.hidden = g.dataset.provider !== p));
  $("#ai-price-note").textContent = p === "off"
    ? "Pick a mode above to see where its API key and model go, with setup steps."
    : p === "ollama" ? "" :
    "Prices are per million tokens (about 750,000 words). Thinking counts as output. " +
    "Estimates run a little high on purpose; you're charged what the provider reports.";
}

async function showAiSpend() {
  const box = $("#ai-spend");
  try {
    const u = await api("GET", "/api/ai/usage");
    const pct = u.limit ? Math.min(100, (u.spent / u.limit) * 100) : 100;
    const label = id => (state.ai.models.find(m => m.id === id) || { label: id }).label;
    if (u.source === "shared") {
      box.innerHTML = u.blocked ? `<div>${esc(u.blocked)}</div>` : `
        <div>Using <b>${esc(u.owner)}'s AI</b>${u.model_label ? `: ${esc(u.model_label)}` : ""}.</div>
        <div><b>Today: ${u.today} of ${u.daily_cap}</b> requests</div>
        <div class="meter"><span style="width:${u.daily_cap ? Math.min(100, u.today / u.daily_cap * 100).toFixed(1) : 100}%"></span></div>
        <div class="muted">${u.limit ? `Paid AI this month: ${usd(u.spent)} of the ${usd(u.limit)} ${esc(u.owner)} gave you.`
          : u.free ? "It's free to use, within the daily requests." : `Paid models need an allowance from ${esc(u.owner)}.`}</div>`;
      return;
    }
    box.innerHTML = `<div><b>This month: ${usd(u.spent)}</b> of your ${usd(u.limit)} limit${
      state.me && state.me.role === "admin" ? ", counting everyone who uses your AI" : ""}</div>
      <div class="meter"><span style="width:${pct.toFixed(1)}%"></span></div>
      ${u.breakdown.length ? `<div class="muted">${u.breakdown.map(r =>
        `${esc(state.ai.actions[r.action]?.label || r.action)} × ${r.n} with ${esc(label(r.model))}: ${usd(r.cost)}`).join("<br>")}</div>` : ""}`;
  } catch (e) { box.textContent = e.message; }
}

function fillSettings() {
  const s = state.settings, f = $("#settings-form");
  const admin = state.me && state.me.role === "admin";
  $$("[data-admin]", f).forEach(el => (el.hidden = !admin));  // shared settings are the admin's
  fillAccount().catch(() => {});
  fillPeople().catch(err => toast(err.message, true));
  fillAiChoices();
  const owner = state.ai.owner || "the admin";
  $("#ai-source-shared").textContent = `Use ${owner}'s AI (shared with you)`;
  for (const el of f.elements) {
    if (!el.name) continue;
    if (el.name.startsWith("src_")) { el.checked = !!s.sources_enabled?.[el.name.slice(4)]; continue; }
    const v = s[el.name];
    if (el.type === "checkbox") el.checked = !!v;
    else if (el.type === "password") { el.value = ""; el.placeholder = v === true ? "saved (leave blank to keep)" : (el.dataset.ph || ""); }
    else if (Array.isArray(v)) el.value = v.join("\n");
    else el.value = v ?? "";
  }
  showAiProvider();
  showAiSpend();
  const port = state.api_port;
  $("#api-status").innerHTML = (s.api_key ? "An API key is set. " : "No API key yet, so the API is off. ") +
    (port ? `Apps use <code>http://${esc(location.hostname === "localhost" ? "localhost" : "THIS-SERVER")}:${port}</code>
      (for this install, <code>http://192.168.86.82:${port}</code> on your network).` : "The API port isn't switched on in this install.");
  $("#api-key-new").textContent = s.api_key ? "Replace API key" : "Create API key";
  $("#api-key-box").hidden = true;
}

$("#api-key-new").addEventListener("click", e => busy(e.target, async () => {
  if (state.settings.api_key && !confirm("Make a new key? Apps using the old one will stop working until you update them.")) return;
  const { api_key } = await api("POST", "/api/api-key");
  $("#api-key-value").value = api_key;
  $("#api-key-box").hidden = false;
  $("#api-key-value").select();
  await refresh();
  toast("New API key made; copy it now");
}));

$("#settings-form [name=ai_provider]").addEventListener("change", showAiProvider);
$("#settings-form [name=ai_source]").addEventListener("change", showAiProvider);

$("#settings-form").addEventListener("submit", async e => {
  e.preventDefault();
  const f = e.target, out = { sources_enabled: {} };
  for (const el of f.elements) {
    if (!el.name) continue;
    if (el.name.startsWith("src_")) out.sources_enabled[el.name.slice(4)] = el.checked;
    else if (el.type === "checkbox") out[el.name] = el.checked;
    else if (el.tagName === "TEXTAREA") out[el.name] = el.value.split("\n").map(s => s.trim()).filter(Boolean);
    else if (el.type === "number") out[el.name] = Number(el.value);
    else out[el.name] = el.value.trim();
  }
  try {
    await api("PUT", "/api/settings", out);
    await refresh();
    fillSettings();
    toast("Settings saved");
  } catch (err) { toast(err.message, true); }
});

$("#test-discord").addEventListener("click", e => busy(e.target, async () => {
  await api("POST", "/api/test-discord");
  toast("Test message sent");
}));

// ---- boot -----------------------------------------------------------------------

(async () => {
  showAppLinks();
  await refresh();
  let tab = "finds";
  try { tab = localStorage.getItem("tab") || "finds"; } catch {}
  showTab(tab);
  if (tab === "hardware") renderMachines();
  setInterval(async () => {
    await refresh();
    if (!$("#tab-finds").hidden) loadListings();
  }, 60000);
})();
