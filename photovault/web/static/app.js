// PhotoVault web UI. Plain JS, no build step. Every piece of data from the API goes into the DOM through
// textContent (see el()), never through innerHTML.
"use strict";

const NOMINATIM = "https://nominatim.openstreetmap.org/search?format=jsonv2&limit=5&q=";
const PAGE = 120;
const DRIVE_DEBOUNCE_MS = 300; // a burst of "drives" events refetches once, after this quiet time
const DRIVE_LATE_MS = 2000;    // and once more, for a slow mount
const TASK_FAST_MS = 1000;   // poll running tasks this often
const TASK_IDLE_MS = 5000;   // poll when nothing runs
const TASK_LIST_MS = 5000;   // re-read the full task list this often
const SEARCH_GAP_MS = 1000;
const RECENT_FINISHED = 3;   // finished tasks shown before the "Earlier" group

// Human labels. The API keeps its own values; these are only what people read.
const ROLE_LABEL = { CARD: "Card", "CARD (new)": "Card", WORKING: "Working drive", LEGACY: "Legacy drive", MASTER: "Master backup" };
const ROLE_WORD = { WORKING: "working drive", MASTER: "master backup", LEGACY: "legacy drive" };
const STATE_LABEL = { running: "Running", done: "Done", failed: "Failed", paused: "Paused" };
const MISSING_LABEL = { "": "All photos", any: "Missing anything", gps: "Missing location", creator: "Missing creator", keywords: "Missing keywords" };
const GPS_LABEL = { match: "Matched by time", map: "Picked on map", search: "Place search", neighbour: "From a nearby photo" };

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
  tasks: new Map(),    // task_id -> task record (see newTask)
  pausing: new Set(),  // task ids a pause was requested for, until they finish
  techOpen: new Set(), // task ids whose "Technical details" are open
  earlierOpen: false,
  importDrive: null,
  assignDrive: null,
  saving: false,
  loaded: false,
};

const inflight = new Set(); // action keys ("cmd:mount") whose request is still on its way

// The editor's pending edits. A null field means "the user has not touched it", so Save leaves it alone.
const pend = {};
function resetPend() {
  Object.assign(pend, { loc: null, place: null, creator: null, copyright: null, title: null,
    kwAdd: [], kwRemove: [], shift: null });
}
resetPend();

// ---------------------------------------------------------------- helpers

const $ = (sel) => document.querySelector(sel);
const fmtInt = (n) => Number(n || 0).toLocaleString();
const plural = (n, word) => `${fmtInt(n)} ${word}${Number(n) === 1 ? "" : "s"}`;

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

function button(label, onclick, cls) {
  return el("button", { type: "button", class: cls || null, onclick }, label);
}

// Replace a parent's children only when the list changed, so existing nodes keep their identity (and their
// CSS transitions) between polls.
function setChildren(parent, nodes) {
  const cur = parent.children;
  if (cur.length === nodes.length && nodes.every((n, i) => cur[i] === n)) return;
  parent.replaceChildren(...nodes);
}

function toast(text, kind = "ok") {
  const box = $("#toasts");
  const node = el("div", { class: `toast ${kind}`, role: kind === "err" ? "alert" : "status" }, text);
  box.append(node);
  while (box.children.length > 4) box.firstElementChild.remove();
  setTimeout(() => node.remove(), kind === "err" ? 7000 : 4000);
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

// Role keys drive CSS classes and the actions offered; labels are shown to people.
function roleKey(role) {
  if (!role) return "unset";
  if (role.startsWith("CARD")) return "card";
  const lc = role.toLowerCase();
  return ["working", "legacy", "master"].includes(lc) ? lc : "unset";
}

function roleLabel(role) {
  return ROLE_LABEL[role] || "Not set up";
}

function baseName(path) {
  return String(path || "").split("/").filter(Boolean).pop() || "";
}

// A drive's name for people: its label, else the folder name under /Volumes (never the full path).
function driveName(d) {
  return d.label || baseName(d.mount) || d.volume_id || "Unnamed drive";
}

function mountName(path) {
  const d = st.drives.find((x) => x.mount === path);
  return d ? driveName(d) : (baseName(path) || "drive");
}

// Sizes arrive as text such as "1.0 TB". Parse them back to bytes for the capacity bar.
const UNIT_BYTES = { B: 1, KB: 1e3, MB: 1e6, GB: 1e9, TB: 1e12 };
function parseSize(text) {
  const m = /([\d.]+)\s*(B|KB|MB|GB|TB)/.exec(text || "");
  return m ? Number(m[1]) * UNIT_BYTES[m[2]] : null;
}

// ---------------------------------------------------------------- dates

const DATE_SHORT = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
const DATE_LONG = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" });
const WALL_CLOCK = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit", timeZone: "UTC" });
const RTF = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });

// "2026-11-12T04:28:00Z" -> "Nov 12, 4:28 AM" (this year) or with the year otherwise.
function fmtDate(iso) {
  if (!iso) return "";
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return String(iso);
  const d = new Date(ms);
  return (d.getFullYear() === new Date().getFullYear() ? DATE_SHORT : DATE_LONG).format(d);
}

// A capture time shows the wall-clock time the camera wrote, so the zone is ignored here.
function fmtWallClock(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?/.exec(iso || "");
  if (!m) return iso || "";
  return WALL_CLOCK.format(new Date(Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +(m[6] || 0))));
}

function relTime(iso) {
  const ms = Date.parse(iso || "");
  if (Number.isNaN(ms)) return "";
  const s = (ms - Date.now()) / 1000;
  const units = [["year", 31536000], ["month", 2592000], ["week", 604800], ["day", 86400], ["hour", 3600], ["minute", 60]];
  for (const [unit, size] of units) {
    if (Math.abs(s) >= size) return RTF.format(Math.round(s / size), unit);
  }
  return "just now";
}

function fmtElapsed(sec) {
  sec = Math.max(0, Math.floor(sec));
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m ${String(s).padStart(2, "0")}s`;
  return `${s}s`;
}

function fmtEta(sec) {
  sec = Math.max(0, Math.round(sec));
  if (sec < 60) return `about ${sec} s left`;
  if (sec < 3600) return `about ${Math.round(sec / 60)} min left`;
  return `about ${Math.floor(sec / 3600)} h ${Math.round((sec % 3600) / 60)} min left`;
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
  return `${sign}${Math.floor(abs / 60)} min ${String(abs % 60).padStart(2, "0")} s`;
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

function gpsSourceLabel(g) {
  if (g.lat === null || g.lat === undefined) return "No location";
  if (!g.gps_source) return "From camera";
  return GPS_LABEL[g.gps_source] || `From ${g.gps_source}`;
}

// ---------------------------------------------------------------- toasts and busy state

// Runs an action once at a time per key. Buttons for that key are disabled while it is in flight.
async function guarded(key, fn) {
  if (inflight.has(key)) return;
  inflight.add(key);
  renderDrives();
  try {
    await fn();
  } catch (err) {
    toast(err.message || String(err), "err");
  } finally {
    inflight.delete(key);
    renderDrives();
  }
}

function runButton(label, key, fn) {
  return el("button", { type: "button", disabled: inflight.has(key), onclick: () => guarded(key, fn) }, label);
}

// ---------------------------------------------------------------- confirm dialog

// Resolves true only when the person presses the confirm button. Esc and Cancel resolve false.
function confirmDialog({ title, lines, action }) {
  const dlg = $("#dlg-confirm");
  $("#confirm-title").textContent = title;
  $("#confirm-body").replaceChildren(...lines.map((line) => el("p", null, line)));
  $("#confirm-ok").textContent = action;
  return new Promise((resolve) => {
    dlg.addEventListener("close", () => resolve(dlg.returnValue === "ok"), { once: true });
    dlg.returnValue = "";
    dlg.showModal();
  });
}

// ---------------------------------------------------------------- drives

// A drive the catalog does not know yet (no volume_id) gets "Set up…" first; a camera card can also be imported.
function driveActions(d) {
  const key = roleKey(d.role);
  const m = d.mount;
  const run = (label, cmd, args) => runButton(label, `${cmd}:${m}`, () => startTask(cmd, args));
  if (!d.volume_id) {
    const out = [button("Set up…", () => openAssign(d), "primary")];
    if (d.looks_like_card) out.push(button("Import…", () => openImport(d)));
    return out;
  }
  if (key === "card") {
    return [run("Card status", "card_status", { mount: m }), button("Import…", () => openImport(d), "primary")];
  }
  if (key === "working") {
    return [run("Scan metadata", "meta_scan", { mount: m }), button("Write sidecars…", () => writeSidecars(d))];
  }
  if (key === "legacy") {
    return [run("Index", "index", { mount: m }), run("Hash", "hash", { mount: m }), run("Scan metadata", "meta_scan", { mount: m })];
  }
  return [button("Set up…", () => openAssign(d), "primary")];
}

// Registered drives are solid and tinted by role. New drives (no volume_id) are dashed and neutral with a "New"
// pill. A remembered drive that is not plugged in is dimmed and says when it was last seen.
function driveCard(d) {
  const key = roleKey(d.role);
  const isNew = !d.volume_id;
  const pills = [el("span", { class: `pill role-${key}` }, roleLabel(d.role))];
  if (isNew) pills.push(el("span", { class: "pill role-new" }, "New"));
  const kids = [el("div", { class: "drive-head" }, pills, el("span", { class: "drive-name" }, driveName(d)))];
  if (d.mounted) {
    const sub = isNew
      ? (d.looks_like_card ? "A camera card, not set up yet." : "Not set up yet. Set it up to use it for photos.")
      : `${d.fs || "Unknown format"} · ${d.free ?? "?"} free of ${d.size || "?"}`;
    kids.push(el("div", { class: "muted small" }, sub));
    const total = parseSize(d.size);
    const free = parseSize(d.free);
    if (total && free !== null) {
      const used = Math.min(100, Math.max(0, ((total - free) / total) * 100));
      const fill = el("span");
      fill.style.width = `${used.toFixed(1)}%`;
      kids.push(el("div", { class: `capbar${used >= 90 ? " high" : ""}`, role: "img", "aria-label": `${Math.round(used)} percent used` }, fill));
    }
    const actions = driveActions(d);
    if (actions.length) kids.push(el("div", { class: "actions" }, actions));
  } else {
    const seen = d.last_seen_at ? ` · last seen ${relTime(d.last_seen_at)}` : "";
    kids.push(el("div", { class: "muted small", title: fmtDate(d.last_seen_at) }, `Not connected${seen}`));
  }
  const cls = ["drive", isNew ? "is-new" : "is-registered", `role-${key}`];
  if (!d.mounted) cls.push("gone");
  return el("div", { class: cls.join(" ") }, kids);
}

function renderDrives() {
  const box = $("#drives");
  if (!st.drives.length) {
    box.replaceChildren(el("p", { class: "muted small" }, "No drives yet. Plug one in, and it appears here."));
    return;
  }
  box.replaceChildren(...st.drives.map(driveCard));
}

function renderDriveFilter() {
  const select = $("#f-drive");
  const keep = select.value;
  const options = [el("option", { value: "" }, "All drives")];
  for (const d of st.drives) {
    if (!d.volume_id) continue;
    options.push(el("option", { value: d.volume_id }, `${driveName(d)} (${roleLabel(d.role)})`));
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
  $("#assign-name").textContent = `Drive: ${baseName(d.mount) || driveName(d)}${d.fs ? ` · ${d.fs}` : ""}`;
  const form = $("#f-assign");
  form.elements.role.value = "WORKING";
  form.elements.label.value = d.label || "";
  $("#dlg-assign").showModal();
}

function openImport(d) {
  st.importDrive = d;
  const targets = st.drives.filter((t) => t.mounted && roleKey(t.role) === "working");
  const form = $("#f-import");
  form.elements.target.replaceChildren(...targets.map((t) => el("option", { value: t.mount }, driveName(t))));
  form.elements.name.value = "";
  const ok = targets.length > 0;
  $("#btn-import").disabled = !ok;
  $("#btn-dry").disabled = !ok;
  $("#import-note").textContent = ok
    ? "Each file is copied and checked by checksum. The card is never changed."
    : "No working drive is connected. Set one up first.";
  $("#import-source").textContent = `Card: ${driveName(d)}`;
  $("#dlg-import").showModal();
}

async function startImport(dryRun) {
  const form = $("#f-import");
  const target = form.elements.target.value;
  if (!target) return;
  const source = st.importDrive;
  const name = form.elements.name.value.trim();
  if (!dryRun) {
    const ok = await confirmDialog({
      title: "Start the import?",
      lines: [
        `Copy the card "${driveName(source)}" onto "${mountName(target)}".`,
        name ? `Shoot name: ${name}.` : "Shoot name: chosen from the photo dates.",
        "Each file is copied, checked by checksum, and the card is left as it is. A dry run shows the plan first.",
      ],
      action: "Import",
    });
    if (!ok) return;
  }
  const args = { source: source.mount, target, dry_run: dryRun };
  if (name) args.name = name;
  $("#dlg-import").close();
  await startTask("ingest", args);
}

async function writeSidecars(d) {
  const ok = await confirmDialog({
    title: "Write sidecar files?",
    lines: [`Write XMP sidecar files on "${driveName(d)}" for photos with saved edits.`],
    action: "Write sidecars",
  });
  if (ok) await startTask("meta_sidecars", { mount: d.mount, dry_run: false });
}

// ---------------------------------------------------------------- tasks

// A task record. `startMs` comes from the first event's timestamp (or the click, for a task just started).
function newTask(id) {
  return {
    task_id: id, argv: [], state: "running", exit_code: null, last_seq: -1, progress: null,
    final: false, startMs: null, note: "", error: "", result: null,
  };
}

// argv is ["python", "-m", "photovault", "--json", ...words]; the words after --json are the command.
function argvWords(argv) {
  const at = (argv || []).indexOf("--json");
  return at >= 0 ? argv.slice(at + 1) : (argv || []).slice();
}

function flagValue(words, flag) {
  const at = words.indexOf(flag);
  return at >= 0 ? words[at + 1] : undefined;
}

function commandKey(words) {
  const [a, b] = words;
  if (a === "volumes" && b === "assign") return "assign";
  if (a === "meta") return { scan: "meta_scan", refs: "meta_refs", sidecars: "meta_sidecars" }[b] || "meta";
  return a || "";
}

function taskTitle(t) {
  const w = argvWords(t.argv);
  if (!w.length) return "Starting…";
  const dry = w.includes("--dry-run");
  switch (commandKey(w)) {
    case "assign": return `Assigning ${mountName(w[2])} as ${ROLE_WORD[flagValue(w, "--role")] || "drive"}`;
    case "ingest": return `Importing card → ${mountName(flagValue(w, "--target"))}${dry ? " (dry run)" : ""}`;
    case "resume": return "Resuming an import";
    case "card-status": return `Checking card ${mountName(w[1])}`;
    case "index": return `Indexing ${mountName(w[1])}`;
    case "hash": return `Hashing ${mountName(w[1])}`;
    case "meta_scan": return `Scanning metadata on ${mountName(flagValue(w, "--volume"))}`;
    case "meta_refs": return `Reading GPS references from ${flagValue(w, "--label") || "a folder"}`;
    case "meta_sidecars": return `Writing sidecars on ${mountName(flagValue(w, "--volume"))}${dry ? " (dry run)" : ""}`;
    default: return "Working";
  }
}

// Progress counts files, except metadata work, which counts frames.
function unitFor(t) {
  const kind = commandKey(argvWords(t.argv));
  return kind === "meta_scan" || kind === "meta_sidecars" ? "frames" : "files";
}

// "1,234 of 5,800 files · 21% · 2m 10s elapsed · 420 per min · about 11 min left"
function progressLine(t, now) {
  const parts = [];
  const p = t.progress;
  const total = p && Number(p.total) > 0 ? Number(p.total) : 0;
  const current = p ? Number(p.current) || 0 : 0;
  const unit = unitFor(t);
  if (total) {
    parts.push(`${fmtInt(current)} of ${fmtInt(total)} ${unit} · ${Math.min(100, Math.floor((current / total) * 100))}%`);
  } else if (current) {
    parts.push(`${fmtInt(current)} ${unit}`);
  }
  const elapsed = t.startMs ? (now - t.startMs) / 1000 : 0;
  if (t.startMs) parts.push(`${fmtElapsed(elapsed)} elapsed`);
  if (total && current > 0 && elapsed >= 5) {
    const rate = current / elapsed;
    parts.push(`${fmtInt(Math.round(rate * 60))} per min`);
    if (current < total) parts.push(fmtEta((total - current) / rate));
  }
  return parts.join(" · ");
}

function resultText(kind, r, dry) {
  switch (kind) {
    case "ingest":
      if (dry) {
        return `Dry run: ${plural(r.files, "file")} in ${plural(r.groups, "frame")} would be copied (${r.planned}). Nothing was written.`;
      }
      return `Copied ${plural(r.copied, "file")} (${r.copied_size}). ${fmtInt(r.skipped_dup)} already on the drive, skipped. ${fmtInt(r.failed)} failed.`;
    case "index":
      return `Indexed ${fmtInt(r.indexed)} of ${plural(r.files, "file")}. ${fmtInt(r.unchanged)} unchanged, ${fmtInt(r.missing)} missing, ${fmtInt(r.errors)} errors.`;
    case "hash":
      return `Hashed ${plural(r.hashed, "file")} (${r.size}). ${fmtInt(r.skipped)} already done, ${fmtInt(r.unreadable)} unreadable, ${fmtInt(r.deferred)} deferred.`;
    case "meta_scan":
      return `Read metadata for ${plural(r.scanned, "frame")}: ${fmtInt(r.with_gps)} with a location, ${fmtInt(r.errors)} read errors.`;
    case "meta_refs":
      return `Stored ${plural(r.stored, "location fix")} from ${r.label}. ${fmtInt(r.errors)} errors.`;
    case "meta_sidecars":
      if (r.dry_run) return `Would write ${plural(r.would_write, "sidecar file")}. Nothing was written.`;
      return `Wrote ${plural(r.written, "sidecar file")}${r.conflicts ? `, ${fmtInt(r.conflicts)} conflicts` : ""}.`;
    case "assign":
      return `${r.label} is set up as ${ROLE_WORD[r.role] || "drive"}.`;
    case "card-status":
      if (r.state === "green") return `Safe to format: all ${plural(r.total, "file")} are verified on a working drive.`;
      return `Not safe to format. ${fmtInt(r.safe)} of ${fmtInt(r.total)} files verified, ${fmtInt(r.unknown)} not imported yet, ${fmtInt(r.changed)} changed, ${fmtInt(r.short)} short.`;
    default: {
      const bits = Object.entries(r || {})
        .filter(([k, v]) => typeof v === "number" && !["v", "seq", "job_id"].includes(k))
        .map(([k, v]) => `${fmtInt(v)} ${k.replace(/_/g, " ")}`);
      return bits.join(", ");
    }
  }
}

// The plain-words result of a finished task. `problem` marks text to show in the error colour.
function summarize(t) {
  const words = argvWords(t.argv);
  const text = t.result ? resultText(commandKey(words), t.result, words.includes("--dry-run")) : "";
  if (t.state === "paused") {
    return { text: text ? `Paused. ${text}` : "Paused. Run it again to pick up where it stopped.", problem: false };
  }
  if (t.state === "failed") {
    const why = t.error || `Failed (exit code ${t.exit_code}).`;
    return { text: text ? `${why} ${text}` : why, problem: true };
  }
  return { text: text || "Finished.", problem: false };
}

// Each task keeps its own DOM nodes while it is listed, so a bar's width transition runs between polls.
const taskViews = new Map(); // task_id -> node refs

function makeTaskView(id) {
  const v = {};
  v.title = el("span", { class: "task-title" });
  v.pill = el("span", { class: "pill" });
  v.fill = el("div", { class: "fill" });
  v.bar = el("div", { class: "bar", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100" }, v.fill);
  v.line = el("div", { class: "task-line" });
  v.note = el("div", { class: "task-note" });
  v.pauseBtn = el("button", { type: "button", class: "small-btn", onclick: () => pauseTask(id) }, "Pause");
  v.actions = el("div", { class: "task-actions" }, v.pauseBtn);
  v.summary = el("p", { class: "task-summary" });
  v.code = el("code");
  v.tech = el("details", {
    class: "tech",
    ontoggle: (e) => {
      if (e.currentTarget.open) st.techOpen.add(id);
      else st.techOpen.delete(id);
    },
  }, el("summary", null, "Technical details"), v.code);
  v.root = el("div", { class: "task" },
    el("div", { class: "task-head" }, v.title, v.pill), v.bar, v.line, v.note, v.actions, v.summary, v.tech);
  return v;
}

function paintTask(v, t, now) {
  const running = !t.final;
  v.title.textContent = taskTitle(t);
  v.pill.className = `pill state-${running ? "running" : t.state}`;
  v.pill.textContent = running ? "Running" : (STATE_LABEL[t.state] || t.state);

  const total = t.progress && Number(t.progress.total) > 0 ? Number(t.progress.total) : 0;
  v.bar.hidden = !running;
  v.bar.classList.toggle("indet", running && !total);
  v.bar.setAttribute("aria-label", taskTitle(t));
  if (running && total) {
    const pct = Math.min(100, Math.floor(((Number(t.progress.current) || 0) / total) * 100));
    v.fill.style.width = `${pct}%`;
    v.bar.setAttribute("aria-valuenow", String(pct));
  } else {
    v.fill.style.width = "";
    v.bar.removeAttribute("aria-valuenow");
  }
  v.line.hidden = !running;
  v.line.textContent = progressLine(t, now) || "Starting…";
  v.note.hidden = !(running && t.note);
  v.note.textContent = t.note;
  v.actions.hidden = !running;
  const pausing = st.pausing.has(t.task_id);
  v.pauseBtn.disabled = pausing;
  v.pauseBtn.textContent = pausing ? "Pausing…" : "Pause";

  const s = running ? null : summarize(t);
  v.summary.hidden = running;
  v.summary.textContent = s ? s.text : "";
  v.summary.classList.toggle("problem", Boolean(s && s.problem));

  v.code.textContent = `task ${t.task_id} · exit ${t.exit_code ?? "–"}\n${t.argv.length ? t.argv.join(" ") : "(not started yet)"}`;
  v.tech.open = st.techOpen.has(t.task_id);
}

let earlierEl = null;
function earlierBlock() {
  if (!earlierEl) {
    earlierEl = el("details", {
      class: "earlier",
      ontoggle: (e) => { st.earlierOpen = e.currentTarget.open; },
    }, el("summary", null), el("div", null));
  }
  return earlierEl;
}

function renderTasks() {
  const box = $("#tasks");
  const now = Date.now();
  const all = [...st.tasks.values()].sort((a, b) => b.task_id - a.task_id);
  if (!all.length) {
    setChildren(box, [el("p", { class: "muted small" }, "Nothing has run yet. Tasks you start appear here.")]);
    updateGlobal();
    return;
  }
  for (const t of all) {
    let v = taskViews.get(t.task_id);
    if (!v) {
      v = makeTaskView(t.task_id);
      taskViews.set(t.task_id, v);
    }
    paintTask(v, t, now);
  }
  const running = all.filter((t) => !t.final);
  const finished = all.filter((t) => t.final);
  const nodes = [...running, ...finished.slice(0, RECENT_FINISHED)].map((t) => taskViews.get(t.task_id).root);
  const older = finished.slice(RECENT_FINISHED);
  if (older.length) {
    const block = earlierBlock();
    block.open = st.earlierOpen;
    block.firstChild.textContent = `Earlier (${older.length})`;
    setChildren(block.lastChild, older.map((t) => taskViews.get(t.task_id).root));
    nodes.push(block);
  }
  setChildren(box, nodes);
  updateGlobal();
}

// The slim header bar: average progress when every running task is countable, else a moving bar.
function updateGlobal() {
  const running = [...st.tasks.values()].filter((t) => !t.final);
  const wrap = $("#global");
  wrap.hidden = running.length === 0;
  if (!running.length) return;
  const fill = $("#global-fill");
  const counted = running.filter((t) => t.progress && Number(t.progress.total) > 0);
  if (counted.length === running.length) {
    const pct = counted.reduce((sum, t) => sum + Math.min(1, Number(t.progress.current) / Number(t.progress.total)), 0)
      / counted.length;
    fill.classList.remove("indet");
    fill.style.width = `${Math.round(pct * 100)}%`;
  } else {
    fill.classList.add("indet");
    fill.style.width = "";
  }
  $("#global-text").textContent = running.length === 1 ? taskTitle(running[0]) : `${running.length} tasks running`;
}

async function startTask(cmd, args) {
  try {
    const { task_id } = await api("POST", "/api/run", { cmd, args });
    const t = newTask(task_id);
    t.startMs = Date.now();
    st.tasks.set(task_id, t);
    renderTasks();
    kick();
  } catch (err) {
    toast(err.message, "err");
  }
}

async function pauseTask(id) {
  st.pausing.add(id);
  renderTasks();
  try {
    await api("POST", `/api/tasks/${id}/pause`, {});
    toast("Pausing. The task stops after the current file.");
  } catch (err) {
    st.pausing.delete(id);
    toast(err.message, "err");
    renderTasks();
  }
}

// Copy what a poll reported into the task record.
function absorb(t, d) {
  t.argv = d.argv;
  t.state = d.state;
  t.exit_code = d.exit_code;
  t.last_seq = d.last_seq;
  if (d.latest_progress) t.progress = d.latest_progress;
  for (const e of d.events) {
    if (t.startMs === null && e.ts) t.startMs = Date.parse(e.ts) || null;
    if (e.event === "result") t.result = e;
    else if (e.event === "warning") t.note = e.message || e.code || "";
    else if (e.event === "error") t.error = e.message || e.code || "";
    else if (e.event === "progress" && e.message) t.note = e.message;
  }
  if (t.startMs === null) t.startMs = Date.now();
}

async function refreshTaskList() {
  const { tasks } = await api("GET", "/api/tasks");
  for (const s of tasks) {
    let t = st.tasks.get(s.task_id);
    if (!t) {
      t = newTask(s.task_id);
      st.tasks.set(s.task_id, t);
    }
    t.argv = s.argv;
    t.exit_code = s.exit_code;
    if (!t.final) t.state = s.state;
  }
  renderTasks();
}

async function pollTasks() {
  const finished = [];
  for (const t of [...st.tasks.values()].filter((x) => !x.final)) {
    try {
      const d = await api("GET", `/api/tasks/${t.task_id}?since=${t.last_seq}`);
      absorb(t, d);
      if (d.state !== "running") {
        t.final = true;
        st.pausing.delete(t.task_id);
        finished.push(t);
      }
    } catch (err) {
      // Keep the task; the next tick tries again.
    }
  }
  renderTasks();
  if (finished.length) await onTasksFinished(finished);
}

async function onTasksFinished(list) {
  for (const t of list) {
    const s = summarize(t);
    toast(`${taskTitle(t)}. ${s.text}`, t.state === "failed" ? "err" : "ok");
  }
  await Promise.allSettled([loadDrives(), loadShoots(), loadGroups()]);
}

// One loop for tasks: 1 s while anything runs, 5 s when idle. kick() runs it now.
let ticking = false;
let tickTimer = null;
let lastListMs = 0;

async function tick() {
  if (ticking) return;
  ticking = true;
  clearTimeout(tickTimer);
  try {
    if (Date.now() - lastListMs >= TASK_LIST_MS) {
      lastListMs = Date.now();
      await refreshTaskList().catch(() => {});
    }
    await pollTasks();
  } catch (err) {
    // The loop keeps running even when one pass fails.
  } finally {
    ticking = false;
    const live = [...st.tasks.values()].some((t) => !t.final);
    tickTimer = setTimeout(tick, live ? TASK_FAST_MS : TASK_IDLE_MS);
  }
}

function kick() {
  if (!ticking) {
    clearTimeout(tickTimer);
    tick();
  }
}

// ---------------------------------------------------------------- metadata grid

function tag(text) {
  return el("span", { class: "tag" }, text);
}

function badgesFor(g) {
  const out = [];
  if (g.lat === null || g.lat === undefined) out.push(tag("No location"));
  if (!g.creator) out.push(tag("No creator"));
  if (!g.keywords || !g.keywords.length) out.push(tag("No keywords"));
  if (g.dirty) out.push(tag("Sidecar pending"));
  return out;
}

function onThumbError(event) {
  const img = event.currentTarget;
  const box = img.parentElement;
  img.remove();
  box.classList.add("nothumb");
  box.append(document.createTextNode("No preview"));
}

function showSkeleton() {
  const cards = Array.from({ length: 8 }, () => el("div", { class: "photo skeleton", "aria-hidden": "true" },
    el("div", { class: "thumb" }),
    el("div", { class: "skel-line" }),
    el("div", { class: "skel-line short" })));
  $("#grid").replaceChildren(...cards);
}

function clearFilters() {
  $("#f-drive").value = "";
  $("#f-shoot").value = "";
  $("#f-missing").value = "";
  st.page = 0;
  loadGroups().catch(reportError);
}

function emptyGrid() {
  const filtered = $("#f-drive").value || $("#f-shoot").value || $("#f-missing").value;
  if (filtered) {
    return el("div", { class: "empty" },
      el("h3", null, "No photos match these filters"),
      el("p", null, "Try another drive, shoot or filter."),
      el("div", { class: "empty-actions" }, button("Clear filters", clearFilters)));
  }
  const working = st.drives.find((d) => d.mounted && roleKey(d.role) === "working");
  const card = st.drives.find((d) => d.mounted && roleKey(d.role) === "card");
  // Scan metadata needs a mounted working drive, so it is disabled until one is connected.
  const scan = working
    ? runButton("Scan metadata", `meta_scan:${working.mount}`, () => startTask("meta_scan", { mount: working.mount }))
    : el("button", { type: "button", disabled: true, title: "Connect your working drive first" }, "Scan metadata");
  return el("div", { class: "empty" },
    el("h3", null, "No photos yet"),
    el("p", null, "Import a card onto your working drive, then choose Scan metadata."),
    el("div", { class: "empty-actions" },
      el("button", { type: "button", class: "primary", disabled: !card, onclick: () => openImport(card) }, "Import a card…"),
      scan,
      el("span", { class: "muted small" }, !working && !card ? "Connect a card or a working drive to start." : "")));
}

function renderGrid() {
  const grid = $("#grid");
  st.cardNodes = new Map();
  if (!st.groups.length) {
    grid.replaceChildren(emptyGrid());
    return;
  }
  grid.replaceChildren(...st.groups.map((g, index) => {
    const node = el("div", {
      class: "photo", role: "button", tabindex: "0", "aria-pressed": st.selected.has(g.group_id) ? "true" : "false",
      onclick: (e) => onCardClick(e, index),
      onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onCardClick(e, index); } },
    },
      el("div", { class: "thumb" },
        el("img", { src: `/api/groups/${g.group_id}/thumb`, alt: "", loading: "lazy", onerror: onThumbError })),
      el("div", { class: "cap" },
        el("div", { class: "stem" }, g.canonical_stem || `Photo ${g.group_id}`),
        el("div", { class: "muted small" }, g.capture_time ? fmtWallClock(g.capture_time) : "No capture time"),
        g.time_shift_s ? el("div", { class: "muted small" },
          `Clock shifted ${g.time_shift_s > 0 ? "+" : ""}${g.time_shift_s} s`) : null),
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

function selectAllOnPage() {
  for (const g of st.groups) st.selected.add(g.group_id);
  updateSelection();
}

function updateSelection() {
  for (const [id, node] of st.cardNodes) {
    const on = st.selected.has(id);
    node.classList.toggle("sel", on);
    node.setAttribute("aria-pressed", on ? "true" : "false");
  }
  const n = st.selected.size;
  const pill = $("#sel-count");
  pill.hidden = n === 0;
  pill.textContent = `${fmtInt(n)} selected`;
  $("#btn-save").disabled = n === 0 || st.saving;
  syncEditor();
}

function renderCounts(c) {
  $("#counts").textContent = `${plural(c.groups, "photo")} · ${fmtInt(c.gps)} without a location · `
    + `${fmtInt(c.creator)} without a creator · ${fmtInt(c.keywords)} without keywords`;
}

async function loadShoots() {
  const { shoots } = await api("GET", "/api/shoots");
  const select = $("#f-shoot");
  const keep = select.value;
  select.replaceChildren(el("option", { value: "" }, "All shoots"), ...shoots.map((s) =>
    el("option", { value: s.shoot_id }, `${s.shoot_date ? `${s.shoot_date} ` : ""}${s.name || "Unnamed shoot"} · ${plural(s.groups, "photo")}`)));
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
  st.loaded = true;
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
  $("#layout").classList.toggle("has-editor", !editor.hidden);
  if (editor.hidden) return;

  const first = groups[0];
  $("#ed-summary").textContent = `${plural(st.selected.size, "photo")} selected. Changes apply to all of them.`;

  // Text fields: show the shared value, or prefill empties from settings (which then count as an edit).
  for (const key of ["creator", "copyright"]) {
    const input = $(`#ed-${key}`);
    const shared = commonValue(groups, key);
    if (shared === null) { input.value = ""; input.placeholder = "Differs between photos"; }
    else if (shared === "") {
      input.value = st.settings[key] || "";
      input.placeholder = "";
      if (input.value) pend[key] = input.value;
    } else { input.value = shared; input.placeholder = ""; }
  }
  const title = commonValue(groups, "title");
  $("#ed-title").value = title === null ? "" : title;
  $("#ed-title").placeholder = title === null ? "Differs between photos" : "";
  const place = commonValue(groups, "place_name");
  $("#place-name").value = place === null ? "" : place;
  $("#place-name").placeholder = place === null ? "Differs between photos" : "";

  $("#sh-sign").value = "+";
  for (const id of ["#sh-h", "#sh-m", "#sh-s"]) $(id).value = "0";
  renderChips();
  renderShiftPreview();
  renderLocInfo();

  const mapEl = $("#map");
  const map = ensureMap();
  $("#map-note").textContent = map ? "" : "Map unavailable while offline. Use suggestions or type a place name.";
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
      box.replaceChildren(el("p", { class: "muted small" }, `No GPS points within ${minutes} min of the first photo.`));
      return;
    }
    box.replaceChildren(el("p", { class: "muted small" }, "Suggested for the first photo:"),
      ...suggestions.map((s) => el("div", { class: "sugg" },
        el("span", null, `${GPS_LABEL[s.source] || s.source} · ${fmtDelta(s.delta_s)} · ${s.confidence}`),
        button("Use for selection", () => setLoc({ lat: s.lat, lon: s.lon, alt: s.alt ?? null, source: "match" })),
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
  const lines = [];
  if (first && first.lat !== null && first.lat !== undefined) {
    lines.push(`Current (first photo): ${Number(first.lat).toFixed(5)}, ${Number(first.lon).toFixed(5)} · ${gpsSourceLabel(first)}`);
  } else {
    lines.push("Current (first photo): no location");
  }
  if (pend.loc) lines.push(`Pending: ${pend.loc.lat.toFixed(5)}, ${pend.loc.lon.toFixed(5)} · ${GPS_LABEL[pend.loc.source] || pend.loc.source}`);
  $("#loc-info").replaceChildren(...lines.flatMap((p, i) => (i ? [el("br"), p] : [p])));
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
      button(h.display_name || h.name || "Place", () => pickPlace(h)))));
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
  $("#shift-preview").textContent = `First photo now: ${correctedText(first, current)}. `
    + `After saving: ${correctedText(first, shift)} (shift ${shift >= 0 ? "+" : ""}${shift} s).`;
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
  if (st.saving) return;
  const msg = $("#save-msg");
  const fields = buildFields();
  if (!Object.keys(fields).length) { msg.textContent = "Nothing to save yet."; return; }
  const ids = selectedIds();
  st.saving = true;
  const btn = $("#btn-save");
  btn.disabled = true;
  btn.textContent = "Saving…";
  try {
    const res = await api("POST", "/api/groups/meta", { group_ids: ids, fields });
    msg.textContent = "";
    toast(`Saved ${plural(res.updated, "photo")}.`);
    resetPend();
    st.selKey = "";
    await loadGroups();
    syncEditor();
  } catch (err) {
    msg.textContent = err.message;
    toast(err.message, "err");
  } finally {
    st.saving = false;
    btn.textContent = "Save";
    btn.disabled = st.selected.size === 0;
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
    toast("Settings saved.");
  } catch (err) {
    toast(err.message, "err");
  }
}

async function addRefs(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const folder = form.elements.folder.value.trim();
  const label = form.elements.label.value.trim();
  if (!folder || !label) { toast("Give a folder path and a source name.", "err"); return; }
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
}

// ---------------------------------------------------------------- keyboard

function isTyping(target) {
  return target instanceof HTMLElement
    && (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName));
}

// Cmd/Ctrl+A selects every photo on the page, unless the person is typing or a dialog is open.
function onGlobalKey(event) {
  if (!(event.metaKey || event.ctrlKey) || event.altKey || event.key.toLowerCase() !== "a") return;
  if (isTyping(event.target) || document.querySelector("dialog[open]")) return;
  if (!st.groups.length) return;
  event.preventDefault();
  selectAllOnPage();
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
  for (const node of document.querySelectorAll("[data-close]")) {
    node.addEventListener("click", () => node.closest("dialog").close());
  }

  $("#f-drive").addEventListener("change", () => { st.page = 0; loadGroups().catch(reportError); });
  $("#f-shoot").addEventListener("change", () => { st.page = 0; loadGroups().catch(reportError); });
  $("#f-missing").addEventListener("change", () => { st.page = 0; loadGroups().catch(reportError); });
  $("#btn-prev").addEventListener("click", () => { st.page = Math.max(0, st.page - 1); loadGroups().catch(reportError); });
  $("#btn-next").addEventListener("click", () => { st.page += 1; loadGroups().catch(reportError); });
  $("#btn-all").addEventListener("click", selectAllOnPage);
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
  document.addEventListener("keydown", onGlobalKey);
}

function reportError(err) {
  toast(err.message || String(err), "err");
}

async function init() {
  wire();
  showSkeleton();
  try {
    await loadSettings();
    await Promise.all([loadDrives(), loadShoots()]);
    await loadGroups();
  } catch (err) {
    reportError(err);
    if (!st.loaded) {
      $("#grid").replaceChildren(el("div", { class: "empty" },
        el("h3", null, "Could not load photos"),
        el("p", null, err.message),
        button("Try again", () => location.reload())));
    }
  }
  kick();
  connectDriveEvents();
  window.addEventListener("focus", refreshDrives);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refreshDrives(); });
}

// ---------------------------------------------------------------- live drive list

// The server sends `event: drives` when a folder under /Volumes appears or leaves. Refetch after a short quiet
// time, and once more later, since a slow mount can finish after the first look. EventSource reconnects by itself.
let driveTimer = null;
let driveLateTimer = null;

function refreshDrives() {
  return loadDrives().catch(() => {});
}

function onDrivesChanged() {
  clearTimeout(driveTimer);
  driveTimer = setTimeout(() => {
    refreshDrives();
    clearTimeout(driveLateTimer);
    driveLateTimer = setTimeout(refreshDrives, DRIVE_LATE_MS);
  }, DRIVE_DEBOUNCE_MS);
}

function connectDriveEvents() {
  if (!window.EventSource) return;
  const source = new EventSource("/api/events");
  source.addEventListener("drives", onDrivesChanged);
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
else init();
