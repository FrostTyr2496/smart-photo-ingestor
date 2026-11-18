// PhotoVault web UI (ui-5). Plain JS, no build step. Every piece of data from the API goes into the DOM through
// textContent (see el()), never through innerHTML.
"use strict";

const NOMINATIM = "https://nominatim.openstreetmap.org/search?format=jsonv2&limit=5&q=";
const PAGE = 120;
const DRIVE_POLL_MS = 5000;
const TASK_POLL_MS = 1500;
const SEARCH_GAP_MS = 1000;

const st = {
  settings: {},
  drives: [],
  groups: [],          // the current page, in grid order
  byId: new Map(),     // group_id -> last known row (survives paging, so the selection can be edited)
  selected: new Set(),
  anchor: null,        // grid index of the last plain or ctrl click, for shift-click ranges
  cardNodes: new Map(),
  page: 0,
  selKey: "",          // identifies the selection the editor was last filled for
  suggestFor: null,
  map: null,
  marker: null,
  lastSearch: 0,
  tasks: new Map(),    // task_id -> {task_id, argv, state, exit_code, last_seq, progress, final}
  importDrive: null,
  assignDrive: null,
};

// The editor's pending edits. A null field means "the user has not touched it", so Save leaves it alone.
const pend = {};
function resetPend() {
  Object.assign(pend, { loc: null, place: null, creator: null, copyright: null, title: null,
    kwAdd: [], kwRemove: [], shift: null });
}
resetPend();

// ---------------------------------------------------------------- helpers

const $ = (sel) => document.querySelector(sel);

// Build an element. Strings and numbers become text nodes; attributes never receive HTML.
function el(tag, props, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key === "value" || key === "checked" || key === "disabled") node[key] = value;
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function setStatus(text, isError = false) {
  const node = $("#status");
  node.textContent = text || "";
  node.classList.toggle("err", Boolean(isError));
  if (text) setTimeout(() => { if (node.textContent === text) node.textContent = ""; }, 6000);
}

async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (method === "POST") {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body ?? {});
  }
  const res = await fetch(path, opts);
  const isJson = (res.headers.get("Content-Type") || "").startsWith("application/json");
  const data = isJson ? await res.json() : null;
  if (!res.ok) throw new Error((data && data.error) || `Request failed (${res.status})`);
  return data;
}

function roleClass(role) {
  if (!role) return "unassigned";
  if (role.startsWith("CARD")) return "card";
  return role.toLowerCase() === "unassigned" ? "unassigned" : role.toLowerCase();
}

function roleLabel(role) {
  return role === "CARD (new)" ? "CARD" : (role || "unassigned");
}

function parseOffset(zone) {
  const m = /^([+-])(\d{2}):?(\d{2})$/.exec(zone);
  if (!m) return 0;
  return (m[1] === "-" ? -1 : 1) * (Number(m[2]) * 60 + Number(m[3]));
}

// The corrected wall-clock time of a frame: capture_time + time_shift_s, shown in its own offset.
function correctedText(group, shift) {
  const iso = group.capture_time;
  if (!iso) return "no capture time";
  const hasZone = /(Z|[+-]\d{2}:?\d{2})$/.test(iso);
  const zone = hasZone
    ? (/Z$/.test(iso) ? "+00:00" : iso.match(/[+-]\d{2}:?\d{2}$/)[0])
    : (group.capture_offset || st.settings.default_offset || "+00:00");
  const ms = Date.parse(hasZone ? iso : `${iso}${zone}`);
  if (Number.isNaN(ms)) return iso;
  const wall = new Date(ms + shift * 1000 + parseOffset(zone) * 60000).toISOString().slice(0, 19);
  return `${wall.replace("T", " ")} (${zone})`;
}

function fmtDelta(seconds) {
  const sign = seconds < 0 ? "−" : "+";
  const abs = Math.abs(seconds);
  return `Δ ${sign}${Math.floor(abs / 60)}m${String(abs % 60).padStart(2, "0")}s`;
}

function selectedIds() {
  // Grid order first, then any selected frames from other pages.
  const onPage = st.groups.map((g) => g.group_id).filter((id) => st.selected.has(id));
  const rest = [...st.selected].filter((id) => !onPage.includes(id));
  return [...onPage, ...rest];
}

function selectedGroups() {
  return selectedIds().map((id) => st.byId.get(id)).filter(Boolean);
}

// Value shared by every selected frame, "" when they all agree on empty, or null when they differ.
function commonValue(groups, key) {
  const values = new Set(groups.map((g) => g[key] ?? ""));
  return values.size === 1 ? [...values][0] : null;
}

// ---------------------------------------------------------------- drives

function driveActions(d) {
  const role = roleClass(d.role);
  const run = (cmd, args) => () => startTask(cmd, args);
  if (role === "unassigned") {
    return [el("button", { type: "button", onclick: () => openAssign(d) }, "Assign…")];
  }
  if (role === "card") {
    return [
      el("button", { type: "button", onclick: run("card_status", { mount: d.mount }) }, "Card status"),
      el("button", { type: "button", onclick: () => openImport(d) }, "Import…"),
    ];
  }
  if (role === "working") {
    return [
      el("button", { type: "button", onclick: run("meta_scan", { mount: d.mount }) }, "Scan metadata"),
      el("button", { type: "button", onclick: () => writeSidecars(d) }, "Write sidecars"),
    ];
  }
  if (role === "legacy") {
    return [
      el("button", { type: "button", onclick: run("index", { mount: d.mount }) }, "Index"),
      el("button", { type: "button", onclick: run("hash", { mount: d.mount }) }, "Hash"),
      el("button", { type: "button", onclick: run("meta_scan", { mount: d.mount }) }, "Scan metadata"),
    ];
  }
  return [];
}

function renderDrives() {
  const box = $("#drives");
  if (!st.drives.length) {
    box.replaceChildren(el("p", { class: "muted" }, "No drives known yet. Plug one in."));
    return;
  }
  box.replaceChildren(...st.drives.map((d) => {
    const name = d.label || d.mount || d.volume_id || "unnamed";
    const detail = d.mounted
      ? `${d.fs || "?"} · ${d.free ?? "?"} free of ${d.size || "?"}`
      : `not connected · last seen ${d.last_seen_at || "unknown"}`;
    const actions = d.mounted ? driveActions(d) : [];
    return el("div", { class: `drive${d.mounted ? "" : " gone"}` },
      el("div", { class: "dhead" },
        el("span", { class: `badge ${roleClass(d.role)}` }, roleLabel(d.role)),
        el("strong", null, name)),
      el("div", { class: "muted small" }, detail),
      actions.length ? el("div", { class: "actions" }, actions) : null);
  }));
}

function renderDriveFilter() {
  const select = $("#f-drive");
  const keep = select.value;
  const options = [el("option", { value: "" }, "All drives")];
  for (const d of st.drives) {
    if (!d.volume_id) continue;
    options.push(el("option", { value: d.volume_id }, `${d.label || d.volume_id} (${roleLabel(d.role)})`));
  }
  select.replaceChildren(...options);
  select.value = keep;
}

async function loadDrives() {
  const data = await api("GET", "/api/drives");
  st.drives = data.drives;
  renderDrives();
  renderDriveFilter();
}

// ---------------------------------------------------------------- dialogs for drive actions

function openAssign(d) {
  st.assignDrive = d;
  $("#assign-mount").textContent = d.mount || "";
  const form = $("#f-assign");
  form.elements.label.value = d.label || "";
  $("#dlg-assign").showModal();
}

function openImport(d) {
  st.importDrive = d;
  const targets = st.drives.filter((t) => t.mounted && roleClass(t.role) === "working");
  const select = $("#f-import").elements.target;
  select.replaceChildren(...targets.map((t) => el("option", { value: t.mount }, t.label || t.mount)));
  $("#btn-import").disabled = targets.length === 0;
  $("#btn-dry").disabled = targets.length === 0;
  $("#import-note").textContent = targets.length
    ? "Pick the WORKING drive to copy onto."
    : "No WORKING drive is mounted. Assign one first.";
  $("#import-source").textContent = `Card: ${d.label || d.mount}`;
  $("#dlg-import").showModal();
}

async function startImport(dryRun) {
  const form = $("#f-import");
  const target = form.elements.target.value;
  if (!target) return;
  const args = { source: st.importDrive.mount, target, dry_run: dryRun };
  const name = form.elements.name.value.trim();
  if (name) args.name = name;
  $("#dlg-import").close();
  await startTask("ingest", args);
}

async function writeSidecars(d) {
  if (!window.confirm(`Write XMP sidecars on ${d.label || d.mount}?`)) return;
  await startTask("meta_sidecars", { mount: d.mount, dry_run: false });
}

async function startTask(cmd, args) {
  try {
    const { task_id } = await api("POST", "/api/run", { cmd, args });
    setStatus(`Started task ${task_id}.`);
    st.tasks.set(task_id, { task_id, argv: [], state: "running", exit_code: null, last_seq: -1,
      progress: null, final: false });
    renderTasks();
    await pollTasks();
  } catch (err) {
    setStatus(err.message, true);
  }
}

// ---------------------------------------------------------------- tasks

function taskArgv(argv) {
  const at = argv.indexOf("--json");
  return (at >= 0 ? argv.slice(at + 1) : argv).join(" ");
}

function renderTasks() {
  const box = $("#tasks");
  const list = [...st.tasks.values()].sort((a, b) => b.task_id - a.task_id).slice(0, 8);
  if (!list.length) {
    box.replaceChildren(el("p", { class: "muted small" }, "No tasks yet."));
    return;
  }
  box.replaceChildren(...list.map((t) => {
    const p = t.progress;
    const progress = p
      ? `${p.phase || ""} ${p.current ?? ""}${p.total ? `/${p.total}` : ""} ${p.message || ""}`.trim()
      : "";
    return el("div", { class: "task" },
      el("div", { class: "small" }, `#${t.task_id} `, el("span", { class: `state-${t.state}` }, t.state),
        t.exit_code !== null && t.exit_code !== undefined ? ` (exit ${t.exit_code})` : ""),
      el("div", { class: "argv" }, taskArgv(t.argv)),
      progress ? el("div", { class: "prog" }, progress) : null,
      t.state === "running"
        ? el("button", { type: "button", onclick: () => pauseTask(t.task_id) }, "Pause")
        : null);
  }));
}

async function pauseTask(id) {
  try {
    await api("POST", `/api/tasks/${id}/pause`, {});
    setStatus(`Pausing task ${id}…`);
  } catch (err) {
    setStatus(err.message, true);
  }
}

async function refreshTaskList() {
  const { tasks } = await api("GET", "/api/tasks");
  for (const s of tasks) {
    const t = st.tasks.get(s.task_id) || { task_id: s.task_id, last_seq: -1, progress: null, final: false };
    Object.assign(t, { argv: s.argv, state: s.state, exit_code: s.exit_code });
    st.tasks.set(s.task_id, t);
  }
  renderTasks();
}

async function pollTasks() {
  let changed = false;
  for (const t of st.tasks.values()) {
    if (t.final) continue;
    try {
      const d = await api("GET", `/api/tasks/${t.task_id}?since=${t.last_seq}`);
      t.argv = d.argv;
      t.state = d.state;
      t.exit_code = d.exit_code;
      t.last_seq = d.last_seq;
      if (d.latest_progress) t.progress = d.latest_progress;
      if (d.state !== "running") t.final = true;
      changed = true;
    } catch (err) {
      setStatus(err.message, true);
    }
  }
  if (changed) renderTasks();
}

// ---------------------------------------------------------------- metadata grid

function badgesFor(g) {
  const out = [];
  if (g.lat === null || g.lat === undefined) out.push(el("span", { class: "tag" }, "no GPS"));
  if (!g.creator) out.push(el("span", { class: "tag" }, "no creator"));
  if (!g.keywords || !g.keywords.length) out.push(el("span", { class: "tag" }, "no keywords"));
  if (g.dirty) out.push(el("span", { class: "tag" }, "sidecar pending"));
  return out;
}

function onThumbError(event) {
  const img = event.currentTarget;
  const box = img.parentElement;
  img.remove();
  box.classList.add("nothumb");
  box.append(document.createTextNode("no preview"));
}

function renderGrid() {
  const grid = $("#grid");
  st.cardNodes = new Map();
  if (!st.groups.length) {
    grid.replaceChildren(el("p", { class: "muted" }, "No frames match these filters."));
    return;
  }
  grid.replaceChildren(...st.groups.map((g, index) => {
    const node = el("div", {
      class: "card", role: "button", tabindex: "0", "aria-pressed": st.selected.has(g.group_id) ? "true" : "false",
      onclick: (e) => onCardClick(e, index),
      onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onCardClick(e, index); } },
    },
      el("div", { class: "thumb" },
        el("img", { src: `/api/groups/${g.group_id}/thumb`, alt: "", loading: "lazy", onerror: onThumbError })),
      el("div", { class: "cap" }, g.canonical_stem || `group ${g.group_id}`,
        el("br"),
        el("span", { class: "muted" }, g.capture_time || "no time"),
        g.time_shift_s ? el("span", { class: "muted" }, ` · shift ${g.time_shift_s > 0 ? "+" : ""}${g.time_shift_s}s`) : null),
      el("div", { class: "badges" }, badgesFor(g)));
    if (st.selected.has(g.group_id)) node.classList.add("sel");
    st.cardNodes.set(g.group_id, node);
    return node;
  }));
}

function onCardClick(event, index) {
  const id = st.groups[index].group_id;
  if (event.shiftKey && st.anchor !== null) {
    const lo = Math.min(st.anchor, index);
    const hi = Math.max(st.anchor, index);
    for (let i = lo; i <= hi; i++) st.selected.add(st.groups[i].group_id);
  } else if (event.metaKey || event.ctrlKey) {
    if (st.selected.has(id)) st.selected.delete(id);
    else st.selected.add(id);
    st.anchor = index;
  } else {
    st.selected = new Set([id]);
    st.anchor = index;
  }
  updateSelection();
}

function updateSelection() {
  for (const [id, node] of st.cardNodes) {
    const on = st.selected.has(id);
    node.classList.toggle("sel", on);
    node.setAttribute("aria-pressed", on ? "true" : "false");
  }
  const n = st.selected.size;
  $("#sel-count").textContent = n ? `${n} selected` : "";
  $("#btn-save").disabled = n === 0;
  syncEditor();
}

function renderCounts(c) {
  $("#counts").textContent = `On this drive: ${c.groups} frames · ${c.gps} without GPS · `
    + `${c.creator} without creator · ${c.keywords} without keywords`;
}

async function loadShoots() {
  const { shoots } = await api("GET", "/api/shoots");
  const select = $("#f-shoot");
  const keep = select.value;
  select.replaceChildren(el("option", { value: "" }, "All shoots"), ...shoots.map((s) =>
    el("option", { value: s.shoot_id }, `${s.shoot_date || ""} ${s.name || "(unnamed)"} · ${s.groups}`)));
  select.value = keep;
}

async function loadGroups() {
  const params = new URLSearchParams();
  const drive = $("#f-drive").value;
  const shoot = $("#f-shoot").value;
  const missing = $("#f-missing").value;
  if (drive) params.set("volume_id", drive);
  if (shoot) params.set("shoot_id", shoot);
  if (missing) params.set("missing", missing);
  params.set("limit", String(PAGE));
  params.set("offset", String(st.page * PAGE));
  const data = await api("GET", `/api/groups?${params}`);
  st.groups = data.groups;
  for (const g of data.groups) st.byId.set(g.group_id, g);
  $("#page-label").textContent = `Page ${st.page + 1}`;
  $("#btn-prev").disabled = st.page === 0;
  $("#btn-next").disabled = data.groups.length < PAGE;
  $("#btn-all").disabled = data.groups.length === 0;
  renderCounts(data.missing_counts);
  renderGrid();
  updateSelection();
}

// ---------------------------------------------------------------- editor

function syncEditor() {
  const key = selectedIds().join(",");
  if (key === st.selKey) return;
  st.selKey = key;
  resetPend();
  fillEditor();
}

function fillEditor() {
  const editor = $("#editor");
  const groups = selectedGroups();
  editor.hidden = st.selected.size === 0;
  if (editor.hidden) return;

  const first = groups[0];
  $("#ed-summary").textContent = `${st.selected.size} frame(s) selected. Changes apply to all of them.`;

  // Text fields: show the shared value, or prefill empties from settings (which then count as an edit).
  for (const key of ["creator", "copyright"]) {
    const input = $(`#ed-${key}`);
    const shared = commonValue(groups, key);
    if (shared === null) { input.value = ""; input.placeholder = "varies"; }
    else if (shared === "") {
      input.value = st.settings[key] || "";
      input.placeholder = "";
      if (input.value) pend[key] = input.value;
    } else { input.value = shared; input.placeholder = ""; }
  }
  const title = commonValue(groups, "title");
  $("#ed-title").value = title === null ? "" : title;
  $("#ed-title").placeholder = title === null ? "varies" : "";
  const place = commonValue(groups, "place_name");
  $("#place-name").value = place === null ? "" : place;
  $("#place-name").placeholder = place === null ? "varies" : "";

  $("#sh-sign").value = "+";
  for (const id of ["#sh-h", "#sh-m", "#sh-s"]) $(id).value = "0";
  renderChips();
  renderShiftPreview();
  renderLocInfo();

  const mapEl = $("#map");
  const map = ensureMap();
  $("#map-note").textContent = map ? "" : "Map unavailable (offline). Use suggestions or type a place name.";
  mapEl.hidden = !map;
  if (map) {
    map.invalidateSize();
    if (first && first.lat !== null && first.lat !== undefined) map.setView([first.lat, first.lon], 12);
  }
  updatePin();
  if (first) loadSuggestions(first.group_id);
  else $("#suggest").replaceChildren();
}

async function loadSuggestions(id) {
  st.suggestFor = id;
  const box = $("#suggest");
  box.replaceChildren(el("p", { class: "muted small" }, "Looking for nearby GPS points…"));
  try {
    const { suggestions } = await api("GET", `/api/groups/${id}/suggestions`);
    if (st.suggestFor !== id) return;
    if (!suggestions.length) {
      const minutes = Math.round((Number(st.settings.match_window_s) || 1800) / 60);
      box.replaceChildren(el("p", { class: "muted small" }, `No GPS points within ±${minutes} min of the first frame.`));
      return;
    }
    box.replaceChildren(el("p", { class: "muted small" }, "Suggested for the first frame:"),
      ...suggestions.map((s) => el("div", { class: "sugg" },
        el("span", null, `${s.source} · ${fmtDelta(s.delta_s)} · ${s.confidence}`),
        el("button", { type: "button", onclick: () => setLoc({ lat: s.lat, lon: s.lon, alt: s.alt ?? null, source: "match" }) },
          "Use for selection"),
        el("span", { class: "muted" }, `${Number(s.lat).toFixed(5)}, ${Number(s.lon).toFixed(5)}`))));
  } catch (err) {
    if (st.suggestFor === id) box.replaceChildren(el("p", { class: "muted small" }, err.message));
  }
}

function ensureMap() {
  if (st.map) return st.map;
  if (!window.L || !$("#map")) return null;
  st.map = window.L.map("map").setView([20, 0], 2);
  window.L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  }).addTo(st.map);
  st.map.on("click", (e) => setLoc({ lat: e.latlng.lat, lon: e.latlng.lng, alt: null, source: "map" }));
  return st.map;
}

function updatePin() {
  if (!st.map || !window.L) return;
  const loc = pend.loc;
  if (!loc) {
    if (st.marker) { st.marker.remove(); st.marker = null; }
    return;
  }
  if (st.marker) st.marker.setLatLng([loc.lat, loc.lon]);
  else st.marker = window.L.marker([loc.lat, loc.lon]).addTo(st.map);
}

function setLoc(loc) {
  pend.loc = loc;
  updatePin();
  renderLocInfo();
}

function renderLocInfo() {
  const first = selectedGroups()[0];
  const parts = [];
  if (first && first.lat !== null && first.lat !== undefined) {
    parts.push(`Current (first frame): ${Number(first.lat).toFixed(5)}, ${Number(first.lon).toFixed(5)} (${first.gps_source || "unknown"})`);
  } else {
    parts.push("Current (first frame): no location");
  }
  if (pend.loc) parts.push(`Pending: ${pend.loc.lat.toFixed(5)}, ${pend.loc.lon.toFixed(5)} (${pend.loc.source})`);
  $("#loc-info").replaceChildren(...parts.flatMap((p, i) => (i ? [el("br"), p] : [p])));
}

let searchTimer = null;
async function searchPlace() {
  const q = $("#place-q").value.trim();
  if (!q) return;
  const wait = SEARCH_GAP_MS - (Date.now() - st.lastSearch);
  if (wait > 0) {
    // One request per second at most. Retry once the gap has passed.
    clearTimeout(searchTimer);
    searchTimer = setTimeout(searchPlace, wait);
    return;
  }
  st.lastSearch = Date.now();
  const list = $("#place-results");
  list.replaceChildren(el("li", { class: "muted small" }, "Searching…"));
  try {
    const res = await fetch(NOMINATIM + encodeURIComponent(q), { headers: { Accept: "application/json" } });
    if (!res.ok) throw new Error(`search failed (${res.status})`);
    const hits = await res.json();
    if (!hits.length) {
      list.replaceChildren(el("li", { class: "muted small" }, "No matches. You can still type a place name."));
      return;
    }
    list.replaceChildren(...hits.slice(0, 5).map((h) => el("li", null,
      el("button", { type: "button", onclick: () => pickPlace(h) }, h.display_name || h.name || "place"))));
  } catch (err) {
    list.replaceChildren(el("li", { class: "muted small" },
      `Search unavailable (${err.message}). Type the place name instead.`));
  }
}

function pickPlace(hit) {
  const lat = Number(hit.lat);
  const lon = Number(hit.lon);
  const name = hit.name || hit.display_name || "";
  $("#place-name").value = name;
  pend.place = name;
  setLoc({ lat, lon, alt: null, source: "search" });
  if (st.map) st.map.flyTo([lat, lon], 12);
}

// Keyword chips: the union across the selection, minus pending removals, plus pending additions.
function keywordList() {
  const seen = new Map();
  for (const g of selectedGroups()) {
    for (const k of g.keywords || []) {
      if (!seen.has(k.toLowerCase())) seen.set(k.toLowerCase(), k);
    }
  }
  const removed = new Set(pend.kwRemove.map((k) => k.toLowerCase()));
  const out = [...seen].filter(([lc]) => !removed.has(lc)).map(([, k]) => k);
  for (const k of pend.kwAdd) {
    if (!out.some((x) => x.toLowerCase() === k.toLowerCase())) out.push(k);
  }
  return out;
}

function renderChips() {
  const chips = keywordList().map((k) => el("span", { class: "chip" }, k,
    el("button", { type: "button", "aria-label": `Remove ${k} from selection`, onclick: () => removeKeyword(k) }, "×")));
  $("#kw-chips").replaceChildren(...chips);
}

function removeKeyword(word) {
  const lc = word.toLowerCase();
  const at = pend.kwAdd.findIndex((k) => k.toLowerCase() === lc);
  if (at >= 0) pend.kwAdd.splice(at, 1);
  else if (!pend.kwRemove.some((k) => k.toLowerCase() === lc)) pend.kwRemove.push(word);
  renderChips();
}

function addKeywords(text) {
  const shown = new Set(keywordList().map((k) => k.toLowerCase()));
  for (const raw of text.split(",")) {
    const word = raw.trim();
    if (!word) continue;
    const lc = word.toLowerCase();
    const at = pend.kwRemove.findIndex((k) => k.toLowerCase() === lc);
    if (at >= 0) pend.kwRemove.splice(at, 1);
    else if (!shown.has(lc) && !pend.kwAdd.some((k) => k.toLowerCase() === lc)) pend.kwAdd.push(word);
    shown.add(lc);
  }
  renderChips();
}

// Clock shift: only a change in the editor sets pend.shift, so other frames keep their own shift.
function readShift() {
  const sign = $("#sh-sign").value === "-" ? -1 : 1;
  const h = Math.max(0, Number($("#sh-h").value) || 0);
  const m = Math.max(0, Number($("#sh-m").value) || 0);
  const s = Math.max(0, Number($("#sh-s").value) || 0);
  return sign * (h * 3600 + m * 60 + s);
}

function onShiftChange() {
  pend.shift = readShift();
  renderShiftPreview();
}

function renderShiftPreview() {
  const first = selectedGroups()[0];
  if (!first) { $("#shift-preview").textContent = ""; return; }
  const current = Number(first.time_shift_s) || 0;
  const shift = pend.shift !== null ? pend.shift : current;
  $("#shift-preview").textContent = `First frame now ${correctedText(first, current)}. `
    + `After save: ${correctedText(first, shift)} (shift ${shift >= 0 ? "+" : ""}${shift} s).`;
}

function buildFields() {
  const fields = {};
  if (pend.loc) {
    Object.assign(fields, { lat: pend.loc.lat, lon: pend.loc.lon, alt: pend.loc.alt ?? null,
      gps_source: pend.loc.source });
  }
  if (pend.place !== null) fields.place_name = pend.place;
  for (const key of ["creator", "copyright", "title"]) {
    if (pend[key] !== null) fields[key] = pend[key];
  }
  if (pend.kwAdd.length) fields.keywords_add = [...pend.kwAdd];
  if (pend.kwRemove.length) fields.keywords_remove = [...pend.kwRemove];
  if (pend.shift !== null) fields.time_shift_s = pend.shift;
  return fields;
}

async function saveSelection() {
  const msg = $("#save-msg");
  const fields = buildFields();
  if (!Object.keys(fields).length) { msg.textContent = "Nothing to save."; return; }
  const ids = selectedIds();
  $("#btn-save").disabled = true;
  try {
    const res = await api("POST", "/api/groups/meta", { group_ids: ids, fields });
    msg.textContent = `Saved ${res.updated} frame(s).`;
    resetPend();
    st.selKey = "";
    await loadGroups();
    syncEditor();
  } catch (err) {
    msg.textContent = err.message;
  } finally {
    $("#btn-save").disabled = st.selected.size === 0;
  }
}

// ---------------------------------------------------------------- settings and reference folders

async function loadSettings() {
  const { settings } = await api("GET", "/api/settings");
  st.settings = settings;
}

function openSettings() {
  const form = $("#f-settings");
  for (const key of ["creator", "copyright", "default_offset", "match_window_s"]) {
    form.elements[key].value = st.settings[key] ?? "";
  }
  $("#dlg-settings").showModal();
}

async function saveSettings(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const body = {
    creator: form.elements.creator.value,
    copyright: form.elements.copyright.value,
    default_offset: form.elements.default_offset.value,
    match_window_s: form.elements.match_window_s.value,
  };
  try {
    const res = await api("POST", "/api/settings", body);
    st.settings = res.settings;
    $("#dlg-settings").close();
    setStatus("Settings saved.");
  } catch (err) {
    setStatus(err.message, true);
  }
}

async function addRefs(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const folder = form.elements.folder.value.trim();
  const label = form.elements.label.value.trim();
  if (!folder || !label) { setStatus("Give a folder path and a label.", true); return; }
  $("#dlg-refs").close();
  await startTask("meta_refs", { folder, label });
}

async function assignDrive(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const d = st.assignDrive;
  const args = { mount: d.mount, role: form.elements.role.value };
  const label = form.elements.label.value.trim();
  if (label) args.label = label;
  $("#dlg-assign").close();
  await startTask("assign", args);
  setTimeout(() => loadDrives().catch((err) => setStatus(err.message, true)), 1500);
}

// ---------------------------------------------------------------- wiring and startup

function wire() {
  $("#btn-settings").addEventListener("click", openSettings);
  $("#f-settings").addEventListener("submit", saveSettings);
  $("#f-assign").addEventListener("submit", assignDrive);
  $("#f-refs").addEventListener("submit", addRefs);
  $("#f-import").addEventListener("submit", (e) => e.preventDefault());
  $("#btn-dry").addEventListener("click", () => startImport(true));
  $("#btn-import").addEventListener("click", () => startImport(false));
  $("#btn-refs").addEventListener("click", () => $("#dlg-refs").showModal());
  for (const button of document.querySelectorAll("[data-close]")) {
    button.addEventListener("click", () => button.closest("dialog").close());
  }

  $("#f-drive").addEventListener("change", () => { st.page = 0; loadGroups().catch(reportError); });
  $("#f-shoot").addEventListener("change", () => { st.page = 0; loadGroups().catch(reportError); });
  $("#f-missing").addEventListener("change", () => { st.page = 0; loadGroups().catch(reportError); });
  $("#btn-prev").addEventListener("click", () => { st.page = Math.max(0, st.page - 1); loadGroups().catch(reportError); });
  $("#btn-next").addEventListener("click", () => { st.page += 1; loadGroups().catch(reportError); });
  $("#btn-all").addEventListener("click", () => {
    for (const g of st.groups) st.selected.add(g.group_id);
    updateSelection();
  });
  $("#btn-clear").addEventListener("click", () => { st.selected = new Set(); st.anchor = null; updateSelection(); });

  $("#btn-search").addEventListener("click", searchPlace);
  $("#place-q").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); searchPlace(); } });
  $("#place-name").addEventListener("input", (e) => { pend.place = e.currentTarget.value; });
  $("#ed-creator").addEventListener("input", (e) => { pend.creator = e.currentTarget.value; });
  $("#ed-copyright").addEventListener("input", (e) => { pend.copyright = e.currentTarget.value; });
  $("#ed-title").addEventListener("input", (e) => { pend.title = e.currentTarget.value; });
  $("#kw-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      addKeywords(e.currentTarget.value);
      e.currentTarget.value = "";
    }
  });
  for (const id of ["#sh-sign", "#sh-h", "#sh-m", "#sh-s"]) {
    $(id).addEventListener("input", onShiftChange);
  }
  $("#btn-save").addEventListener("click", saveSelection);
}

function reportError(err) {
  setStatus(err.message || String(err), true);
}

async function init() {
  wire();
  try {
    await loadSettings();
    await loadDrives();
    await loadShoots();
    await loadGroups();
    await refreshTaskList();
  } catch (err) {
    reportError(err);
  }
  setInterval(() => loadDrives().catch(() => {}), DRIVE_POLL_MS);
  setInterval(() => refreshTaskList().catch(() => {}), DRIVE_POLL_MS * 2);
  setInterval(() => pollTasks().catch(() => {}), TASK_POLL_MS);
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
else init();
