"use strict";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const STRIP_PAGE = 10, STRIP_MAX = 50;

const S = {
  alias: "", origin: "", vault: "",
  queue: [],
  cats: [], catById: new Map(),
  frozen: { top: [], subs: {} },   // category order, fixed for the session
  selCats: new Set(),              // explicitly selected category ids
  tagsAll: [], tagsRecent: [], selTags: [],
  recent: [], names: [],
  page: 0,
  paired: null, prePair: null,
  stats: {}, editing: false,
  samePost: null,
};

// ------------------------------------------------------------------- utils
async function api(url, body) {
  const opts = body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  };
  const res = await fetch(url, opts);
  const out = await res.json().catch(() => ({ status: "error", error: `HTTP ${res.status}` }));
  // The viewer was restarted under this tab: go back to the start screen.
  if (res.status === 409 && !$("work").hidden) location.reload();
  return out;
}

function setStatus(msg, kind) {
  const el = $("status");
  el.textContent = msg || "";
  el.className = "status" + (kind ? " " + kind : "");
}

function normTag(t) {
  return (t || "").trim().toLowerCase().replace(/\s+/g, "-")
    .replace(/[^a-z0-9_/-]+/g, "-").replace(/-{2,}/g, "-").replace(/^[-/]+|[-/]+$/g, "");
}

function fmtSize(n) {
  if (n > 1 << 20) return (n / (1 << 20)).toFixed(1) + " MB";
  if (n > 1 << 10) return (n / (1 << 10)).toFixed(0) + " KB";
  return n + " B";
}

function fmtDuration(min) {
  if (!isFinite(min)) return "—";
  const h = Math.floor(min / 60), m = Math.round(min % 60);
  return h ? `${h}h ${m}m` : `${m}m`;
}

function typing() {
  const a = document.activeElement;
  return a && (a.tagName === "INPUT" || a.tagName === "TEXTAREA" || a.tagName === "SELECT");
}

function dialog({ title, html = "", buttons, init }) {
  return new Promise((resolve) => {
    const dlg = $("dlg");
    let done = false;
    $("dlg-title").textContent = title;
    $("dlg-body").innerHTML = html;
    const actions = $("dlg-actions");
    actions.innerHTML = "";
    for (const b of buttons) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = b.label;
      if (b.kind) btn.className = b.kind;
      btn.onclick = () => { if (done) return; done = true; dlg.close(); resolve(b.value); };
      actions.appendChild(btn);
    }
    // Esc. (Not `close`: a previous dialog's close event can arrive after this one opens.)
    dlg.oncancel = () => { if (!done) { done = true; resolve(null); } };
    dlg.showModal();
    if (init) init($("dlg-body"));
    const first = $("dlg-body").querySelector("input,select");
    (first || actions.querySelector(".primary") || actions.firstChild)?.focus();
  });
}

// ============================================================ start screen
let inspectTimer = null;

async function initStart() {
  const st = await api("/api/state");
  if (st.active) return enterWork(st.active);
  $("start").hidden = false;
  $("work").hidden = true;
  $("s-alias").value = st.alias || "";
  const last = (st.recent || [])[0];
  $("s-origin").value = last ? last.origin : "";
  $("s-vault").value = last ? last.vault : st.default_vault;
  const box = $("s-recent");
  box.innerHTML = "";
  (st.recent || []).slice(0, 5).forEach((r) => {
    const b = document.createElement("button");
    b.type = "button";
    b.innerHTML = `<b>${esc(r.origin)}</b> → ${esc(r.vault)}` +
      (r.last_used ? ` <small>· ${esc(r.last_used.slice(0, 16).replace("T", " "))}</small>` : "");
    b.onclick = () => { $("s-origin").value = r.origin; $("s-vault").value = r.vault; inspect(); };
    box.appendChild(b);
  });
  inspect();
}

async function inspect() {
  const origin = $("s-origin").value.trim(), vault = $("s-vault").value.trim();
  const sum = $("s-summary");
  $("s-begin").disabled = true;
  $("s-init").hidden = true;
  if (!origin || !vault) { sum.innerHTML = ""; return; }
  sum.textContent = "Checking…";
  const r = await api("/api/inspect", { origin, vault });
  const lines = [];
  if (r.origin.error) lines.push(`<div class="err">Origin: ${esc(r.origin.error)}</div>`);
  else lines.push(`<div><b>Origin:</b> ${esc(r.origin.path)}<br>` +
    `<span class="ok">${r.origin.count.toLocaleString()}</span> unsorted media files` +
    (r.origin.new_since_last != null ? ` · <b>${r.origin.new_since_last.toLocaleString()}</b> new since your last session` : "") +
    `</div>`);
  if (r.vault.error) lines.push(`<div class="err">Vault: ${esc(r.vault.error)}</div>`);
  else if (r.vault.initialized) lines.push(`<div><b>Vault:</b> ${esc(r.vault.path)}<br>` +
    `<span class="ok">index found</span> · ${r.vault.files.toLocaleString()} files in ` +
    `${r.vault.events.toLocaleString()} events · ${r.vault.categories} categories</div>`);
  else if (r.vault.path) {
    lines.push(`<div><b>Vault:</b> ${esc(r.vault.path)}<br><span class="err">` +
      (r.vault.exists ? "No Xolitical index here yet." : "This folder doesn't exist yet. Initializing creates it.") +
      `</span></div>`);
    $("s-init").hidden = false;
  }
  sum.innerHTML = lines.join("");
  $("s-begin").disabled = !r.ok;
}

function queueInspect() {
  clearTimeout(inspectTimer);
  inspectTimer = setTimeout(inspect, 400);
}

// folder browser
let browseTarget = null, browsePath = "";
async function browse(path) {
  const r = await api("/api/browse", { path });
  if (r.status === "error") { $("br-path").textContent = r.error; return; }
  browsePath = r.path;
  $("br-path").textContent = r.path || "Drives & shortcuts";
  $("br-up").disabled = r.parent === null;
  $("br-up").onclick = () => browse(r.parent || "");
  const list = $("br-list");
  list.innerHTML = "";
  r.dirs.forEach((d) => {
    const div = document.createElement("div");
    div.textContent = "📁 " + d.name;
    div.onclick = () => browse(d.path);
    list.appendChild(div);
  });
  if (!r.dirs.length) list.innerHTML = `<div style="color:var(--muted)">(no subfolders)</div>`;
  $("br-choose").disabled = !r.path;
}

document.querySelectorAll("[data-browse]").forEach((b) => {
  b.onclick = () => {
    browseTarget = b.dataset.browse;
    $("dlg-browse").showModal();
    browse($(browseTarget).value.trim());
  };
});
$("br-choose").onclick = () => { $(browseTarget).value = browsePath; $("dlg-browse").close(); inspect(); };
$("br-cancel").onclick = () => $("dlg-browse").close();
["s-origin", "s-vault"].forEach((id) => $(id).addEventListener("input", queueInspect));

$("s-init").onclick = async () => {
  const r = await api("/api/init", { vault: $("s-vault").value.trim() });
  if (r.status !== "ok") { $("s-summary").innerHTML = `<div class="err">${esc(r.error)}</div>`; return; }
  inspect();
};

$("s-begin").onclick = async () => {
  const alias = $("s-alias").value.trim();
  if (!alias) { $("s-alias").focus(); $("s-summary").innerHTML = `<div class="err">Enter your alias first.</div>`; return; }
  $("s-begin").disabled = true;
  $("s-begin").textContent = "Starting…";
  const r = await api("/api/begin", { origin: $("s-origin").value.trim(), vault: $("s-vault").value.trim(), alias });
  $("s-begin").textContent = "Begin session";
  if (r.status !== "ok") { $("s-summary").innerHTML = `<div class="err">${esc(r.error)}</div>`; $("s-begin").disabled = false; return; }
  enterWork(r);
};

// ============================================================= work screen
function enterWork(b) {
  S.alias = b.alias; S.origin = b.origin; S.vault = b.vault;
  S.queue = b.queue;
  setCats(b.categories);
  freezeOrder();
  S.tagsAll = b.tags.all; S.tagsRecent = b.tags.recent;
  S.recent = b.events.recent; S.names = b.events.names;
  S.stats = b.stats;
  S.page = 0;
  $("start").hidden = true;
  $("work").hidden = false;
  $("empty-path").textContent = S.origin;
  resetForm();
  renderAll();
  focusEvent();
}

function setCats(list) {
  S.cats = list;
  S.catById = new Map(list.map((c) => [c.id, c]));
  for (const id of [...S.selCats]) if (!S.catById.has(id)) S.selCats.delete(id);
}

function parents() { return S.cats.filter((c) => !c.parent); }
function childrenOf(pid) { return S.cats.filter((c) => c.parent === pid); }
const byName = (a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: "base" });
const byUse = (a, b) => (b.count - a.count) || byName(a, b);

function freezeOrder() {
  const used = parents().filter((c) => c.count > 0).sort(byUse);
  S.frozen.top = used.slice(0, 5).map((c) => c.id);
  S.frozen.subs = {};
  for (const p of parents()) S.frozen.subs[p.id] = childrenOf(p.id).sort(byUse).map((c) => c.id);
}

function orderedSubs(pid) {
  const fixed = (S.frozen.subs[pid] || []).filter((id) => S.catById.get(id)?.parent === pid);
  const extra = childrenOf(pid).filter((c) => !fixed.includes(c.id)).sort(byName).map((c) => c.id);
  return [...fixed, ...extra];
}

function renderAll() {
  renderCounter();
  renderStrip();
  renderCats();
  renderTags();
  renderCurrent();
}

// ----------------------------------------------------------------- counter
function renderCounter() {
  const st = S.stats;
  const remaining = S.queue.length;
  $("c-sorted").textContent = (st.sorted_total ?? 0).toLocaleString();
  $("c-session").textContent = st.sorted_session ? `+${st.sorted_session}` : "";
  $("c-remaining").textContent = remaining.toLocaleString();
  $("c-new").textContent = st.new_since_last == null ? "—" : st.new_since_last.toLocaleString();
  $("c-avg").textContent = st.avg_per_session ?? "—";
  $("c-rate").textContent = st.per_minute ?? "—";
  $("c-eta").textContent = st.per_minute ? fmtDuration(remaining / st.per_minute) : "—";
}

$("c-refresh").onclick = async () => {
  const r = await api("/api/stats");
  if (r.status === "error") return setStatus(r.error, "err");
  S.stats = r.stats;
  setCats(r.categories);
  freezeOrder();
  renderCounter();
  renderCats();
  setStatus("Averages recomputed and categories re-ranked.", "ok");
};

$("c-end").onclick = endSession;
$("empty-end").onclick = endSession;
async function endSession() {
  await api("/api/end", {});
  $("empty").hidden = true;
  initStart();
}

// ------------------------------------------------------------------- strip
function stripEvents() { return S.recent.slice(0, STRIP_MAX); }

function renderStrip() {
  const evs = stripEvents();
  const maxPage = Math.max(0, Math.ceil(evs.length / STRIP_PAGE) - 1);
  S.page = Math.min(S.page, maxPage);
  const start = S.page * STRIP_PAGE;
  const box = $("strip");
  box.innerHTML = "";
  for (let i = 0; i < STRIP_PAGE; i++) {
    const ev = evs[start + i];
    const card = document.createElement("div");
    card.className = "ev";
    if (!ev) { card.classList.add("placeholder"); box.appendChild(card); continue; }
    if (S.paired?.id === ev.id) card.classList.add("paired");
    const stack = document.createElement("div");
    stack.className = "stack";
    const files = ev.files.slice(0, 3);
    const n = files.length;
    // back cards first; the newest file sits in front
    files.slice().reverse().forEach((f, j) => {
      const depth = n - 1 - j;                     // 0 = front
      const x = depth * 6, y = (n - 1 - depth) * 6;
      const img = document.createElement("img");
      img.className = "card";
      img.loading = "lazy";
      img.src = "/thumb?sha=" + f.sha256;
      img.style.left = (n === 1 ? 6 : x) + "px";
      img.style.top = (n === 1 ? 6 : y) + "px";
      img.style.zIndex = String(3 - depth);
      img.onerror = () => {
        const ph = document.createElement("div");
        ph.className = "card";
        ph.style.cssText = img.style.cssText;
        ph.textContent = f.type === "video" ? "▶" : f.type === "pdf" ? "📄" : "?";
        img.replaceWith(ph);
      };
      stack.appendChild(img);
    });
    const key = document.createElement("span");
    key.className = "key";
    key.textContent = String((i + 1) % 10);
    stack.appendChild(key);
    if (ev.count > 1) {
      const c = document.createElement("span");
      c.className = "count";
      c.textContent = ev.count;
      stack.appendChild(c);
    }
    const title = document.createElement("div");
    title.className = "title";
    title.textContent = ev.name;
    card.append(stack, title);
    card.onclick = () => togglePair(ev.id);
    card.onmouseenter = (e) => showTip(e, ev);
    card.onmousemove = moveTip;
    card.onmouseleave = hideTip;
    box.appendChild(card);
  }
  $("strip-prev").disabled = S.page === 0;
  $("strip-next").disabled = S.page >= maxPage;
  $("strip-page").textContent = evs.length
    ? `${start + 1}–${Math.min(start + STRIP_PAGE, evs.length)} of ${evs.length} recent events` : "no events yet";
}

function showTip(e, ev) {
  const t = $("tooltip");
  const cats = ev.categories.map((id) => {
    const c = S.catById.get(id);
    if (!c) return null;
    const p = c.parent ? S.catById.get(c.parent) : null;
    return p ? `${p.name} › ${c.name}` : c.name;
  }).filter(Boolean);
  t.innerHTML = `<b>${esc(ev.name)}</b><small>${ev.count} file${ev.count === 1 ? "" : "s"}` +
    (ev.tags.length ? " · #" + ev.tags.map(esc).join(" #") : "") + `</small>` +
    (cats.length ? `<small>${cats.map(esc).join(" · ")}</small>` : "");
  t.hidden = false;
  moveTip(e);
}
function moveTip(e) {
  const t = $("tooltip");
  const x = Math.min(e.clientX + 14, window.innerWidth - t.offsetWidth - 8);
  t.style.left = x + "px";
  t.style.top = (e.clientY + 16) + "px";
}
function hideTip() { $("tooltip").hidden = true; }

function pageStrip(d) {
  const maxPage = Math.max(0, Math.ceil(stripEvents().length / STRIP_PAGE) - 1);
  S.page = Math.max(0, Math.min(maxPage, S.page + d));
  renderStrip();
}
$("strip-prev").onclick = () => pageStrip(-1);
$("strip-next").onclick = () => pageStrip(1);

// ----------------------------------------------------------------- pairing
async function eventDetail(id) {
  const local = S.recent.find((e) => e.id === id);
  if (local) return local;
  const r = await api("/api/event?id=" + encodeURIComponent(id));
  return r.status === "ok" ? r.event : null;
}

async function togglePair(id) {
  if (S.paired?.id === id) return unpair();
  await pairWith(id);
}

async function pairWith(id) {
  const ev = await eventDetail(id);
  if (!ev) return setStatus("That event no longer exists.", "err");
  if (S.paired) unpair(false);
  S.prePair = { cats: new Set(S.selCats), tags: [...S.selTags], name: $("f-event").value };
  S.paired = { id: ev.id, name: ev.name, count: ev.count };
  for (const c of ev.categories) S.selCats.add(c);
  S.selTags = [...new Set([...ev.tags, ...S.selTags])];
  $("f-event").value = ev.name;
  $("f-event").classList.remove("invalid");
  closeAc("ac-events");
  renderPairState();
  renderStrip();
  renderCats();
  renderTags();
}

function unpair(render = true) {
  if (!S.paired) return;
  const pre = S.prePair || { cats: new Set(), tags: [], name: "" };
  S.paired = null;
  S.prePair = null;
  S.selCats = pre.cats;
  S.selTags = pre.tags;
  $("f-event").value = pre.name;
  if (render) { renderPairState(); renderStrip(); renderCats(); renderTags(); }
}

function renderPairState() {
  const el = $("pair-state");
  el.innerHTML = "";
  if (S.paired) {
    const renamed = $("f-event").value.trim() !== S.paired.name;
    el.innerHTML = `<span class="tag-paired">⛓ paired</span> <span>joins a ${S.paired.count}-file event` +
      (renamed ? ` · <b>will rename</b>` : "") + `</span>`;
    const b = document.createElement("button");
    b.type = "button"; b.textContent = "✕ unpair"; b.onclick = () => unpair();
    el.appendChild(b);
  } else {
    el.textContent = "new event";
  }
}

// event-name autocomplete (only when not paired; while paired the box renames the event)
let acIndex = -1;
$("f-event").addEventListener("input", () => {
  if ($("f-event").classList.contains("invalid")) setStatus("");
  $("f-event").classList.remove("invalid");
  if (S.paired) { renderPairState(); return; }
  const q = $("f-event").value.trim().toLowerCase();
  if (!q) return closeAc("ac-events");
  const hits = S.names.filter((n) => n.name.toLowerCase().includes(q)).slice(0, 8);
  renderAc("ac-events", hits.map((n) => ({
    label: n.name, hint: `${n.date || ""} · ${n.count} file${n.count === 1 ? "" : "s"}`,
    pick: () => pairWith(n.id),
  })));
});
$("f-event").addEventListener("keydown", (e) => acKeys(e, "ac-events"));
$("f-event").addEventListener("blur", () => setTimeout(() => closeAc("ac-events"), 150));

function renderAc(id, items) {
  const box = $(id);
  box.innerHTML = "";
  acIndex = -1;
  if (!items.length) { box.hidden = true; return; }
  items.forEach((it, i) => {
    const d = document.createElement("div");
    d.innerHTML = `<span>${esc(it.label)}</span>` + (it.hint ? `<small>${esc(it.hint)}</small>` : "");
    d.onmousedown = (e) => { e.preventDefault(); it.pick(); };
    d._pick = it.pick;
    box.appendChild(d);
  });
  box.hidden = false;
}
function closeAc(id) { $(id).hidden = true; acIndex = -1; }
function acKeys(e, id) {
  const box = $(id);
  if (e.key === "Escape") { closeAc(id); return; }
  if (box.hidden) return false;
  const rows = [...box.children];
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    acIndex = (acIndex + (e.key === "ArrowDown" ? 1 : -1) + rows.length) % rows.length;
    rows.forEach((r, i) => r.classList.toggle("active", i === acIndex));
    return true;
  }
  if (e.key === "Enter" && !e.ctrlKey && acIndex >= 0) {
    e.preventDefault();
    rows[acIndex]._pick();
    return true;
  }
  return false;
}

// -------------------------------------------------------------------- tags
function toggleTag(t) {
  const i = S.selTags.indexOf(t);
  if (i >= 0) S.selTags.splice(i, 1); else S.selTags.push(t);
  renderTags();
}

function addTag(raw) {
  const t = normTag(raw);
  if (!t) return;
  if (!S.selTags.includes(t)) S.selTags.push(t);
  if (!S.tagsAll.some((x) => x.tag === t)) S.tagsAll.push({ tag: t, count: 0 });
  $("f-tag").value = "";
  closeAc("ac-tags");
  renderTags();
}

// One chip row: selected tags first (click × to remove), then the 10 most
// recent tags that aren't selected yet (click to add).
function renderTags() {
  const box = $("tags");
  box.dataset.empty = "No tags yet. Tags are shared by every file in the event";
  box.innerHTML = "";
  S.selTags.forEach((t) => {
    const c = document.createElement("span");
    c.className = "chip on";
    c.innerHTML = `#${esc(t)}<span class="x">×</span>`;
    c.onclick = () => toggleTag(t);
    box.appendChild(c);
  });
  S.tagsRecent.slice(0, 10).filter((t) => !S.selTags.includes(t)).forEach((t) => {
    const c = document.createElement("span");
    c.className = "chip";
    c.textContent = "#" + t;
    c.onclick = () => toggleTag(t);
    box.appendChild(c);
  });
}

$("f-tag").addEventListener("input", () => {
  const q = normTag($("f-tag").value);
  if (!q) return closeAc("ac-tags");
  const hits = S.tagsAll.filter((x) => x.tag.includes(q)).slice(0, 12).map((x) => ({
    label: "#" + x.tag, hint: x.count ? String(x.count) : "", pick: () => addTag(x.tag),
  }));
  if (!S.tagsAll.some((x) => x.tag === q)) hits.push({ label: `+ create #${q}`, hint: "new", pick: () => addTag(q) });
  renderAc("ac-tags", hits);
});
$("f-tag").addEventListener("keydown", (e) => {
  if (acKeys(e, "ac-tags")) return;
  if (e.key === "Enter" && !e.ctrlKey) { e.preventDefault(); addTag($("f-tag").value); }
});
$("f-tag").addEventListener("blur", () => setTimeout(() => closeAc("ac-tags"), 150));

// -------------------------------------------------------------- categories
function isOn(id) { return S.selCats.has(id); }
function isImplied(pid) { return childrenOf(pid).some((c) => S.selCats.has(c.id)); }

function clickCat(c) {
  if (S.editing) return;
  if (c.parent) {
    S.selCats.has(c.id) ? S.selCats.delete(c.id) : S.selCats.add(c.id);
  } else if (isOn(c.id) || isImplied(c.id)) {
    S.selCats.delete(c.id);
    childrenOf(c.id).forEach((k) => S.selCats.delete(k.id));
  } else {
    S.selCats.add(c.id);
  }
  renderCats();
}

function catRow(c) {
  const row = document.createElement("div");
  row.className = "cat-row " + (c.parent ? "sub" : "parent");
  if (isOn(c.id)) row.classList.add("on");
  else if (!c.parent && isImplied(c.id)) row.classList.add("implied");
  row.innerHTML = `<span class="box">${isOn(c.id) || (!c.parent && isImplied(c.id)) ? "✓" : ""}</span>` +
    `<span class="name">${esc(c.name)}</span><span class="n">${c.count || ""}</span>`;
  const ops = document.createElement("span");
  ops.className = "ops";
  [["✎", "Rename", () => renameCat(c)],
   ["⇢", c.parent ? "Move / merge" : "Merge into…", () => mergeCat(c)],
   ["🗑", "Delete", () => deleteCat(c)]].forEach(([g, t, fn]) => {
    const b = document.createElement("button");
    b.type = "button"; b.textContent = g; b.title = t;
    b.onclick = (e) => { e.stopPropagation(); fn(); };
    ops.appendChild(b);
  });
  row.appendChild(ops);
  row.onclick = () => clickCat(c);
  return row;
}

function catGroup(p, filter) {
  const g = document.createElement("div");
  g.className = "cat-group";
  const subs = orderedSubs(p.id).map((id) => S.catById.get(id));
  const pMatch = !filter || p.name.toLowerCase().includes(filter);
  const shownSubs = pMatch ? subs : subs.filter((s) => s.name.toLowerCase().includes(filter));
  if (!pMatch && !shownSubs.length) return null;
  g.appendChild(catRow(p));
  shownSubs.forEach((s) => g.appendChild(catRow(s)));
  if (S.editing || isOn(p.id) || isImplied(p.id)) {
    const wrap = document.createElement("div");
    wrap.className = "add-sub";
    const inp = document.createElement("input");
    inp.placeholder = `+ subcategory of ${p.name}`;
    inp.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); addCat(inp.value, p.id); } };
    wrap.appendChild(inp);
    g.appendChild(wrap);
  }
  return g;
}

function renderCats() {
  const list = $("cat-list");
  const filter = $("cat-filter").value.trim().toLowerCase();
  const scroll = list.scrollTop;
  list.innerHTML = "";
  list.classList.toggle("editing", S.editing);
  $("cat-edit").classList.toggle("on", S.editing);
  $("cat-edit").textContent = S.editing ? "✓" : "✎";
  const ps = parents();
  if (!ps.length) {
    list.innerHTML = `<p style="color:var(--muted)">No categories yet. Add one below.</p>`;
    return;
  }
  const top = S.frozen.top.map((id) => S.catById.get(id)).filter((c) => c && !c.parent);
  const rest = ps.filter((c) => !top.includes(c)).sort(byName);
  const section = (label, cats) => {
    const groups = cats.map((p) => catGroup(p, filter)).filter(Boolean);
    if (!groups.length) return;
    const h = document.createElement("div");
    h.className = "cat-section";
    h.textContent = label;
    list.appendChild(h);
    groups.forEach((g) => list.appendChild(g));
  };
  if (top.length) section("Most used", top);
  section(top.length ? "All categories (A–Z)" : "Categories (A–Z)", rest);
  list.scrollTop = scroll;
}

$("cat-filter").addEventListener("input", renderCats);
$("cat-edit").onclick = () => { S.editing = !S.editing; renderCats(); };
$("cat-new").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); addCat($("cat-new").value, null); }
});

async function addCat(name, parent) {
  name = name.trim();
  if (!name) return;
  const r = await api("/api/category", { op: "add", name, parent });
  if (r.status !== "ok") return setStatus(r.error, "err");
  setCats(r.categories);
  if (!S.editing) S.selCats.add(r.id);
  if (!parent) $("cat-new").value = "";
  renderCats();
  setStatus(r.created ? `Added “${name}”.` : `“${name}” already exists, so it's selected.`, "ok");
}

async function confirmCatOp(body) {
  const pre = await api("/api/category", { ...body, dry_run: true });
  if (pre.status !== "preview") { setStatus(pre.error || "failed", "err"); return; }
  const i = pre.impact;
  const ok = await dialog({
    title: "Update the whole index?",
    html: `<p>${esc(pre.description)}.</p><div class="impact">This rewrites <b>${i.events}</b> event note${i.events === 1 ? "" : "s"} ` +
      `(${i.files} file${i.files === 1 ? "" : "s"}) and <b>${i.category_notes}</b> category note${i.category_notes === 1 ? "" : "s"} in the vault, ` +
      `and fixes links to them. Commit or back up first if you're unsure.</div>`,
    buttons: [{ label: "Apply", value: true, kind: body.op === "delete" ? "danger" : "primary" },
              { label: "Cancel", value: false }],
  });
  if (!ok) return;
  const r = await api("/api/category", body);
  if (r.status !== "ok") return setStatus(r.error, "err");
  setCats(r.categories);
  S.recent = r.events.recent;
  renderCats();
  renderStrip();
  setStatus(r.description + " ✓", "ok");
}

async function renameCat(c) {
  const v = await dialog({
    title: `Rename “${c.name}”`,
    html: `<label>New name <input id="dlg-input" value="${esc(c.name)}"></label>`,
    buttons: [{ label: "Preview changes", value: "go", kind: "primary" }, { label: "Cancel", value: null }],
    init: (b) => b.querySelector("input").select(),
  });
  const name = $("dlg-input")?.value.trim();
  if (v !== "go" || !name || name === c.name) return;
  confirmCatOp({ op: "rename", id: c.id, name });
}

async function mergeCat(c) {
  let html;
  if (!c.parent) {
    const opts = parents().filter((p) => p.id !== c.id).sort(byName)
      .map((p) => `<option value="merge:${p.id}">${esc(p.name)}</option>`).join("");
    if (!opts) return setStatus("There is no other category to merge into.", "err");
    html = `<label>Merge “${esc(c.name)}” into <select id="dlg-input">${opts}</select></label>` +
      `<p><small>Its subcategories move across too (same-named ones are merged).</small></p>`;
  } else {
    const moveOpts = parents().filter((p) => p.id !== c.parent).sort(byName)
      .map((p) => `<option value="move:${p.id}">${esc(p.name)}</option>`).join("");
    const mergeGroups = parents().sort(byName).map((p) => {
      const kids = childrenOf(p.id).filter((k) => k.id !== c.id).sort(byName);
      return kids.length ? `<optgroup label="Merge into a subcategory of ${esc(p.name)}">` +
        kids.map((k) => `<option value="merge:${k.id}">${esc(p.name)} › ${esc(k.name)}</option>`).join("") + `</optgroup>` : "";
    }).join("");
    html = `<label>“${esc(c.name)}” → <select id="dlg-input">` +
      (moveOpts ? `<optgroup label="Move under category">${moveOpts}</optgroup>` : "") + mergeGroups + `</select></label>`;
  }
  const v = await dialog({
    title: c.parent ? "Move or merge subcategory" : "Merge category",
    html, buttons: [{ label: "Preview changes", value: "go", kind: "primary" }, { label: "Cancel", value: null }],
  });
  const pick = $("dlg-input")?.value;
  if (v !== "go" || !pick) return;
  const [op, target] = pick.split(":");
  confirmCatOp(op === "move" ? { op, id: c.id, parent: target } : { op, id: c.id, into: target });
}

function deleteCat(c) { confirmCatOp({ op: "delete", id: c.id }); }

// ----------------------------------------------------------------- current
function current() { return S.queue[0]; }

function resetForm() {
  S.paired = null; S.prePair = null;
  S.selCats = new Set(); S.selTags = [];
  $("f-event").value = "";
  $("f-event").classList.remove("invalid");
  $("f-note").value = "";
  closeAc("ac-events"); closeAc("ac-tags");
}

// Stop a playing video and drop its connection so the server can move the file
// (Windows won't rename a file that is still being streamed).
function releaseMedia() {
  const m = document.querySelector("#viewer video, #viewer img, #viewer embed");
  if (!m) return;
  if (m.tagName === "VIDEO") { m.pause(); m.removeAttribute("src"); m.load(); }
  else m.removeAttribute("src");
}

function renderViewer(item) {
  const box = $("viewer");
  box.innerHTML = "";
  const url = "/file?p=" + encodeURIComponent(item.path);
  let node;
  if (item.type === "image") {
    node = document.createElement("img");
    node.src = url;
  } else if (item.type === "video") {
    node = document.createElement("video");
    Object.assign(node, { src: url, controls: true, muted: true, autoplay: true, loop: true });
  } else if (item.type === "pdf") {
    node = document.createElement("embed");
    node.src = url; node.type = "application/pdf";
  } else if (item.type === "text") {
    node = document.createElement("pre");
    fetch(url).then((r) => r.text()).then((t) => { node.textContent = t; });
  } else {
    node = document.createElement("div");
    node.className = "nopreview";
    node.textContent = "No inline preview for this file type.";
  }
  box.appendChild(node);
}

function renderDetails(item) {
  $("f-source").value = item.source || "";
  $("f-account").value = item.account || "";
  $("f-posted").value = item.posted || "";
  $("f-platform").value = item.platform || "other";
  $("f-type").value = item.type || "other";
  updateDetailSummary();
}

function updateDetailSummary() {
  const bits = [];
  const acc = $("f-account").value.trim(), posted = $("f-posted").value, src = $("f-source").value.trim();
  bits.push($("f-platform").value);
  if (acc) bits.push("@" + acc);
  if (posted) bits.push(posted);
  bits.push(src ? "source ✓" : "no source URL");
  $("d-summary").textContent = bits.join(" · ");
}
["f-source", "f-account", "f-posted", "f-platform", "f-type"].forEach((id) =>
  $(id).addEventListener("input", updateDetailSummary));

async function checkSamePost(item) {
  const banner = $("same-post");
  banner.hidden = true;
  S.samePost = null;
  if (!item.source) return;
  const r = await api("/api/same_post?source=" + encodeURIComponent(item.source));
  if (current() !== item || !r.event) return;
  S.samePost = r.event;
  banner.innerHTML = `<span>Another file from this post is already in <b>${esc(r.event.name)}</b> ` +
    `(${r.event.count} file${r.event.count === 1 ? "" : "s"}).</span>`;
  const b = document.createElement("button");
  b.type = "button"; b.textContent = "Pair with it (P)";
  b.onclick = () => pairWith(r.event.id);
  banner.appendChild(b);
  banner.hidden = false;
}

function renderCurrent() {
  renderCounter();
  renderPairState();
  const item = current();
  if (!item) { $("empty").hidden = false; return; }
  $("empty").hidden = true;
  renderViewer(item);
  $("m-name").textContent = item.path;
  $("m-info").textContent = `${fmtSize(item.size)} · created ${item.created_iso.slice(0, 16).replace("T", " ")}`;
  renderDetails(item);
  checkSamePost(item);
  // warm the next few: browser cache for images, server-side hash for everything
  S.queue.slice(1, 4).forEach((n) => { if (n.type === "image") new Image().src = "/file?p=" + encodeURIComponent(n.path); });
  api("/api/prefetch", { paths: S.queue.slice(0, 3).map((n) => n.path) });
}

// after a save / skip / undo the next file starts at the event-name box
function focusEvent() { $("f-event").focus(); }

// ------------------------------------------------------------------ save
async function save(force = false) {
  const item = current();
  if (!item) return;
  const name = $("f-event").value.trim().replace(/\s+/g, " ");
  if (!name) {
    $("f-event").classList.add("invalid");
    $("f-event").focus();
    return setStatus("Every file needs an event name.", "err");
  }
  if (!S.paired && !force) {
    const same = S.names.find((n) => n.name.toLowerCase() === name.toLowerCase());
    if (same) {
      const v = await dialog({
        title: "An event with this name exists",
        html: `<p>“${esc(same.name)}” already has ${same.count} file${same.count === 1 ? "" : "s"}. Pair this file with it?</p>`,
        buttons: [{ label: "Pair with it", value: "pair", kind: "primary" },
                  { label: "Create a separate event", value: "new" }, { label: "Cancel", value: null }],
      });
      if (!v) return;
      if (v === "pair") await pairWith(same.id);
    }
  }
  if (S.paired && name !== S.paired.name) {
    const v = await dialog({
      title: "Rename event?",
      html: `<p>Rename “${esc(S.paired.name)}” → “${esc(name)}”? Its note is renamed and links to it are updated.</p>`,
      buttons: [{ label: "Rename & save", value: true, kind: "primary" }, { label: "Cancel", value: false }],
    });
    if (!v) return;
  }
  const body = {
    path: item.path,
    event_id: S.paired?.id || null,
    event_name: $("f-event").value.trim().replace(/\s+/g, " "),
    categories: [...S.selCats],
    tags: S.selTags,
    note: $("f-note").value,
    source: $("f-source").value.trim(),
    account: $("f-account").value.trim(),
    posted: $("f-posted").value,
    platform: $("f-platform").value,
    type: $("f-type").value,
    force,
  };
  setStatus("Saving…");
  releaseMedia();
  const saveBtns = [$("b-save"), $("b-save-top")];
  saveBtns.forEach((b) => { b.disabled = true; });
  let r;
  try { r = await api("/api/save", body); } finally { saveBtns.forEach((b) => { b.disabled = false; }); }
  if (r.status !== "saved" && current() === item) renderViewer(item);  // file stayed: show it again

  if (r.status === "duplicate") {
    const v = await dialog({
      title: "Duplicate file",
      html: `<p>This exact file is already in the vault as <code>${esc(r.match.name)}</code> in event <b>${esc(r.match.event)}</b>.</p>`,
      buttons: [{ label: "Move to _duplicates", value: true, kind: "danger" }, { label: "Cancel", value: false }],
    });
    setStatus("");
    if (v) markDuplicate();
    return;
  }
  if (r.status === "same_source") {
    const v = await dialog({
      title: "Same post as an existing event",
      html: `<p>A file from this post (<code>${esc(r.match.name)}</code>) is already in <b>${esc(r.match.event)}</b>. ` +
        `This is usually another image from the same tweet.</p>`,
      buttons: [{ label: `Pair with “${r.match.event}” & save`, value: "pair", kind: "primary" },
                { label: "Save as its own event", value: "force" }, { label: "Cancel", value: null }],
    });
    setStatus("");
    if (v === "pair") { await pairWith(r.match.event_id); return save(true); }
    if (v === "force") return save(true);
    return;
  }
  if (r.status !== "saved") return setStatus(r.error || "Save failed.", "err");

  S.queue.shift();
  setCats(r.categories);
  S.tagsAll = r.tags.all; S.tagsRecent = r.tags.recent;
  S.recent = r.events.recent;
  if (r.event_entry) S.names = [r.event_entry, ...S.names.filter((n) => n.id !== r.event_entry.id)];
  S.stats = r.stats;
  S.page = 0;
  resetForm();
  renderStrip(); renderCats(); renderTags(); renderCurrent(); focusEvent();
  setStatus("Saved → " + r.note, "ok");
}

async function markDuplicate() {
  const item = current();
  if (!item) return;
  releaseMedia();
  const r = await api("/api/duplicate", { path: item.path });
  if (r.status !== "moved") renderViewer(item);
  if (r.status !== "moved") return setStatus(r.error || "Move failed.", "err");
  S.queue.shift();
  S.stats = r.stats;
  resetForm();
  renderTags(); renderCats(); renderStrip(); renderCurrent(); focusEvent();
  setStatus("Moved to " + r.to, "ok");
}

function skip() {
  if (S.queue.length < 2) return setStatus("Nothing else in the queue.");
  S.queue.push(S.queue.shift());
  resetForm();
  renderTags(); renderCats(); renderStrip(); renderCurrent(); focusEvent();
  setStatus("Skipped. It's now at the end of the queue.");
}

async function undo() {
  const r = await api("/api/undo", {});
  if (r.status !== "undone") return setStatus(r.error || "Undo failed.", "err");
  S.queue.unshift(r.item);
  setCats(r.categories);
  S.tagsAll = r.tags.all; S.tagsRecent = r.tags.recent;
  S.recent = r.events.recent; S.names = r.events.names;
  S.stats = r.stats;
  resetForm();
  const f = r.form;
  if (f.event_id) await pairWith(f.event_id);
  S.selCats = new Set(f.categories);
  S.selTags = [...f.tags];
  $("f-event").value = f.event_name;
  $("f-note").value = f.note || "";
  renderStrip(); renderCats(); renderTags(); renderCurrent();
  if (f.source !== undefined) {
    $("f-source").value = f.source; $("f-account").value = f.account;
    $("f-posted").value = f.posted; $("f-platform").value = f.platform || "other";
    $("f-type").value = f.type || "other"; updateDetailSummary();
  }
  focusEvent();
  setStatus("Undone. The file is back in the origin.", "ok");
}

$("b-save").onclick = () => save(false);
$("b-save-top").onclick = () => save(false);  // same action, placed by the strip
$("b-skip").onclick = skip;
$("b-dupe").onclick = markDuplicate;
$("b-undo").onclick = undo;

// --------------------------------------------------------------- keyboard
document.addEventListener("keydown", (e) => {
  if ($("work").hidden || document.querySelector("dialog[open]")) return;
  if (e.ctrlKey && e.key === "Enter") { e.preventDefault(); save(false); return; }
  const k = e.key.toLowerCase();
  if (e.altKey && k === "s") { e.preventDefault(); skip(); return; }
  if (e.altKey && k === "z") { e.preventDefault(); undo(); return; }
  const free = !typing();
  if ((e.altKey || free) && /^[0-9]$/.test(e.key)) {
    const i = (Number(e.key) + 9) % 10;
    const ev = stripEvents()[S.page * STRIP_PAGE + i];
    if (ev) { e.preventDefault(); togglePair(ev.id); }
    return;
  }
  if ((e.altKey || free) && (e.key === "ArrowLeft" || e.key === "ArrowRight")) {
    e.preventDefault(); pageStrip(e.key === "ArrowLeft" ? -1 : 1); return;
  }
  if (free && k === "p" && S.samePost) { e.preventDefault(); pairWith(S.samePost.id); }
});

initStart().catch((e) => { document.body.textContent = "Failed to load: " + e.message; });
