"use strict";

const $ = (id) => document.getElementById(id);

let items = [];        // remaining queue
let total = 0;         // count at load time
let done = 0;          // saved or marked-duplicate this session
let vocab = [];        // tag vocabulary
let selected = new Set();

const el = {
  main: $("main"), empty: $("empty"), progress: $("progress"),
  preview: $("preview-box"), filename: $("filename"), filesize: $("filesize"),
  title: $("f-title"), desc: $("f-desc"), source: $("f-source"),
  platform: $("f-platform"), type: $("f-type"), account: $("f-account"), date: $("f-date"),
  tagFilter: $("tag-filter"), chips: $("tag-chips"), status: $("status"),
  dlg: $("dupe-dialog"), dlgMsg: $("dupe-msg"),
};

function current() { return items[0]; }

function fmtSize(n) {
  if (n > 1 << 20) return (n / (1 << 20)).toFixed(1) + " MB";
  if (n > 1 << 10) return (n / (1 << 10)).toFixed(0) + " KB";
  return n + " B";
}

function setStatus(msg, isErr) {
  el.status.textContent = msg || "";
  el.status.classList.toggle("err", !!isErr);
}

function renderProgress() {
  el.progress.textContent = items.length
    ? `${done + 1} of ${total} — ${items.length} remaining`
    : `${done} done — queue empty`;
}

function renderChips() {
  const q = el.tagFilter.value.trim().toLowerCase();
  el.chips.innerHTML = "";
  const shown = vocab.filter(t => !q || t.includes(q));
  for (const t of shown) {
    const c = document.createElement("span");
    c.className = "chip" + (selected.has(t) ? " on" : "");
    c.textContent = t;
    c.onclick = () => { selected.has(t) ? selected.delete(t) : selected.add(t); renderChips(); };
    el.chips.appendChild(c);
  }
  const newTag = q.replace(/\s+/g, "-");
  if (newTag && !vocab.includes(newTag)) {
    const c = document.createElement("span");
    c.className = "chip add";
    c.textContent = `+ add “${newTag}”`;
    c.onclick = () => addTag(newTag);
    el.chips.appendChild(c);
  }
}

function addTag(t) {
  if (!vocab.includes(t)) vocab.push(t);
  selected.add(t);
  el.tagFilter.value = "";
  renderChips();
}

function renderPreview(item) {
  el.preview.innerHTML = "";
  const url = "/file?p=" + encodeURIComponent(item.path);
  let node;
  if (item.type === "image") {
    node = document.createElement("img");
    node.src = url;
  } else if (item.type === "video") {
    node = document.createElement("video");
    node.src = url;
    node.controls = true;
    node.muted = true;
    node.autoplay = true;
    node.loop = true;
  } else if (item.type === "pdf") {
    node = document.createElement("embed");
    node.src = url;
    node.type = "application/pdf";
  } else {
    node = document.createElement("div");
    node.className = "nopreview";
    node.textContent = "No inline preview for this file type — open it locally if needed.";
  }
  el.preview.appendChild(node);
  el.filename.textContent = item.path;
  el.filesize.textContent = fmtSize(item.size);
}

function renderCurrent() {
  renderProgress();
  const item = current();
  if (!item) {
    el.main.hidden = true;
    el.empty.hidden = false;
    return;
  }
  el.main.hidden = false;
  el.empty.hidden = true;
  renderPreview(item);
  el.title.value = "";
  el.desc.value = "";
  el.source.value = item.source || "";
  el.platform.value = item.platform || "other";
  el.type.value = item.type || "other";
  el.account.value = item.account || "";
  el.date.value = item.date_posted || "";
  selected = new Set();
  el.tagFilter.value = "";
  renderChips();
  setStatus("");
  el.title.focus();
}

function payload(force) {
  const item = current();
  return {
    path: item.path,
    tweet_id: item.tweet_id || "",
    title: el.title.value.trim(),
    description: el.desc.value.trim(),
    source: el.source.value.trim(),
    platform: el.platform.value,
    type: el.type.value,
    account: el.account.value.trim(),
    date_posted: el.date.value,
    tags: [...selected],
    force: !!force,
  };
}

async function api(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return res.json();
}

async function save(force) {
  if (!current()) return;
  setStatus("saving…");
  try {
    const out = await api("/api/save", payload(force));
    if (out.status === "duplicate") {
      el.dlgMsg.textContent =
        `This item matches an existing note by ${out.match.key === "source" ? "source URL" : "file hash"}: ` +
        out.match.note;
      el.dlg.showModal();
      setStatus("");
      return;
    }
    if (out.status !== "saved") throw new Error(out.error || "save failed");
    done++;
    items.shift();
    renderCurrent();
    setStatus("saved → " + out.note);
  } catch (e) {
    setStatus(e.message, true);
  }
}

async function markDuplicate() {
  if (!current()) return;
  try {
    const out = await api("/api/duplicate", { path: current().path });
    if (out.status !== "moved") throw new Error(out.error || "move failed");
    done++;
    items.shift();
    renderCurrent();
    setStatus("moved to _duplicates");
  } catch (e) {
    setStatus(e.message, true);
  }
}

function skip() {
  if (items.length < 2) { setStatus("nothing else to show"); return; }
  items.push(items.shift());
  renderCurrent();
}

async function init() {
  const cfg = await (await fetch("/api/config")).json();
  $("alias").textContent = cfg.alias;
  $("unsorted-path").textContent = cfg.unsorted_dir;
  vocab = cfg.tags || [];

  const q = await (await fetch("/api/queue")).json();
  items = q.items || [];
  total = items.length;
  renderCurrent();
}

$("btn-save").onclick = () => save(false);
$("btn-skip").onclick = skip;
$("btn-dupe").onclick = markDuplicate;
$("dupe-move").onclick = () => { el.dlg.close(); markDuplicate(); };
$("dupe-force").onclick = () => { el.dlg.close(); save(true); };
$("dupe-cancel").onclick = () => el.dlg.close();

el.tagFilter.addEventListener("input", renderChips);
el.tagFilter.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    const t = el.tagFilter.value.trim().toLowerCase().replace(/\s+/g, "-");
    if (t) addTag(t);
  }
});

document.addEventListener("keydown", (e) => {
  if (e.ctrlKey && e.key === "Enter") { e.preventDefault(); save(false); }
  else if (e.altKey && e.key.toLowerCase() === "s") { e.preventDefault(); skip(); }
});

init().catch(e => { el.progress.textContent = "failed to load: " + e.message; });
