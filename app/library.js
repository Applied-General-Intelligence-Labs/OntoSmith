"use strict";
/* OntoSmith documents page — a thin client over the
   library routes `ontosmith serve --library` adds:
     GET    /library
     POST   /library/documents   {name, text}
     DELETE /library/documents?name=...
     POST   /library/rebuild
   Same rules as the demo app: no external assets, and all
   server text is rendered via textContent. */

const $ = (sel) => document.querySelector(sel);

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

async function call(method, url, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const resp = await fetch(url, opts);
  let data = null;
  try { data = await resp.json(); } catch { /* not JSON */ }
  if (!resp.ok) {
    const msg = data && data.error ? data.error.message : `HTTP ${resp.status}`;
    const err = new Error(msg);
    err.code = data && data.error ? data.error.code : null;
    throw err;
  }
  return data;
}

const fmt = new Intl.NumberFormat();
let state = null;
let pollTimer = null;
// Why adding is off right now (library off, or a rebuild
// running), or null. Checked by every add path, not just
// shown: a CSS class alone doesn't stop a drop or a submit.
let addBlocked = "Loading…";

function setAddBlocked(reason) {
  addBlocked = reason;
  $("#drop-zone").classList.toggle("disabled", !!reason);
  $("#file-input").disabled = !!reason;
  $("#paste-form button[type=submit]").disabled = !!reason;
}

/* ------------------------------------------------ rendering */

function renderDisabled(message) {
  $("#library-meta").replaceChildren(el("span", "error-box", message));
  setAddBlocked("the document library is off");
  $("#rebuild-btn").disabled = true;
}

function renderDocs(s) {
  const t = s.totals;
  $("#library-meta").textContent = t.documents
    ? `${fmt.format(t.documents)} document${t.documents === 1 ? "" : "s"}, `
      + `${fmt.format(t.words)} words — folder ${s.folder}`
    : `No documents yet — folder ${s.folder}`;
  const list = $("#doc-list");
  list.replaceChildren();
  if (!s.documents.length) {
    list.append(el("p", "empty", "Add a .txt file to get started."));
    return;
  }
  const table = el("table", "doc-table");
  const head = el("tr");
  for (const h of ["Name", "Words", "Added", ""]) head.append(el("th", null, h));
  table.append(head);
  for (const d of s.documents) {
    const tr = el("tr");
    tr.append(el("td", "doc-name", d.name));
    tr.append(el("td", "num", fmt.format(d.words)));
    tr.append(el("td", "muted", new Date(d.modified).toLocaleString()));
    const td = el("td", "actions");
    const rm = el("button", "linkish danger", "Remove");
    rm.type = "button";
    rm.disabled = isRunning(s);
    rm.addEventListener("click", () => removeDoc(d.name));
    td.append(rm);
    tr.append(td);
    table.append(tr);
  }
  list.append(table);
}

function fact(dl, term, value) {
  dl.append(el("dt", null, term), el("dd", null, value));
}

function capText(cap) {
  if (cap.error) return cap.error;
  if (cap.usd === null) return `no cap — ${cap.source}`;
  return `$${cap.usd.toFixed(2)} (${cap.source})`;
}

function isRunning(s) {
  return s && s.build && s.build.status === "running";
}

function renderRebuild(s) {
  const dl = $("#rebuild-facts");
  dl.replaceChildren();
  fact(dl, "documents", `${fmt.format(s.totals.documents)} (${fmt.format(s.totals.words)} words)`);
  fact(dl, "spend cap", capText(s.cap));
  fact(dl, "pack", s.pack);
  fact(dl, "serving", s.served_batch || "the graph Neo4j holds now");
  const btn = $("#rebuild-btn");
  btn.disabled = isRunning(s) || !s.totals.documents || !!s.cap.error;
  btn.textContent = isRunning(s) ? "Rebuilding…" : "Rebuild graph";
  renderBuild(s.build);
}

function renderBuild(b) {
  const body = $("#build-body");
  body.replaceChildren();
  if (!b || b.status === "idle") return;
  const badge = { running: "running", succeeded: "done", failed: "failed" }[b.status] || b.status;
  const head = el("p", `build-status ${b.status}`);
  head.append(el("span", "badge", badge), ` ${b.message || ""}`);
  body.append(head);
  const dl = el("dl", "facts");
  fact(dl, "batch", b.batch_id);
  fact(dl, "documents", `${fmt.format(b.documents)} (${fmt.format(b.words)} words)`);
  fact(dl, "started", new Date(b.started_at).toLocaleTimeString());
  if (b.finished_at) fact(dl, "finished", new Date(b.finished_at).toLocaleTimeString());
  if (b.cost_usd !== null && b.cost_usd !== undefined) fact(dl, "cost", `$${b.cost_usd.toFixed(4)}`);
  body.append(dl);
  if (b.status === "succeeded") {
    const p = el("p");
    const a = el("a", null, "Ask the new graph →");
    a.href = "/";
    p.append(a);
    body.append(p);
  }
  if (b.log && b.log.length) {
    const det = el("details", "build-log");
    det.open = b.status !== "succeeded";
    det.append(el("summary", null, `Build log (${b.log.length} lines)`));
    const pre = el("pre", null, b.log.join("\n"));
    det.append(pre);
    body.append(det);
    pre.scrollTop = pre.scrollHeight;
  }
}

/* ------------------------------------------------ data flow */

async function refresh() {
  try {
    state = await call("GET", "/library");
  } catch (err) {
    renderDisabled(err.code === "library_disabled"
      ? "The document library is off. Restart the server with "
        + "`ontosmith serve --library <folder>` to manage documents here."
      : `Couldn't load the library: ${err.message}`);
    return;
  }
  setAddBlocked(isRunning(state)
    ? "a rebuild is running — add documents once it finishes" : null);
  renderDocs(state);
  renderRebuild(state);
  clearTimeout(pollTimer);
  if (isRunning(state)) pollTimer = setTimeout(refresh, 2000);
}

function note(kind, text) {
  const box = $("#add-results");
  const p = el("p", `add-note ${kind}`, text);
  box.prepend(p);
  while (box.children.length > 8) box.lastChild.remove();
}

async function addOne(name, text) {
  try {
    const r = await call("POST", "/library/documents", { name, text });
    note("ok", `Added ${r.name} (${fmt.format(r.words)} words)`);
    return true;
  } catch (err) {
    note("bad", `${name}: ${err.message}`);
    return false;
  }
}

async function addFiles(files) {
  if (addBlocked) {
    note("bad", `Not added: ${addBlocked}`);
    return;
  }
  let added = 0;
  for (const f of files) {
    const isText = f.name.toLowerCase().endsWith(".txt") || f.type === "text/plain";
    if (!isText) {
      note("bad", `${f.name}: only plain-text (.txt) files are supported`);
      continue;
    }
    if (await addOne(f.name, await f.text())) added += 1;
  }
  if (added) await refresh();
}

async function removeDoc(name) {
  if (!confirm(`Remove ${name} from the library? The graph keeps it until the next rebuild.`)) return;
  try {
    await call("DELETE", `/library/documents?name=${encodeURIComponent(name)}`);
    note("ok", `Removed ${name}`);
  } catch (err) {
    note("bad", `${name}: ${err.message}`);
  }
  await refresh();
}

async function rebuild() {
  if (!state) return;
  const t = state.totals;
  const ok = confirm(
    `Rebuild the graph from all ${t.documents} document(s), ${fmt.format(t.words)} words?\n\n`
    + `This calls the model (spend cap: ${capText(state.cap)}) and replaces `
    + `the graph Neo4j holds now.`);
  if (!ok) return;
  try {
    // An empty JSON body: the server takes JSON writes only.
    await call("POST", "/library/rebuild", {});
  } catch (err) {
    note("bad", `Rebuild: ${err.message}`);
  }
  await refresh();
}

/* ------------------------------------------------ wiring */

// A file dropped outside the zone (or on it while it is
// disabled) must not make the browser open it instead.
for (const type of ["dragover", "drop"]) {
  window.addEventListener(type, (ev) => ev.preventDefault());
}
const zone = $("#drop-zone");
zone.addEventListener("dragover", (ev) => { ev.preventDefault(); zone.classList.add("over"); });
zone.addEventListener("dragleave", () => zone.classList.remove("over"));
zone.addEventListener("drop", (ev) => {
  ev.preventDefault();
  zone.classList.remove("over");
  addFiles([...ev.dataTransfer.files]);
});
$("#file-input").addEventListener("change", (ev) => {
  addFiles([...ev.target.files]);
  ev.target.value = "";
});
$("#paste-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  if (addBlocked) {
    note("bad", `Not added: ${addBlocked}`);
    return;
  }
  const name = $("#paste-name").value.trim() || "pasted.txt";
  const text = $("#paste-text").value;
  if (await addOne(name, text)) {
    $("#paste-name").value = "";
    $("#paste-text").value = "";
    await refresh();
  }
});
$("#rebuild-btn").addEventListener("click", rebuild);

refresh();
