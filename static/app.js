"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
let state = { watches: [], machines: [], settings: {}, poller: {} };

// Source keys, in form order, and how they're shown.
const SOURCES = { ebay: "eBay", ebay_local: "eBay local pickup", reddit: "Reddit", bestbuy: "Best Buy open-box" };
const NEW_WATCH_SOURCES = ["ebay", "ebay_local", "reddit"];
const sourceName = s => SOURCES[s] || s;

const CATEGORIES = ["cpu", "motherboard", "ram", "gpu", "storage", "psu", "cooler", "case", "other"];

// ---- helpers ------------------------------------------------------------------

async function api(method, path, body) {
  const write = method !== "GET";  // the server only accepts JSON writes
  const res = await fetch(path, {
    method,
    headers: write ? { "Content-Type": "application/json" } : {},
    body: write ? JSON.stringify(body || {}) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
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
  if (!listings.length) {
    box.innerHTML = `<div class="empty">${state.watches.length ? "Nothing here yet. New matches show up after each check."
      : "No watches yet. Add one on the Watches tab, or add your machines under My hardware to find upgrades."}</div>`;
    return;
  }
  box.innerHTML = listings.map(l => {
    const pct = l.deal_pct;
    const label = pct == null ? "" : pct >= 0.25 ? "great" : pct >= 0.10 ? "good" : "";
    const deal = label ? `<span class="deal ${label}">${Math.round(pct * 100)}% under typical</span>` : "";
    const ship = l.shipping ? ` <span class="meta">(${money(l.price)} + ${money(l.shipping)} ship)</span>` : "";
    return `<div class="card ${l.status}" data-id="${l.id}">
      ${l.image ? `<a href="${esc(l.url)}" target="_blank" rel="noopener" class="img" style="background-image:${esc(cssUrl(l.image))}"></a>`
        : `<a href="${esc(l.url)}" target="_blank" rel="noopener" class="img none">${esc(l.buying || l.source)}</a>`}
      <div class="body">
        <a class="title" href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.title)}</a>
        <div><span class="price">${l.source === "reddit" && l.total != null ? "≈" : ""}${money(l.total)}</span>${ship}${deal}
          ${l.source === "reddit" ? `<span class="meta">${l.total == null ? "see post" : "guessed from post"}</span>` : ""}</div>
        <div class="meta">${esc(sourceName(l.source))} · ${esc(l.condition || "")} ${l.location ? "· " + esc(l.location) : ""}</div>
        <div class="meta">${esc(l.watch_name)} · found ${ago(l.first_seen)}</div>
        ${l.ai_note ? `<div class="ai-note">${esc(l.ai_note)}</div>` : ""}
      </div>
      <div class="actions">
        <button class="small" data-act="${l.status === "starred" ? "seen" : "starred"}">${l.status === "starred" ? "★ Starred" : "☆ Star"}</button>
        <button class="small" data-act="${l.status === "dismissed" ? "seen" : "dismissed"}">${l.status === "dismissed" ? "Restore" : "Dismiss"}</button>
        ${aiOn() ? `<button class="small" data-act="ai">Ask AI</button>` : ""}
      </div>
    </div>`;
  }).join("");
}

$("#listings").addEventListener("click", async e => {
  const btn = e.target.closest("button[data-act]");
  const card = e.target.closest(".card");
  if (!card) return;
  const id = card.dataset.id;
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

// ---- watches --------------------------------------------------------------------

function renderWatches() {
  const box = $("#watch-list");
  if (!state.watches.length) { box.innerHTML = `<p class="muted">No watches yet.</p>`; return; }
  const machineName = id => (state.machines.find(m => m.id === id) || {}).name;
  box.innerHTML = `<div class="table-wrap"><table>
    <tr><th>On</th><th>Name / query</th><th>Price range</th><th>Finds</th><th>Best</th><th>Checked</th><th></th></tr>
    ${state.watches.map(w => `<tr data-id="${w.id}">
      <td><input type="checkbox" data-act="toggle" ${w.enabled ? "checked" : ""}></td>
      <td><b>${esc(w.name)}</b><br><code>${esc(w.query)}</code>
        ${w.exclude.length ? `<span class="muted"> not: ${esc(w.exclude.join(", "))}</span>` : ""}
        ${w.machine_id ? `<br><span class="muted">for ${esc(machineName(w.machine_id) || "?")}</span>` : ""}
        ${w.notes ? `<br><span class="muted">${esc(w.notes)}</span>` : ""}
        ${w.last_error ? `<div class="error">${esc(w.last_error)}</div>` : ""}</td>
      <td>${w.min_price != null ? money(w.min_price) : "$0"} – ${w.max_price != null ? money(w.max_price) : "any"}<br>
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

function editWatch(w) {
  const f = $("#watch-form");
  f.reset();
  f.id.value = w?.id || "";
  if (w) {
    f.name.value = w.name;
    f.query.value = w.query;
    f.exclude.value = w.exclude.join(", ");
    f.min_price.value = w.min_price ?? "";
    f.max_price.value = w.max_price ?? "";
    f.condition.value = w.condition;
    f.machine_id.value = w.machine_id ?? "";
    f.include_auctions.checked = w.include_auctions;
    Object.keys(SOURCES).forEach(s => (f["src-" + s].checked = w.sources.includes(s)));
  }
  $("#watch-form-title").textContent = w ? `Edit "${w.name}"` : "New watch";
  $("#watch-cancel").hidden = !w;
  showTab("watches");
  f.scrollIntoView({ behavior: "smooth" });
}

$("#watch-cancel").addEventListener("click", () => editWatch(null));

function formToWatch(f) {
  return {
    name: f.name.value.trim(),
    query: f.query.value.trim(),
    exclude: f.exclude.value.split(",").map(s => s.trim()).filter(Boolean),
    min_price: f.min_price.value,
    max_price: f.max_price.value,
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
    if (f.id.value) {
      await api("PUT", `/api/watches/${f.id.value}`, data);
      const r = await api("POST", `/api/watches/${f.id.value}/run`);
      toast(`Saved · ${r.new} new` + (r.errors.length ? ` · ${r.errors.join("; ")}` : ""), r.errors.length > 0);
    } else {
      const r = await api("POST", "/api/watches", data);
      toast(`Watch added · ${r.new} found` + (r.errors.length ? ` · ${r.errors.join("; ")}` : ""), r.errors.length > 0);
    }
    editWatch(null);
    await refresh();
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
  const box = $("#machines");
  box.innerHTML = state.machines.map(m => machineCard(m)).join("") ||
    `<p class="muted">No machines yet.</p>`;
}

function machineCard(m) {
  return `<div class="panel machine" data-id="${m.id ?? ""}">
    <div class="grid">
      <label>Name <input data-f="name" value="${esc(m.name)}" placeholder="X299 box"></label>
      <label>Notes (use, PSU wattage, case limits…) <input data-f="notes" value="${esc(m.notes)}"></label>
    </div>
    <div class="parts">${(m.parts.length ? m.parts : [{ category: "cpu", model: "" }, { category: "motherboard", model: "" }, { category: "ram", model: "" }]).map(partRow).join("")}</div>
    <div class="row">
      <button class="small" data-act="add-part">+ Part</button>
      <span class="spacer"></span>
      <button data-act="save">Save</button>
      <button class="primary" data-act="suggest">Find upgrades</button>
      ${aiOn() ? `<button data-act="suggest-ai">Find upgrades with AI</button>` : ""}
      ${m.id ? `<button class="danger" data-act="delete">Delete</button>` : ""}
    </div>
    <div class="results"></div>
  </div>`;
}

function machineFromCard(card) {
  return {
    name: $("[data-f=name]", card).value.trim() || "Untitled",
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
  if (act === "add-part") return $(".parts", card).insertAdjacentHTML("beforeend", partRow({ category: "other", model: "" }));
  if (act === "rm-part") return btn.closest(".part-row").remove();
  if (act === "delete") {
    if (!confirm("Delete this machine? Its watches are kept.")) return;
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
    box.innerHTML = `<div><b>This month: ${usd(u.spent)}</b> of your ${usd(u.limit)} limit</div>
      <div class="meter"><span style="width:${pct.toFixed(1)}%"></span></div>
      ${u.breakdown.length ? `<div class="muted">${u.breakdown.map(r =>
        `${esc(state.ai.actions[r.action]?.label || r.action)} × ${r.n} with ${esc(label(r.model))}: ${usd(r.cost)}`).join("<br>")}</div>` : ""}`;
  } catch (e) { box.textContent = e.message; }
}

function fillSettings() {
  const s = state.settings, f = $("#settings-form");
  fillAiChoices();
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
}

$("#settings-form [name=ai_provider]").addEventListener("change", showAiProvider);

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
