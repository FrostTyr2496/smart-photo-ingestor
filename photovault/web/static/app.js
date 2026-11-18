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
const ACTIVITY_MS = 3000;    // re-read runs started outside the UI this often (ui-11)
const QUIET_WARN_S = 600;    // a run with no catalog write for this long gets a "may be stuck" note
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
  showIgnored: readPref("showIgnoredDrives") === "1", // the folded "Ignored drives" list is expanded
  foldedGroups: new Set((readPref("driveFold") || "").split(",").filter(Boolean)), // collapsed sidebar groups
  photosOpen: false,   // the photo grid is the middle view; otherwise, with nothing else open, the home view
  driveView: null,     // volume_id of the drive whose summary replaces the photo grid, or null
  driveFiles: { state: "", copies: "", camera: "", offset: 0 }, // file list filter and paging inside that summary
  cardsOpen: false,    // the Cards view replaces the photo grid
  cardView: null,      // volume_id of the card whose ranked file list is open, or null for the card list
  cardFiles: { tier: "", copies: "", other: "", camera: "", offset: 0 },
  cards: null,         // last /api/cards answer
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
  elsewhere: [],       // runs the UI did not start, from /api/activity (ui-11)
  staleJobs: [],       // jobs marked running that no process is doing
  staleOpen: false,
  pausing: new Set(),  // task ids a pause was requested for, until they finish
  techOpen: new Set(), // task ids whose "Technical details" are open
  earlierOpen: false,
  importDrive: null,
  assignDrive: null,
  setupView: null,     // { drive, taskId, role, runAfter }: a new drive being set up, shown in the middle view
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

// Per-browser conveniences only; storage can be missing or throw (private windows), so failures are ignored.
function readPref(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}
function writePref(key, value) {
  try { localStorage.setItem(key, value); } catch { /* not kept */ }
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
// Bytes for people, in the same decimal units the server uses (1 KB = 1000 B).
function fmtBytes(n) {
  if (n === null || n === undefined) return "";
  let v = Number(n);
  let i = 0;
  while (v >= 1000 && i < 4) { v /= 1000; i++; }
  const units = ["B", "KB", "MB", "GB", "TB"];
  return i === 0 ? `${fmtInt(v)} B` : `${v >= 100 ? Math.round(v) : v.toFixed(1)} ${units[i]}`;
}

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

// ---------------------------------------------------------------- workflow

// A drive's workflow is a fixed run of engine steps. Cards have none: they are imported, not indexed.
const STEP_NAME = { index: "Index", hash: "Hash", scan: "Scan metadata", sidecars: "Write sidecars" };
const STEP_HINT = {
  index: "List every file", hash: "Verify each file's bytes", scan: "Read dates and locations",
  sidecars: "Save edits next to the photos",
};
const STEP_BY_CMD = { index: "index", hash: "hash", meta_scan: "scan", meta_sidecars: "sidecars" };

function workflowSteps(d) {
  const key = roleKey(d.role);
  if (!d.volume_id || key === "card") return [];
  const mount = d.mount;
  const steps = [
    { id: "index", cmd: "index", args: { mount } },
    { id: "hash", cmd: "hash", args: { mount } },
    { id: "scan", cmd: "meta_scan", args: { mount } },
  ];
  if (key === "working") steps.push({ id: "sidecars", cmd: "meta_sidecars", args: { mount, dry_run: false } });
  return steps;
}

// The step a live (unfinished) task is running on this drive, with its task, or null.
function liveStep(d) {
  if (!d || !d.mount) return null;
  for (const t of st.tasks.values()) {
    if (t.final) continue;
    const words = argvWords(t.argv);
    if (!words.includes(d.mount)) continue;
    const key = commandKey(words);
    const step = { index: "index", hash: "hash", meta_scan: "scan", meta_sidecars: "sidecars" }[key];
    if (step) return { step, task: t };
  }
  return null;
}

// "Running · Hash", or "Last run: Hash · 2 hours ago", for a drive card in the sidebar.
function driveStatus(d) {
  const live = liveStep(d);
  if (live) return { text: `Running · ${STEP_NAME[live.step]}`, cls: "running" };
  const steps = d.steps || {};
  let best = null;
  for (const [id, v] of Object.entries(steps)) {
    if (v && v.at && (!best || Date.parse(v.at) > Date.parse(best.at))) best = { id, ...v };
  }
  if (!best) return { text: roleKey(d.role) === "card" ? "Ready to import" : "Not run yet", cls: "" };
  const lead = best.state === "paused" ? "Paused" : best.state === "failed" ? "Failed"
    : best.state === "running" || best.state === "planned" ? "Interrupted" : "Last run";
  return { text: `${lead}: ${STEP_NAME[best.id]} · ${relTime(best.at)}`, cls: lead === "Last run" ? "" : "warn" };
}

// One timeline step's display state, from live tasks, the workflow in progress, and the saved last run.
function stepView(d, step, last) {
  const wf = st.workflow && st.workflow.volumeId === d.volume_id ? st.workflow : null;
  const live = liveStep(d);
  if (live && live.step === step.id) return { cls: "running", when: "Running now", task: live.task };
  if (wf && !wf.outcome) {
    const at = wf.steps.findIndex((x) => x.id === step.id);
    if (at > wf.at) return { cls: "queued", when: "Waiting" };
  }
  if (!last) return { cls: "never", when: "Never run" };
  const ago = relTime(last.at);
  const left = last.pending ? ` · ${fmtInt(last.pending)} left` : "";
  if (last.state === "done") return { cls: "done", when: `Last run ${ago}` };
  // A hash that finished while files still wait (ui-13): e.g. it ran before the index found them.
  if (last.state === "incomplete") return { cls: "warn", when: `${fmtInt(last.pending)} files not hashed yet` };
  if (last.state === "paused") return { cls: "warn", when: `Paused ${ago}${left}` };
  if (last.state === "failed") return { cls: "warn", when: `Failed ${ago}` };
  return { cls: "warn", when: `Interrupted ${ago}` };
}

function waitForTask(id) {
  return new Promise((resolve) => {
    const timer = setInterval(() => {
      const t = st.tasks.get(id);
      if (t && t.final) { clearInterval(timer); resolve(t); }
    }, 500);
  });
}

// A run started in a terminal on this drive (ui-11) counts too, so the UI never starts a second one beside it.
function workflowBusy(d) {
  return Boolean(st.workflow) || Boolean(liveStep(d))
    || st.elsewhere.some((e) => (e.words || []).includes(d.mount));
}

// Runs the steps one after another and stops at the first one that does not finish.
async function runWorkflow(d) {
  if (workflowBusy(d)) return;
  const steps = workflowSteps(d);
  if (!steps.length) return;
  if (steps.some((s) => s.id === "sidecars")) {
    const ok = await confirmDialog({
      title: "Run the workflow?",
      lines: [`On "${driveName(d)}": index, hash and scan metadata, then write XMP sidecar files next to photos with saved edits.`,
        "Nothing is deleted or overwritten."],
      action: "Run workflow",
    });
    if (!ok) return;
  }
  const wf = { volumeId: d.volume_id, steps, at: 0, outcome: null };
  st.workflow = wf;
  try {
    for (let i = 0; i < steps.length; i++) {
      wf.at = i;
      updateWorkflowUI();
      const id = await startTask(steps[i].cmd, steps[i].args);
      if (!id) { wf.outcome = "failed"; break; }
      const t = await waitForTask(id);
      if (t.state !== "done") {
        wf.outcome = t.state;
        toast(`Workflow stopped at ${STEP_NAME[steps[i].id]}. ${t.state === "paused" ? "It was paused." : "It did not finish."}`, "err");
        break;
      }
    }
    if (!wf.outcome) toast(`Workflow finished on ${driveName(d)}.`);
  } finally {
    st.workflow = null;
    renderDrives();
    if (st.driveView) loadDriveView().catch(reportError);
  }
}

// Runs one step on its own (ui-12), e.g. hash after an index that ran in a terminal. Sidecars write to the
// drive, so they ask first, as the workflow does.
async function runStep(d, step) {
  if (workflowBusy(d)) return;
  if (step.id === "sidecars") {
    const ok = await confirmDialog({
      title: "Write sidecar files?",
      lines: [`On "${driveName(d)}": write XMP sidecar files next to photos with saved edits.`, "Nothing is deleted or overwritten."],
      action: "Write sidecars",
    });
    if (!ok) return;
  }
  await startTask(step.cmd, step.args);
  updateWorkflowUI();
}

// One Run button per drive and step, kept across repaints so a click is never lost to a poll.
const stepBtns = new Map();
function stepButton(d, step) {
  const key = `${d.volume_id}:${step.id}`;
  let b = stepBtns.get(key);
  if (!b) {
    b = el("button", { type: "button", class: "small-btn tl-run" }, "Run");
    stepBtns.set(key, b);
  }
  b.onclick = () => runStep(d, step);
  b.title = `Run only ${STEP_NAME[step.id].toLowerCase()} on ${driveName(d)}`;
  return b;
}

let timelineBox = null;
let workflowBtn = null;

// Repaints the timeline and the Run workflow button in place; called on every task poll.
function updateWorkflowUI() {
  if (!timelineBox || !st.driveView || !st.driveSummary) return;
  const base = st.drives.find((x) => x.volume_id === st.driveView) || {};
  const d = { ...base, ...(st.driveSummary.drive || {}), steps: st.driveSummary.steps };
  const busy = workflowBusy(d);
  if (workflowBtn) {
    workflowBtn.disabled = busy || !d.mounted;
    workflowBtn.textContent = busy ? "Workflow running…" : "Run workflow";
    workflowBtn.title = d.mounted ? "" : "Connect this drive to run the workflow";
  }
  const steps = workflowSteps(d);
  const items = steps.map((step, i) => {
    const v = stepView(d, step, (d.steps || {})[step.id]);
    const kids = [
      el("span", { class: "tl-dot", "aria-hidden": "true" }, v.cls === "done" ? "✓" : String(i + 1)),
      el("span", { class: "tl-name" }, STEP_NAME[step.id]),
      el("span", { class: "tl-when", title: v.cls === "done" || v.cls === "warn" ? fmtDate((d.steps || {})[step.id]?.at) : "" }, v.when),
      el("span", { class: "tl-hint muted small" }, STEP_HINT[step.id]),
    ];
    if (v.task) {
      const total = v.task.progress && Number(v.task.progress.total) > 0 ? Number(v.task.progress.total) : 0;
      const fill = el("div", { class: "fill" });
      const bar = el("div", { class: `bar${total ? "" : " indet"}`, role: "progressbar", "aria-label": STEP_NAME[step.id] }, fill);
      if (total) fill.style.width = `${Math.min(100, Math.floor(((Number(v.task.progress.current) || 0) / total) * 100))}%`;
      kids.push(bar);
      if (total) kids.push(el("span", { class: "tl-count muted small" }, `${fmtInt(v.task.progress.current)} of ${fmtInt(total)}`));
    } else if (!busy && d.mounted) {
      kids.push(stepButton(d, step));
    }
    return el("li", { class: `tl-step is-${v.cls}` }, kids);
  });
  timelineBox.hidden = !steps.length;
  timelineBox.replaceChildren(...items);
}

// Registered drives are solid and tinted by role. New drives (no volume_id) are dashed and neutral with a "New"
// pill. A remembered drive that is not plugged in is dimmed and says when it was last seen.
function driveCard(d) {
  const key = roleKey(d.role);
  const isNew = !d.volume_id;
  const pills = [el("span", { class: `pill role-${key}` }, roleLabel(d.role))];
  if (isNew) pills.push(el("span", { class: "pill role-new" }, "New"));
  const ignoreBtn = el("button", {
    type: "button", class: "drive-ignore", title: d.ignored ? "Show this drive in the list again" : "Hide this drive under Ignored",
    "aria-label": `${d.ignored ? "Stop ignoring" : "Ignore"} ${driveName(d)}`,
    onclick: (e) => { e.stopPropagation(); setIgnored(d, !d.ignored).catch(reportError); },
    onkeydown: (e) => e.stopPropagation(),
  }, d.ignored ? "Unignore" : "Ignore");
  const kids = [el("div", { class: "drive-head" }, pills, el("span", { class: "drive-name" }, driveName(d)), ignoreBtn)];
  if (d.mounted) {
    let sub = isNew
      ? (d.looks_like_card ? "A camera card, not checked yet. Click to see what is backed up."
        : d.local ? `The folder ${d.mount} on this Mac. Click to use it as a working drive.`
          : "Not set up yet. Click to set it up.")
      : `${d.fs || "Unknown format"} · ${d.free ?? "?"} free of ${d.size || "?"}`;
    if (d.local && !isNew) sub = `${d.mount} on this Mac · ${d.free ?? "?"} free`;
    kids.push(el("div", { class: "muted small" }, sub));
    const total = parseSize(d.size);
    const free = parseSize(d.free);
    if (total && free !== null) {
      const used = Math.min(100, Math.max(0, ((total - free) / total) * 100));
      const fill = el("span");
      fill.style.width = `${used.toFixed(1)}%`;
      kids.push(el("div", { class: `capbar${used >= 90 ? " high" : ""}`, role: "img", "aria-label": `${Math.round(used)} percent used` }, fill));
    }
    if (!isNew) {
      const status = driveStatus(d);
      kids.push(el("div", { class: `drive-status ${status.cls}` }, status.text));
    } else if (!d.looks_like_card) {
      const t = assignTaskFor(d.mount);
      if (t && !t.final) kids.push(el("div", { class: "drive-status running" }, `Setting up as ${assignRoleWord(t)}…`));
      else if (t && t.state === "failed") kids.push(el("div", { class: "drive-status warn" }, "Setup failed · click to see why"));
    }
  } else {
    const seen = d.last_seen_at ? ` · last seen ${relTime(d.last_seen_at)}` : "";
    kids.push(el("div", { class: "muted small", title: fmtDate(d.last_seen_at) }, `Not connected${seen}`));
  }
  const cls = ["drive", isNew ? "is-new" : "is-registered", `role-${key}`];
  if (!d.mounted) cls.push("gone");
  if (isCardDrive(d)) {
    // A camera card opens the Cards view: how much of it is backed up, and an Import button.
    const open = st.cardsOpen && st.cardView === d.volume_id;
    if (open) cls.push("open");
    const go = () => openCards(d.volume_id || null);
    return el("div", {
      class: `${cls.join(" ")} is-clickable`, role: "button", tabindex: "0", title: "See what is backed up",
      "aria-pressed": open ? "true" : "false", onclick: go,
      onkeydown: (e) => { if ((e.key === "Enter" || e.key === " ") && e.target === e.currentTarget) { e.preventDefault(); go(); } },
    }, kids);
  }
  if (isNew) {
    // A drive the catalog does not know yet opens the setup dialog, or, once a setup was started, its setup view.
    const open = st.setupView && st.setupView.drive.mount === d.mount;
    if (open) cls.push("open");
    return el("div", {
      class: `${cls.join(" ")} is-clickable`, role: "button", tabindex: "0", title: "Set up this drive",
      onclick: () => clickNewDrive(d),
      onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); clickNewDrive(d); } },
    }, kids);
  }
  // A registered drive opens its summary in the middle view. A click on one of its buttons does only that button's job.
  const open = st.driveView === d.volume_id;
  if (open) cls.push("open");
  return el("div", {
    class: `${cls.join(" ")} is-clickable`, role: "button", tabindex: "0",
    "aria-pressed": open ? "true" : "false",
    onclick: () => openDrive(d.volume_id),
    onkeydown: (e) => {
      if ((e.key === "Enter" || e.key === " ") && e.target === e.currentTarget) { e.preventDefault(); openDrive(d.volume_id); }
    },
  }, kids);
}

// Drives fall into Mounted, Not connected and Ignored (system volumes, tool installers, anything that is not
// photos), each under a header that folds. Ignored starts folded.
const DRIVE_GROUPS = [
  { key: "mounted", label: "Mounted", test: (d) => !d.ignored && d.mounted },
  { key: "unmounted", label: "Not connected", test: (d) => !d.ignored && !d.mounted },
  { key: "ignored", label: "Ignored", test: (d) => d.ignored },
];

function groupFolded(key) {
  return key === "ignored" ? !st.showIgnored : st.foldedGroups.has(key);
}

function toggleGroup(key) {
  if (key === "ignored") {
    st.showIgnored = !st.showIgnored;
    writePref("showIgnoredDrives", st.showIgnored ? "1" : "0");
  } else {
    if (st.foldedGroups.has(key)) st.foldedGroups.delete(key);
    else st.foldedGroups.add(key);
    writePref("driveFold", [...st.foldedGroups].join(","));
  }
  renderDrives();
}

function groupToggle(key, text, count) {
  const open = !groupFolded(key);
  return el("button", {
    type: "button", class: `link ignored-toggle group-toggle group-${key}`, "aria-expanded": open ? "true" : "false",
    onclick: () => toggleGroup(key),
  }, `${open ? "▾" : "▸"} ${text} (${count})`);
}

function renderDrives() {
  syncBase();
  renderHome();
  const box = $("#drives");
  const shown = st.drives.filter((d) => !d.ignored);
  const nodes = [];
  if (!st.drives.length || !shown.length) {
    nodes.push(el("p", { class: "muted small" }, st.drives.length
      ? "Every drive is ignored. Open Ignored to bring one back."
      : "No drives yet. Plug one in, and it appears here."));
  }
  for (const g of DRIVE_GROUPS) {
    const list = st.drives.filter(g.test);
    if (!list.length) continue;
    nodes.push(groupToggle(g.key, g.label, list.length));
    if (!groupFolded(g.key)) {
      nodes.push(el("div", { class: `drive-list${g.key === "ignored" ? " ignored-list" : ""}` }, list.map(driveCard)));
    }
  }
  box.replaceChildren(...nodes);
}

async function setIgnored(d, ignored) {
  await api("POST", "/api/drives/ignore", { key: d.key, ignored });
  for (const x of st.drives) if (x.key === d.key) x.ignored = ignored;
  renderDrives();
  toast(ignored ? `${driveName(d)} is ignored. Find it under Ignored.` : `${driveName(d)} is back in the list.`);
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
  if (st.driveView) loadDriveView().catch(reportError); // keeps the open summary current after runs and plug events
  loadCards().catch(() => {}); // the header dot, and the Cards view when it is open
}

// ---------------------------------------------------------------- dialogs for drive actions

// macOS mounts NTFS read-only, and working and master drives need the ID file written to them.
function readOnlyFs(d) {
  return !d.local && /ntfs/i.test(d.fs || "");
}

function openAssign(d, preset = {}) {
  st.assignDrive = d;
  $("#assign-name").textContent = d.local
    ? `Folder on this Mac: ${d.mount}. Only this folder is used.`
    : `Drive: ${baseName(d.mount) || driveName(d)}${d.fs ? ` · ${d.fs}` : ""}`;
  const form = $("#f-assign");
  const ro = readOnlyFs(d);
  for (const opt of form.elements.role.options) {  // a Mac folder has no disk UUID, so it cannot be a legacy drive
    const off = (Boolean(d.local) && opt.hasAttribute("data-not-local")) || (ro && opt.value !== "LEGACY");
    opt.hidden = off;
    opt.disabled = off;
  }
  const note = $("#assign-note");
  note.hidden = !ro;
  note.textContent = ro
    ? `macOS can read ${d.fs.toUpperCase()} drives but cannot write to them, so this drive can only be a legacy drive. `
      + "To use it as a working drive or master backup, reformat it as APFS or exFAT (this erases it)."
    : "";
  form.elements.role.value = ro ? "LEGACY" : (preset.role || "WORKING");
  form.elements.label.value = preset.label ?? d.label ?? "";
  form.elements.run_after.checked = preset.runAfter ?? true;
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
  if (a === "cards" && b === "check") return "card_check";
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
    case "card_check": return `Checking what is backed up on ${mountName(w[2])}`;
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
    case "card_check": {
      const c = r.counts || {};
      const n = (t) => fmtInt((c[t] || {}).files);
      return `${TIER[r.tier] ? TIER[r.tier].headline : ""} ${n("backed_up")} backed up, ${n("needs_backup")} need backup, `
        + `${n("only_on_card")} only on the card${(c.unchecked || {}).files ? `, ${n("unchecked")} not checked` : ""}.`;
    }
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
  v.line.textContent = (progressLine(t, now) || "Starting…") + (t.keep_awake ? " · keeping the Mac awake" : "");
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
    setChildren(box, st.elsewhere.length ? []
      : [el("p", { class: "muted small" }, "Nothing has run yet. Tasks you start appear here.")]);
    updateGlobal();
    syncDriveViews();
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
  syncDriveViews();
  renderHome();
}

// Keeps the sidebar status lines and the open drive's timeline current. The sidebar is rebuilt only when what
// is running changes, so a focused card keeps focus.
let driveSig = "";
function syncDriveViews() {
  const sig = st.drives.map((d) => {
    const a = d.volume_id ? null : assignTaskFor(d.mount);
    return `${d.volume_id}:${liveStep(d)?.step || ""}:${a ? a.state : ""}`;
  }).join("|");
  if (sig !== driveSig) { driveSig = sig; renderDrives(); }
  updateWorkflowUI();
  renderSetupView();
}

// The slim header bar: average progress when every running task is countable, else a moving bar.
// Runs started outside the UI count too.
function updateGlobal() {
  const running = [...st.tasks.values()].filter((t) => !t.final).concat(st.elsewhere.map(extTask));
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
    return task_id;
  } catch (err) {
    toast(err.message, "err");
    return null;
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
  t.keep_awake = Boolean(d.keep_awake);
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
  if (st.driveView && !st.workflow) await loadDriveView().catch(reportError);
  finishSetup();
}

// One loop for tasks: 1 s while anything runs, 5 s when idle. kick() runs it now.
let ticking = false;
let tickTimer = null;
let lastListMs = 0;
let lastActivityMs = 0;

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
    if (Date.now() - lastActivityMs >= ACTIVITY_MS) {
      lastActivityMs = Date.now();
      await loadActivity().catch(() => {});
    }
  } catch (err) {
    // The loop keeps running even when one pass fails.
  } finally {
    ticking = false;
    const live = [...st.tasks.values()].some((t) => !t.final) || st.elsewhere.length > 0;
    tickTimer = setTimeout(tick, live ? TASK_FAST_MS : TASK_IDLE_MS);
  }
}

function kick() {
  if (!ticking) {
    clearTimeout(tickTimer);
    tick();
  }
}

// ---------------------------------------------------------------- runs started outside the UI (ui-11)

// A run started elsewhere, in the shape the task helpers (taskTitle, progressLine, updateGlobal) expect.
// A run with a progress file has exact numbers; an older one has counts from its catalog job.
function extTask(e) {
  const j = e.job;
  const progress = e.progress || (j && j.current != null ? { current: j.current, total: j.total || 0 } : null);
  return {
    argv: e.words || [], progress, final: false,
    startMs: Date.parse(e.phase_started_at || e.started_at) || null,
  };
}

function agoText(iso, now) {
  const ms = Date.parse(iso || "");
  return Number.isNaN(ms) ? "" : `${fmtElapsed((now - ms) / 1000)} ago`;
}

// Progress for a run known only from the catalog: counts since its job started, its pace, and the newest write.
function catalogLine(e, now) {
  const j = e.job;
  if (!j) return `Running for ${fmtElapsed((now - Date.parse(e.started_at)) / 1000)}. No catalog job matched it.`;
  const cur = Number(j.current) || 0;
  const total = Number(j.total) || 0;
  const parts = [total
    ? `${fmtInt(cur)} of ${fmtInt(total)} ${j.unit} · ${Math.min(100, Math.floor((cur / total) * 100))}%`
    : `${fmtInt(cur)} ${j.unit}`];
  parts.push(`${fmtElapsed((now - Date.parse(e.started_at)) / 1000)} elapsed`);
  const since = (now - Date.parse(j.created_at)) / 1000;
  if (cur > 0 && since >= 60) {
    const rate = cur / since;
    parts.push(`${fmtInt(Math.round(rate * 60))} per min`);
    if (total && cur < total) parts.push(fmtEta((total - cur) / rate));
  }
  return parts.join(" · ");
}

// What the note under the bar says, and whether it is a warning.
function extNote(e, now) {
  if (e.source === "progress_file") {
    const msg = (e.progress && e.progress.message) || "";
    return { text: e.warning ? `${msg}${msg ? " · " : ""}${e.warnings} warning(s), last: ${e.warning}` : msg, warn: false };
  }
  const j = e.job;
  if (!j) return { text: "", warn: false };
  if (!j.last_write_at) {
    return { text: j.kind === "index_volume" ? "No files recorded yet. It is probably still walking the drive." : "No catalog writes yet.", warn: false };
  }
  const quiet = (now - Date.parse(j.last_write_at)) / 1000;
  if (quiet > QUIET_WARN_S) {
    return { text: `No catalog writes for ${fmtElapsed(quiet)}. It may be stuck on a slow or failing drive.`, warn: true };
  }
  return { text: `Last catalog write ${agoText(j.last_write_at, now)}.`, warn: false };
}

const extViews = new Map(); // pid -> node refs, kept so the bar's width transition runs between polls

function makeExtView() {
  const v = {};
  v.title = el("span", { class: "task-title" });
  v.pill = el("span", { class: "pill state-running" }, "Running");
  v.fill = el("div", { class: "fill" });
  v.bar = el("div", { class: "bar", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100" }, v.fill);
  v.line = el("div", { class: "task-line" });
  v.note = el("div", { class: "task-note" });
  v.origin = el("div", { class: "task-note" });
  v.code = el("code");
  v.tech = el("details", { class: "tech" }, el("summary", null, "Technical details"), v.code);
  v.root = el("div", { class: "task task-ext" },
    el("div", { class: "task-head" }, v.title, v.pill), v.bar, v.line, v.note, v.origin, v.tech);
  return v;
}

function paintExt(v, e, now) {
  const t = extTask(e);
  v.title.textContent = taskTitle(t);
  const total = t.progress && Number(t.progress.total) > 0 ? Number(t.progress.total) : 0;
  v.bar.classList.toggle("indet", !total);
  v.bar.setAttribute("aria-label", taskTitle(t));
  if (total) {
    const pct = Math.min(100, Math.floor(((Number(t.progress.current) || 0) / total) * 100));
    v.fill.style.width = `${pct}%`;
    v.bar.setAttribute("aria-valuenow", String(pct));
  } else {
    v.fill.style.width = "";
    v.bar.removeAttribute("aria-valuenow");
  }
  v.line.textContent = (e.source === "progress_file" ? progressLine(t, now) : catalogLine(e, now)) || "Starting…";
  const note = extNote(e, now);
  v.note.hidden = !note.text;
  v.note.textContent = note.text;
  v.note.classList.toggle("problem", note.warn);
  v.origin.textContent = e.source === "progress_file"
    ? "Started outside this window."
    : "Started outside this window. Counts come from the catalog; exact totals show for runs started after this update.";
  const j = e.job;
  v.code.textContent = `pid ${e.pid}${j ? ` · job ${j.job_id} (${j.kind})` : ""} · started ${e.started_at}\n`
    + `photovault ${(e.words || []).join(" ")}`;
}

let staleEl = null;
function staleBlock() {
  if (!staleEl) {
    staleEl = el("details", {
      class: "earlier",
      ontoggle: (ev) => { st.staleOpen = ev.currentTarget.open; },
    }, el("summary", null), el("p", { class: "muted small" },
      "A run ended without closing its job (for example the terminal was closed). Nothing is working on these."),
    el("div", null));
  }
  return staleEl;
}

const JOB_WORD = { index_volume: "Index", baseline_hash: "Hash", card_ingest: "Import", replicate: "Backup", fixity_audit: "Audit" };

function renderElsewhere() {
  const box = $("#elsewhere");
  const now = Date.now();
  const nodes = [];
  const pids = new Set();
  for (const e of st.elsewhere) {
    pids.add(e.pid);
    let v = extViews.get(e.pid);
    if (!v) { v = makeExtView(); extViews.set(e.pid, v); }
    paintExt(v, e, now);
    nodes.push(v.root);
  }
  for (const pid of [...extViews.keys()]) if (!pids.has(pid)) extViews.delete(pid);
  if (st.staleJobs.length) {
    const b = staleBlock();
    b.open = st.staleOpen;
    b.firstChild.textContent = `Marked running, but stopped (${st.staleJobs.length})`;
    setChildren(b.lastChild, st.staleJobs.map((j) => el("div", { class: "task-note" },
      `Job ${j.job_id} · ${JOB_WORD[j.kind] || j.kind} · ${j.label || "unknown drive"} · started ${relTime(j.created_at) || j.created_at}`)));
    nodes.push(b);
  }
  box.hidden = nodes.length === 0;
  setChildren(box, nodes);
  renderHome();
}

async function loadActivity() {
  const d = await api("GET", "/api/activity");
  const before = new Map(st.elsewhere.map((e) => [e.pid, e]));
  st.elsewhere = d.activity || [];
  st.staleJobs = d.stale_jobs || [];
  const gone = [...before.values()].filter((e) => !st.elsewhere.some((x) => x.pid === e.pid));
  renderElsewhere();
  renderTasks();
  if (gone.length) {
    for (const e of gone) toast(`${taskTitle(extTask(e))} has ended. It was started outside this window, so see its terminal for the result.`, "ok");
    await Promise.allSettled([loadDrives(), loadShoots(), loadGroups()]);
    if (st.driveView && !st.workflow) await loadDriveView().catch(reportError);
  }
}

// ---------------------------------------------------------------- home: tasks by drive (ui-13)

// With no drive, card or setup open, the middle view is the home page (tasks on each drive) or the photo grid.
function syncBase() {
  const base = !st.driveView && !st.setupView && !st.cardsOpen;
  $("#home-view").hidden = !(base && !st.photosOpen);
  $("#photo-panel").hidden = !(base && st.photosOpen);
  $("#btn-home").setAttribute("aria-pressed", base && !st.photosOpen ? "true" : "false");
  $("#btn-photos").setAttribute("aria-pressed", base && st.photosOpen ? "true" : "false");
}

// Closing a drive or the Cards view goes back to whichever of the two was open before.
function backLabel() {
  return st.photosOpen ? "Back to photos" : "Back to tasks";
}

function showBaseView(photos) {
  st.photosOpen = photos;
  if (st.cardsOpen) hideCards();
  if (st.driveView || st.setupView) closeDrive(); // closeDrive re-renders the sidebar, which calls syncBase
  else renderDrives();
}

// The mounts a command works on. An import lists its card first, then its target.
function taskMounts(words) {
  switch (commandKey(words)) {
    case "assign": case "card_check": return [words[2]];
    case "index": case "hash": case "card-status": return [words[1]];
    case "meta_scan": case "meta_sidecars": return [flagValue(words, "--volume")];
    case "ingest": return [flagValue(words, "--source"), flagValue(words, "--target")];
    default: return [];
  }
}

function sameMount(a, b) {
  return Boolean(a && b) && a.replace(/\/+$/, "") === b.replace(/\/+$/, "");
}

// The sidebar drive a mount belongs to, or a stand-in for a mount the drive list does not show.
function deviceForMount(mount) {
  const d = st.drives.find((x) => x.mount && sameMount(x.mount, mount));
  if (d) return { key: d.key || d.volume_id || `mount:${d.mount}`, drive: d };
  return { key: `path:${mount}`, drive: null, mount };
}

const homeViews = { dev: new Map(), task: new Map(), ext: new Map() }; // kept between polls, like the sidebar's

function homeTaskNode(devKey, t, now) {
  const k = `${devKey}|${t.task_id}`;
  let v = homeViews.task.get(k);
  if (!v) { v = makeTaskView(t.task_id); homeViews.task.set(k, v); }
  paintTask(v, t, now);
  return v.root;
}

function homeExtNode(devKey, e, now) {
  const k = `${devKey}|${e.pid}`;
  let v = homeViews.ext.get(k);
  if (!v) { v = makeExtView(); homeViews.ext.set(k, v); }
  paintExt(v, e, now);
  return v.root;
}

// Opens a card in the Cards view and a registered drive in its summary. A drive not set up yet has no Open:
// its sidebar entry starts the setup.
function openDevice(d) {
  if (isCardDrive(d)) openCards(d.volume_id || null);
  else openDrive(d.volume_id);
}

function deviceBlock(dev) {
  let v = homeViews.dev.get(dev.key);
  if (!v) {
    v = {};
    v.pills = el("span", { class: "dev-pills" });
    v.name = el("span", { class: "dev-name" });
    v.sub = el("span", { class: "muted small dev-sub" });
    v.open = el("button", { type: "button", class: "small-btn" }, "Open");
    v.head = el("div", { class: "dev-head" }, el("div", { class: "dev-title" }, v.pills, v.name), v.sub, v.open);
    v.running = el("div", { class: "dev-tasks" });
    v.recent = el("details", { class: "earlier" }, el("summary", null), el("div", null));
    v.root = el("article", { class: "dev" }, v.head, v.running, v.recent);
    homeViews.dev.set(dev.key, v);
  }
  const d = dev.drive;
  v.root.className = `dev role-${d ? roleKey(d.role) : "new"}`;
  v.name.textContent = d ? driveName(d) : mountName(dev.mount);
  v.pills.replaceChildren(el("span", { class: `pill role-${d ? roleKey(d.role) : "new"}` }, d ? roleLabel(d.role) : "Folder"));
  v.sub.textContent = !d ? "Not in the drive list"
    : !d.mounted ? `Not connected${d.last_seen_at ? ` · last seen ${relTime(d.last_seen_at)}` : ""}`
      : d.local ? `${d.mount} on this Mac · ${d.free ?? "?"} free`
        : `${d.fs || "Unknown format"} · ${d.free ?? "?"} free of ${d.size || "?"}`;
  const canOpen = Boolean(d) && (isCardDrive(d) || Boolean(d.volume_id));
  v.open.hidden = !canOpen;
  v.open.onclick = canOpen ? () => openDevice(d) : null;
  v.open.setAttribute("aria-label", `Open ${v.name.textContent}`);
  return v;
}

function renderHome() {
  const box = $("#home-view");
  if (!box || box.hidden) return;
  const now = Date.now();
  const devs = new Map(); // key -> { key, drive, mount, running: [nodes], finished: [tasks], stale: [jobs] }
  const devOf = (mount) => {
    const base = deviceForMount(mount);
    if (!devs.has(base.key)) devs.set(base.key, { ...base, running: [], finished: [], stale: [] });
    return devs.get(base.key);
  };
  const other = [];
  const all = [...st.tasks.values()].sort((a, b) => b.task_id - a.task_id);
  for (const t of all) {
    const mounts = taskMounts(argvWords(t.argv)).filter(Boolean);
    if (!mounts.length) { if (!t.final) other.push(homeTaskNode("other", t, now)); continue; }
    for (const m of mounts) {
      const dev = devOf(m);
      if (t.final) dev.finished.push(t);
      else dev.running.push(homeTaskNode(dev.key, t, now));
    }
  }
  for (const e of st.elsewhere) {
    const mounts = taskMounts(e.words || []).filter(Boolean);
    if (!mounts.length) { other.push(homeExtNode("other", e, now)); continue; }
    for (const m of mounts) {
      const dev = devOf(m);
      dev.running.push(homeExtNode(dev.key, e, now));
    }
  }
  const staleLeft = [];
  for (const j of st.staleJobs) {
    const d = st.drives.find((x) => x.label && x.label === j.label);
    if (d && d.mount) devOf(d.mount).stale.push(j);
    else staleLeft.push(j);
  }

  const busy = [...devs.values()].filter((x) => x.running.length);
  const nRunning = [...st.tasks.values()].filter((t) => !t.final).length + st.elsewhere.length;
  const h = homeParts(box);
  h.count.textContent = nRunning
    ? `${plural(nRunning, "task")} running${busy.length ? ` on ${plural(busy.length, "drive")}` : ""}`
      + (st.elsewhere.length ? ` · ${fmtInt(st.elsewhere.length)} started in a terminal` : "")
    : "Nothing is running";

  const used = new Set();
  const block = (dev, idle) => {
    const v = deviceBlock(dev);
    used.add(dev.key);
    v.root.classList.toggle("idle", idle);
    const kids = [...dev.running];
    for (const j of dev.stale) {
      kids.push(el("div", { class: "task-note problem" },
        `Job ${j.job_id} (${JOB_WORD[j.kind] || j.kind}) is marked running, but no process is doing it. `
        + `Started ${relTime(j.created_at) || j.created_at}.`));
    }
    if (idle && !kids.length) {
      const status = dev.drive && dev.drive.volume_id ? driveStatus(dev.drive) : null;
      const last = dev.finished[0];
      const s = last ? summarize(last) : null;
      kids.push(el("div", { class: `task-note${s && s.problem ? " problem" : ""}` },
        last ? `${taskTitle(last)}: ${s.text}` : (status ? status.text : "Nothing has run here yet.")));
    }
    setChildren(v.running, kids);
    const recent = idle ? [] : dev.finished.slice(0, RECENT_FINISHED);
    v.recent.hidden = !recent.length;
    if (recent.length) {
      v.recent.firstChild.textContent = `Finished here (${recent.length})`;
      setChildren(v.recent.lastChild, recent.map((t) => homeTaskNode(dev.key, t, now)));
    }
    return v.root;
  };
  setChildren(h.busy, busy.map((dev) => block(dev, false)));
  h.busy.hidden = !busy.length;
  h.empty.hidden = busy.length > 0;
  h.otherHead.hidden = h.other.hidden = !other.length;
  setChildren(h.other, other);

  // Connected drives with nothing running: one compact row each, with the last thing that ran.
  const idle = st.drives.filter((d) => d.mounted && !d.ignored && !used.has(deviceForMount(d.mount).key));
  h.idleHead.hidden = h.idle.hidden = !idle.length;
  h.idleHead.textContent = `Idle drives (${idle.length})`;
  setChildren(h.idle, idle.map((d) => {
    const base = deviceForMount(d.mount);
    return block(devs.get(base.key) || { ...base, running: [], finished: [], stale: [] }, true);
  }));

  h.staleHead.hidden = h.stale.hidden = !staleLeft.length;
  h.staleHead.textContent = `Marked running, but stopped (${staleLeft.length})`;
  h.stale.replaceChildren(...staleLeft.map((j) => el("div", { class: "task-note" },
    `Job ${j.job_id} · ${JOB_WORD[j.kind] || j.kind} · ${j.label || "unknown drive"} · started ${relTime(j.created_at) || j.created_at}`)));

  for (const k of [...homeViews.dev.keys()]) if (!used.has(k)) homeViews.dev.delete(k);
  for (const map of [homeViews.task, homeViews.ext]) {
    for (const [k, v] of [...map]) if (!v.root.isConnected) map.delete(k);
  }
}

// The home page's fixed parts, built once, so focused buttons and open details survive the 1 s repaint.
let homeEl = null;
function homeParts(box) {
  if (homeEl) return homeEl;
  const h = {};
  h.count = el("span", { class: "muted small" });
  h.busy = el("div", { class: "dev-list" });
  h.empty = el("p", { class: "muted home-empty" },
    "No task is running. Open a drive to index, hash or scan it, or open Cards to import a card.");
  h.otherHead = el("h3", { class: "sub" }, "Other tasks");
  h.other = el("div", { class: "dev-tasks" });
  h.idleHead = el("h3", { class: "sub" });
  h.idle = el("div", { class: "dev-list compact" });
  h.staleHead = el("h3", { class: "sub" });
  h.stale = el("div", { class: "dev-tasks" });
  box.replaceChildren(el("div", { class: "section-head" }, el("h2", null, "Tasks"), h.count),
    h.busy, h.empty, h.otherHead, h.other, h.idleHead, h.idle, h.staleHead, h.stale);
  homeEl = h;
  return h;
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

// ---------------------------------------------------------------- drive summary (middle view)

// Opening a registered drive replaces the photo grid with what the drive holds: its files by state, the index and
// hash runs with their numbers, and the file list (with Show in Finder). Read-only: nothing here changes the catalog.
const FILE_FILTERS = [
  ["", "All files"], ["indexed", "Not hashed yet"], ["verified", "Hashed"], ["mismatch", "Unreadable"], ["missing", "Missing"],
];
const FILE_STATE_TILES = [
  ["indexed", "Not hashed yet"], ["verified", "Hashed"], ["mismatch", "Unreadable"], ["missing", "Missing"],
];
const FILE_STATE_NAME = { indexed: "Not hashed yet", verified: "Hashed", mismatch: "Unreadable", missing: "Missing" };
const FILE_PAGE = 200;

// ---------------------------------------------------------------- setting up a new drive

// The newest assign task for a mount (live or finished), or null.
function assignTaskFor(mount) {
  if (!mount) return null;
  let best = null;
  for (const t of st.tasks.values()) {
    const w = argvWords(t.argv);
    if (commandKey(w) === "assign" && w[2] === mount && (!best || t.task_id > best.task_id)) best = t;
  }
  const sv = st.setupView;  // a task just started has no argv until its first poll
  if (sv && sv.drive.mount === mount && sv.taskId != null && (!best || sv.taskId > best.task_id)) {
    best = st.tasks.get(sv.taskId) || best;
  }
  return best;
}

function assignRoleWord(t) {
  const role = flagValue(argvWords(t.argv), "--role")
    || (st.setupView && st.setupView.taskId === t.task_id ? st.setupView.role : "");
  return ROLE_WORD[role] || "a drive";
}

// A new drive with a setup already started (running, or failed) shows that setup; otherwise the dialog opens.
function clickNewDrive(d) {
  const t = assignTaskFor(d.mount);
  if (t && t.state !== "done") openSetup(d, { taskId: t.task_id });
  else openAssign(d);
}

function openSetup(d, { taskId, role, label, runAfter } = {}) {
  hideCards();
  st.driveView = null;
  st.driveSummary = null;
  timelineBox = workflowBtn = null;
  const t = st.tasks.get(taskId);
  const keep = st.setupView && st.setupView.taskId === taskId ? st.setupView : {};
  st.setupView = {
    drive: d, taskId,
    role: role || keep.role || (t ? flagValue(argvWords(t.argv), "--role") : "") || "",
    label: label ?? keep.label ?? "",
    runAfter: runAfter ?? keep.runAfter ?? false,
  };
  setupSig = "";
  $("#photo-panel").hidden = true;
  $("#drive-view").hidden = false;
  renderSetupView();
  renderDrives();
  finishSetup();
}

// Plain words for why a setup failed, and whether the drive is read-only to this Mac.
function setupProblem(t, d) {
  const msg = t.error || `The setup stopped (exit code ${t.exit_code ?? "unknown"}).`;
  if (/Errno 30|Read-only file system/i.test(msg)) {
    const fs = (d.fs || "").toUpperCase();
    return {
      readOnly: true,
      lines: [
        fs === "NTFS"
          ? "macOS can read NTFS drives but cannot write to them, so the drive's ID file could not be written."
          : "This drive is mounted read-only, so the drive's ID file could not be written.",
        "A working drive or master backup gets a small ID file (.photovault-id) at its top, so it is recognised "
          + "whatever its name. Nothing was written and nothing on the drive changed.",
        "You can set it up as a legacy drive instead: nothing is written to it, and its photos are listed, hashed "
          + "and counted as copies. To use it as a master backup, reformat it as APFS or exFAT first (this erases it).",
      ],
      msg,
    };
  }
  return { readOnly: false, lines: [msg], msg };
}

let setupSig = "";
let setupElapsed = null;

// The middle view while a new drive is set up: the setup step, then the workflow that follows it. Rebuilt only
// when the task's state changes; the elapsed time is updated in place.
function renderSetupView() {
  const sv = st.setupView;
  if (!sv) return;
  const d = sv.drive;
  const t = sv.taskId != null ? st.tasks.get(sv.taskId) : null;
  const state = t ? t.state : "running";
  if (t && setupElapsed && state === "running") {
    setupElapsed.textContent = t.startMs ? `${fmtElapsed((Date.now() - t.startMs) / 1000)} elapsed` : "";
  }
  const sig = `${sv.taskId}:${state}:${t ? t.error : ""}:${sv.role}`;
  if (sig === setupSig) return;
  setupSig = sig;
  setupElapsed = null;

  const role = sv.role || (t ? flagValue(argvWords(t.argv), "--role") : "") || "";
  const roleWord = ROLE_WORD[role] || "a drive";
  const steps = ["index", "hash", "scan"].concat(role === "WORKING" ? ["sidecars"] : []);
  const failed = state === "failed" || state === "paused";
  const done = state === "done";
  const after = sv.runAfter ? "Starts after setup" : "Run it after setup";

  const setupStep = el("li", { class: `tl-step is-${failed ? "warn" : done ? "done" : "running"}` },
    el("span", { class: "tl-dot", "aria-hidden": "true" }, done ? "✓" : "1"),
    el("span", { class: "tl-name" }, "Set up"),
    el("span", { class: "tl-when" }, failed ? "Failed" : done ? "Done" : "Running now"),
    el("span", { class: "tl-hint muted small" }, `Add it to the catalog as ${roleWord}`),
    failed || done ? null : el("div", { class: "bar indet", role: "progressbar", "aria-label": "Set up" }, el("div", { class: "fill" })));
  const items = [setupStep, ...steps.map((id, i) => el("li", { class: `tl-step is-${failed ? "never" : "queued"}` },
    el("span", { class: "tl-dot", "aria-hidden": "true" }, String(i + 2)),
    el("span", { class: "tl-name" }, STEP_NAME[id]),
    el("span", { class: "tl-when" }, failed ? "Not started" : after),
    el("span", { class: "tl-hint muted small" }, STEP_HINT[id])))];

  const head = el("div", { class: "section-head" },
    el("h2", null, driveName(d)),
    el("span", { class: "muted small" }, failed ? "Setup failed" : `Setting up as ${roleWord}`));
  const back = button("Back to photos", closeDrive);
  let body;
  if (failed) {
    const p = setupProblem(t, d);
    const actions = p.readOnly
      ? [button("Set up as legacy drive…", () => openAssign(d, { role: "LEGACY", label: sv.label }), "primary"), back]
      : [button("Try again…", () => openAssign(d, { role, label: sv.label }), "primary"), back];
    body = el("div", { class: "empty" },
      el("h3", null, `Could not set up ${driveName(d)} as ${roleWord}`),
      el("div", { class: "setup-why" },
        p.lines.map((line) => el("p", null, line)),
        el("p", null, el("code", null, p.msg))),
      el("div", { class: "empty-actions" }, actions));
  } else {
    setupElapsed = el("span", { class: "muted small" },
      t && t.startMs ? `${fmtElapsed((Date.now() - t.startMs) / 1000)} elapsed` : "");
    body = el("div", null,
      el("div", { class: "loading", role: "status" },
        el("span", { class: "spinner", "aria-hidden": "true" }),
        el("span", { class: "muted" }, done ? "Set up. Opening the drive…" : "Writing the drive's ID file and adding it to the catalog…"),
        setupElapsed),
      el("p", { class: "muted small" }, sv.runAfter
        ? "When setup finishes, this page shows the drive and the workflow starts."
        : "When setup finishes, this page shows the drive. Press Run workflow there to index and hash it."),
      el("div", { class: "bulkbar" }, back));
  }
  $("#drive-view").replaceChildren(head,
    el("p", { class: "muted small" }, [d.fs, d.size ? `${d.size} total` : null, d.free ? `${d.free} free` : null].filter(Boolean).join(" · ")),
    el("ol", { class: "timeline", "aria-label": "Setup and workflow" }, items),
    body);
}

// When the setup task has finished and the drive list knows the new volume, open its summary (and start the
// workflow if asked).
function finishSetup() {
  const sv = st.setupView;
  if (!sv) return;
  const t = sv.taskId != null ? st.tasks.get(sv.taskId) : null;
  if (!t || t.state !== "done") return;
  const d = st.drives.find((x) => x.volume_id && x.mount === sv.drive.mount);
  if (!d) return;
  openDrive(d.volume_id);
  if (sv.runAfter) runWorkflow(d).catch(reportError);
}

// Placeholder while a drive's summary loads: a spinner, then grey blocks in the shape of the real summary.
function renderDriveLoading(volumeId) {
  const d = st.drives.find((x) => x.volume_id === volumeId) || {};
  $("#drive-view").replaceChildren(
    el("div", { class: "section-head" },
      el("h2", null, driveName(d)),
      el("span", { class: "muted small" }, d.role ? roleLabel(d.role) : "")),
    el("div", { class: "loading", role: "status" },
      el("span", { class: "spinner", "aria-hidden": "true" }),
      el("span", { class: "muted" }, "Reading this drive…")),
    el("div", { class: "skel-stats", "aria-hidden": "true" }, [0, 1, 2, 3].map(() => el("div", { class: "skel-tile" }))),
    el("div", { class: "skel-line", "aria-hidden": "true" }),
    el("div", { class: "skel-line short", "aria-hidden": "true" }));
}

function openDrive(volumeId) {
  hideCards();
  st.setupView = null;
  setupSig = "";
  st.driveView = volumeId;
  st.driveSummary = null;
  timelineBox = workflowBtn = null;
  st.driveFiles = { state: "", copies: "", camera: "", offset: 0 };
  $("#photo-panel").hidden = true;
  $("#drive-view").hidden = false;
  renderDriveLoading(volumeId);
  renderDrives();
  loadDriveView().catch((err) => {
    // Say why the panel is empty; a stale server (started before this UI) answers "unknown endpoint" here.
    reportError(err);
    if (st.driveView !== volumeId) return;
    $("#drive-view").replaceChildren(el("div", { class: "empty" },
      el("h3", null, "Could not load this drive"),
      el("p", null, err.message),
      button(backLabel(), closeDrive)));
  });
}

function closeDrive() {
  st.setupView = null;
  setupSig = "";
  st.driveView = null;
  st.driveSummary = null;
  timelineBox = workflowBtn = null;
  $("#drive-view").hidden = true;
  $("#drive-view").replaceChildren();
  renderDrives();
}

async function loadDriveView() {
  const id = st.driveView;
  if (!id) return;
  const params = new URLSearchParams({ limit: String(FILE_PAGE), offset: String(st.driveFiles.offset) });
  if (st.driveFiles.state) params.set("state", st.driveFiles.state);
  const copies = st.driveFiles.copies;
  if (copies.startsWith("on:")) params.set("other", copies.slice(3));
  else if (copies) params.set("copies", copies);
  if (st.driveFiles.camera) params.set("camera", st.driveFiles.camera);
  const base = `/api/volumes/${encodeURIComponent(id)}`;
  const [summary, files] = await Promise.all([api("GET", base), api("GET", `${base}/files?${params}`)]);
  if (st.driveView !== id) return; // the user opened another drive, or closed this one, while this loaded
  renderDriveView(summary, files.files, files.total);
}

async function revealOnDrive(relPath, volumeId = st.driveView) {
  const id = volumeId;
  if (!id) return;
  try {
    await api("POST", `/api/volumes/${encodeURIComponent(id)}/reveal`, relPath ? { rel_path: relPath } : {});
  } catch (err) {
    reportError(err);
  }
}

// One line per run: what it did and its final numbers. Runs from before stats were saved say so.
function runItem(r) {
  const res = r.result;
  let detail;
  if (!res) detail = "No stats were saved for this run.";
  else if (r.kind === "index_volume") {
    detail = `${plural(res.files, "file")} seen · ${fmtInt(res.indexed)} indexed · ${fmtInt(res.unchanged)} unchanged · `
      + `${fmtInt(res.missing)} missing · ${fmtInt(res.errors)} read errors`;
  } else {
    detail = `${fmtInt(res.hashed)} of ${fmtInt(res.files)} hashed (${fmtBytes(res.bytes)}) · ${fmtInt(res.skipped)} skipped · `
      + `${fmtInt(res.unreadable)} unreadable · ${fmtInt(res.deferred)} deferred · ${fmtInt(res.excluded)} excluded`;
  }
  const when = `${STATE_LABEL[r.state] || r.state}${r.started_at ? ` · started ${fmtDate(r.started_at)}` : ""}`;
  return el("li", { class: "run" },
    el("div", null, el("strong", null, r.label), " ", el("span", { class: "muted small" }, when)),
    el("div", { class: "small" }, detail));
}

function renderDriveView(s, files, total) {
  const d = s.drive || {};
  const isCard = roleKey(d.role) === "card";
  st.driveSummary = s;
  workflowBtn = null;
  timelineBox = null;
  const mounted = Boolean(d.mounted);
  const hashed = Object.values(s.states).reduce((n, x) => n + x.hashed, 0);
  const pct = s.files ? Math.round((hashed / s.files) * 100) : 0;
  const isEmpty = s.files === 0;

  const tiles = FILE_STATE_TILES.map(([key, label]) => {
    const x = s.states[key];
    const on = st.driveFiles.state === key;
    return el("button", {
      type: "button", class: `stat state-${key}${on ? " on" : ""}`, "aria-pressed": on ? "true" : "false",
      onclick: () => setDriveFilter(key),
    },
      el("span", { class: "stat-label" }, label),
      el("span", { class: "stat-num" }, fmtInt(x.files)),
      el("span", { class: "muted small" }, fmtBytes(x.bytes)));
  });

  const kinds = s.kinds.map((k) => `${k.kind} ${fmtInt(k.files)}`).join(" · ");
  const pager = el("div", { class: "pager" },
    button("Previous", () => { st.driveFiles.offset = Math.max(0, st.driveFiles.offset - FILE_PAGE); loadDriveView().catch(reportError); }),
    el("span", { class: "muted small" }, files.length
      ? `${fmtInt(st.driveFiles.offset + 1)}–${fmtInt(st.driveFiles.offset + files.length)} of ${fmtInt(total)}` : "No files"),
    button("Next", () => { st.driveFiles.offset += FILE_PAGE; loadDriveView().catch(reportError); }));
  pager.querySelector("button:first-child").disabled = st.driveFiles.offset === 0;
  pager.querySelector("button:last-child").disabled = st.driveFiles.offset + files.length >= total;

  const rows = files.map((f) => el("tr", null,
    el("td", { class: "path" }, f.rel_path),
    el("td", { class: "num" }, fmtBytes(f.size)),
    el("td", null, f.kind),
    el("td", null, f.camera_name),
    el("td", null, el("span", { class: `pill state-${f.state}` }, FILE_STATE_NAME[f.state] || f.state)),
    el("td", null, copyCell({ ...f, hashed: f.state === "verified" && f.copies !== null && f.copies !== undefined },
      st.driveView, "this drive")),
    el("td", null, mounted ? button("Show in Finder", () => revealOnDrive(f.rel_path)) : null)));

  const filterBar = fileFilterBar({
    show: { options: FILE_FILTERS, value: st.driveFiles.state, onchange: setDriveFilter },
    copies: { options: copyFilterOptions("this drive", (s.copies && s.copies.by_volume) || []),
      value: st.driveFiles.copies, onchange: setCopyFilter },
    camera: { options: cameraFilterOptions(s.cameras || []), value: st.driveFiles.camera,
      onchange: (camera) => { st.driveFiles = { ...st.driveFiles, camera, offset: 0 }; loadDriveView().catch(reportError); } },
    shown: total, total: s.files,
  });

  $("#drive-view").replaceChildren(
    el("div", { class: "section-head" },
      el("h2", null, driveName(d)),
      el("span", { class: "muted small" }, d.role ? roleLabel(d.role) : "")),
    el("div", { class: "bulkbar" },
      isCard
        ? button("Import…", () => openImport(d), "primary")
        : (workflowBtn = el("button", { type: "button", class: "primary", onclick: () => runWorkflow({ ...d, steps: s.steps }) }, "Run workflow")),
      el("button", {
        type: "button", disabled: !mounted,
        title: mounted ? undefined : "Connect this drive to show it in Finder",
        onclick: () => revealOnDrive(""),
      }, "Show in Finder"),
      button(backLabel(), closeDrive)),
    (timelineBox = el("ol", { class: "timeline", "aria-label": "Workflow" })),
    el("p", { class: "muted small" },
      [d.fs || "Unknown format", d.size ? `${d.size} total` : null,
        mounted ? (d.free ? `${d.free} free` : null) : `Not connected${d.last_seen_at ? ` · last seen ${relTime(d.last_seen_at)}` : ""}`]
        .filter(Boolean).join(" · ")),
    isEmpty
      ? el("div", { class: "empty" },
        el("h3", null, "Nothing indexed on this drive yet"),
        el("p", null, roleKey(d.role) === "card"
          ? "No file records here yet. Files appear once a card is imported onto this drive."
          : "Press Run workflow to list its files, verify them and read their metadata."))
      : el("div", null,
        el("div", { class: "overview" },
          el("span", null, plural(s.files, "file")),
          el("span", null, fmtBytes(s.bytes)),
          el("span", null, `${pct}% hashed`),
          kinds ? el("span", { class: "muted small" }, kinds) : null),
        el("div", { class: "stats" }, tiles),
        copySection(s),
        el("h3", { class: "sub" }, "Runs"),
        s.runs.length
          ? el("ul", { class: "runs" }, s.runs.map(runItem))
          : el("p", { class: "muted small" }, "No index or hash runs yet."),
        el("h3", { class: "sub" }, "Files"),
        filterBar,
        rows.length
          ? el("div", { class: "table-wrap" }, el("table", { class: "file-table" },
            el("thead", null, el("tr", null,
              el("th", null, "Path"), el("th", { class: "num" }, "Size"), el("th", null, "Kind"),
              el("th", null, "Camera"), el("th", null, "State"), el("th", null, "Copies"), el("th", null, ""))),
            el("tbody", null, rows)))
          : el("p", { class: "muted small" }, "No files in this view."),
        pager));
  updateWorkflowUI();
}

function setDriveFilter(state) {
  st.driveFiles = { ...st.driveFiles, state, offset: 0 };
  loadDriveView().catch(reportError);
}

function setCopyFilter(copies) {
  st.driveFiles = { ...st.driveFiles, copies, offset: 0 };
  loadDriveView().catch(reportError);
}

// ---------------------------------------------------------------- copies across drives (ui-8)

// Where this drive's files also live. Identity is the full hash: only hashed (verified) files are compared, and a
// card never counts as a copy, the same rule as `report duplicates`. Read-only: nothing here deletes or marks a file.
// The same Copies choices on a drive and on a card; `here` is "this drive" or "this card", and each other drive
// that holds a copy gets its own named choice.
function copyFilterOptions(here, byVolume) {
  return [["", "All files"], ["elsewhere", "On other drives"], ["only_here", `Only on ${here}`],
    ["dup_here", `Duplicated on ${here}`]]
    .concat(byVolume.map((v) => [`on:${v.volume_id}`, `Also on ${driveName(v)} (${roleLabel(v.role)})`]));
}

function cameraFilterOptions(cameras) {
  return [["", "All cameras"]].concat(cameras.map((m) => [m.camera, `${m.name} (${fmtInt(m.files)})`]));
}

// The filter bar shared by the drive and card views: Show, Copies and Camera, then how many files this view
// shows out of all the files on the drive or card ("x / N files").
function fileFilterBar({ show, copies, camera, shown, total }) {
  const field = (label, f) => {
    const select = el("select", { "aria-label": label, onchange: (e) => f.onchange(e.currentTarget.value) },
      f.options.map(([value, text]) => el("option", { value }, text)));
    select.value = f.value;
    return el("label", { class: "field" }, label, select);
  };
  const filtered = Boolean(show.value || copies.value || camera.value);
  return el("div", { class: "toolbar" },
    field("Show", show), field("Copies", copies), field("Camera", camera),
    el("div", { class: "filter-count", role: "status", "aria-live": "polite",
      title: filtered ? "Files in this view / all files" : "All files" },
    el("span", { class: "filter-count-num" }, `${fmtInt(shown)} / ${fmtInt(total)}`),
    el("span", { class: "muted small" }, total === 1 ? "file" : "files")));
}

// "A", "A and B", "A, B and C".
function nameList(names) {
  return names.length < 2 ? (names[0] || "") : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

function copySection(s) {
  const c = s.copies;
  const head = el("h3", { class: "sub" }, "Copies on other drives");
  if (!c || !c.hashed) {
    return el("div", null, head,
      el("p", { class: "muted small" }, "Hash this drive to see which of its files also live on other drives."));
  }
  const tile = (key, label, files, detail) => {
    const on = st.driveFiles.copies === key;
    return el("button", {
      type: "button", class: `stat copy-${key}${on ? " on" : ""}`, "aria-pressed": on ? "true" : "false",
      onclick: () => setCopyFilter(on ? "" : key),
    },
      el("span", { class: "stat-label" }, label),
      el("span", { class: "stat-num" }, fmtInt(files)),
      el("span", { class: "muted small" }, detail));
  };
  const pending = s.states.indexed ? s.states.indexed.files : 0;
  const drives = c.by_volume.map((v) => {
    const key = `on:${v.volume_id}`;
    const on = st.driveFiles.copies === key;
    return el("li", null, el("button", {
      type: "button", class: `copy-drive${on ? " on" : ""}`, "aria-pressed": on ? "true" : "false",
      onclick: () => setCopyFilter(on ? "" : key),
    },
      el("span", { class: `pill role-${roleKey(v.role)}` }, roleLabel(v.role)),
      el("strong", null, driveName(v)),
      el("span", { class: "muted small" }, v.mounted ? "Connected" : "Not connected"),
      el("span", { class: "copy-drive-num" }, `${plural(v.files, "file")} · ${fmtBytes(v.bytes)}`)));
  });
  return el("div", null, head,
    el("div", { class: "stats" },
      tile("elsewhere", "On other drives", c.elsewhere.files,
        [fmtBytes(c.elsewhere.bytes), nameList(c.by_volume.map(driveName))].filter(Boolean).join(" · ")),
      tile("only_here", "Only on this drive", c.only_here.files, fmtBytes(c.only_here.bytes)),
      tile("dup_here", "Duplicated on this drive", c.dup_here.files,
        `${fmtBytes(c.dup_here.extra_bytes)} in extra copies`)),
    drives.length
      ? el("ul", { class: "copy-drives" }, drives)
      : el("p", { class: "muted small" }, "No other drive holds a verified copy of these files yet."),
    pending
      ? el("p", { class: "muted small" }, `${plural(pending, "file")} not hashed yet, so not compared. `
        + "Other drives count only once they are hashed too.")
      : null);
}

// The Copies cell, the same on a drive and on a card: the other drives that hold the same bytes, by name, and
// whether this drive (or card) holds it twice. Click to list every copy, each with Show in Finder when connected.
// `f` has hashed, copy_drives ({label, role} each), dup_here and asset_id; `volumeId` is the drive or card being shown.
function copyCell(f, volumeId, here) {
  if (!f.hashed) return el("span", { class: "muted small" }, "–");
  const names = (f.copy_drives || []).map((v) => `${driveName(v)} (${roleLabel(v.role)})`);
  const pills = [
    names.length
      ? el("span", { class: "pill copy-elsewhere", title: `Also on ${nameList(names)}` }, `On ${nameList(names)}`)
      : el("span", { class: "pill copy-only_here" }, `Only on ${here}`),
    f.dup_here > 0 ? el("span", { class: "pill copy-dup_here" }, `×${f.dup_here + 1} on ${here}`) : null,
  ];
  if ((!names.length && !f.dup_here) || !f.asset_id) return el("span", { class: "copy-pills" }, pills);
  return el("button", {
    type: "button", class: "copy-toggle", "aria-expanded": "false", title: "Show every copy",
    onclick: (e) => toggleCopies(e.currentTarget, f, volumeId),
  }, pills);
}

async function toggleCopies(btn, f, volumeId) {
  const tr = btn.closest("tr");
  const next = tr.nextElementSibling;
  if (next && next.classList.contains("copy-row")) {
    next.remove();
    btn.setAttribute("aria-expanded", "false");
    return;
  }
  btn.setAttribute("aria-expanded", "true");
  const cell = el("td", { colspan: String(tr.children.length) }, el("span", { class: "muted small" }, "Finding copies…"));
  const row = el("tr", { class: "copy-row" }, cell);
  tr.after(row);
  try {
    const data = await api("GET", `/api/assets/${encodeURIComponent(f.asset_id)}/copies`);
    cell.replaceChildren(el("ul", { class: "copy-list" }, data.copies.map((c) => {
      const self = c.volume_id === volumeId && c.rel_path === f.rel_path;
      const isCard = roleKey(c.role) === "card";
      return el("li", null,
        el("span", { class: `pill role-${roleKey(c.role)}` }, roleLabel(c.role)),
        el("strong", null, driveName(c)),
        el("span", { class: "path" }, c.rel_path),
        self ? el("span", { class: "muted small" }, "(this file)") : null,
        isCard && !self ? el("span", { class: "muted small" }, `(${driveName(c)} is a card, so it does not count as a copy)`) : null,
        c.lr_referenced ? el("span", { class: "pill" }, "Lightroom") : null,
        c.mounted
          ? button("Show in Finder", () => revealOnDrive(c.rel_path, c.volume_id))
          : el("span", { class: "muted small" }, `${driveName(c)} is not connected`));
    })));
  } catch (err) {
    cell.replaceChildren(el("span", { class: "muted small" }, `Could not list copies: ${err.message}`));
  }
}

// ---------------------------------------------------------------- cards (card-3): what is safe to remove

// Each file on a card is ranked by where else its bytes are (full hash; a card never counts as a copy, D-046):
// green on a backup (master) drive, yellow only on a working or legacy drive, red only on this card, grey not
// checked yet. A card and a camera take the worst colour of their files. Read-only: nothing here changes a card.
const TIER = {
  only_on_card: { label: "Only on this card", pill: "Back up now", headline: "Back up now." },
  unchecked: { label: "Not checked", pill: "Not checked", headline: "Not checked yet." },
  needs_backup: { label: "Needs backup", pill: "Needs backup", headline: "Needs backup." },
  backed_up: { label: "Backed up", pill: "Safe to remove", headline: "Safe to remove." },
  empty: { label: "Empty", pill: "Empty", headline: "No photos on this card." },
};
const TIER_ORDER = ["only_on_card", "unchecked", "needs_backup", "backed_up"]; // worst first
const TIER_HINT = {
  only_on_card: "No other drive has these files. Import or copy them before you format the card.",
  unchecked: "Check the card to compare it with your other drives. Checking only reads the card.",
  needs_backup: "They are on a working or legacy drive, but not on a backup (master) drive yet.",
  backed_up: "Every file is on a backup (master) drive.",
  empty: "",
};
const CARD_PAGE = 200;

function isCardDrive(d) {
  return roleKey(d.role) === "card" || Boolean(d.looks_like_card);
}

function tierPill(tier, text) {
  return el("span", { class: `pill t-${tier}` }, el("span", { class: "tier-dot", "aria-hidden": "true" }),
    text || (TIER[tier] || {}).pill || tier);
}

function tierBar(counts, total) {
  const bar = el("div", { class: "tierbar", role: "img",
    "aria-label": TIER_ORDER.map((t) => `${fmtInt(counts[t].files)} ${TIER[t].label.toLowerCase()}`).join(", ") });
  if (!total) return bar;
  for (const t of TIER_ORDER) {
    if (!counts[t].files) continue;
    const span = el("span", { class: `t-${t}`, title: `${TIER[t].label}: ${fmtInt(counts[t].files)}` });
    span.style.width = `${((counts[t].files / total) * 100).toFixed(2)}%`;
    bar.append(span);
  }
  return bar;
}

function tierCounts(counts) {
  return TIER_ORDER.filter((t) => counts[t].files)
    .map((t) => `${fmtInt(counts[t].files)} ${TIER[t].label.toLowerCase()}`).join(" · ");
}

// The drives that hold copies of a card's files, by name: "X9 Pro (Working) and Flats 1 (Legacy)".
function cardCopyNames(c, roles) {
  return nameList((c.copies_by_volume || []).filter((v) => !roles || roles.includes(v.role))
    .map((v) => `${driveName(v)} (${roleLabel(v.role)})`));
}

function cardHint(c) {
  if (c.tier === "needs_backup") {
    const on = cardCopyNames(c, ["WORKING", "LEGACY"]);
    return on ? `They are on ${on}, but not on a backup (master) drive yet.` : TIER_HINT.needs_backup;
  }
  if (c.tier === "backed_up") {
    const on = cardCopyNames(c, ["MASTER"]);
    return on ? `Every file is on ${on}.` : TIER_HINT.backed_up;
  }
  return TIER_HINT[c.tier];
}

function cardHeadline(c) {
  const red = c.counts.only_on_card.files;
  if (c.tier === "only_on_card") return `Back up now: ${plural(red, "file")} only on this card.`;
  if (c.tier === "unchecked") return `Not checked: ${plural(c.counts.unchecked.files, "file")} still to compare.`;
  if (c.tier === "needs_backup") {
    const on = cardCopyNames(c, ["WORKING", "LEGACY"]);
    return `Needs backup: ${plural(c.counts.needs_backup.files, "file")} ${on ? `only on ${on}, ` : ""}not on a backup drive yet.`;
  }
  if (c.tier === "backed_up") {
    const on = cardCopyNames(c, ["MASTER"]);
    return `Safe to remove: every file is on ${on ? `backup drive ${on}` : "a backup drive"}.`;
  }
  return TIER.empty.headline;
}

function hideCards() {
  st.cardsOpen = false;
  st.cardView = null;
  $("#cards-view").hidden = true;
  $("#cards-view").replaceChildren();
  $("#btn-cards").setAttribute("aria-pressed", "false");
}

function openCards(volumeId) {
  if (st.driveView || st.setupView) closeDrive();
  st.cardsOpen = true;
  st.cardView = volumeId;
  st.cardFiles = { tier: "", copies: "", other: "", camera: "", offset: 0 };
  $("#photo-panel").hidden = true;
  $("#drive-view").hidden = true;
  $("#cards-view").hidden = false;
  $("#btn-cards").setAttribute("aria-pressed", "true");
  $("#cards-view").replaceChildren(el("div", { class: "loading", role: "status" },
    el("span", { class: "spinner", "aria-hidden": "true" }), el("span", { class: "muted" }, "Reading cards…")));
  renderDrives();
  loadCards().catch((err) => {
    reportError(err);
    $("#cards-view").replaceChildren(el("div", { class: "empty" },
      el("h3", null, "Could not load cards"), el("p", null, err.message), button(backLabel(), closeCards)));
  });
}

function closeCards() {
  hideCards();
  renderDrives();
}

async function loadCards() {
  const data = await api("GET", "/api/cards");
  st.cards = data;
  const connected = data.cards.filter((c) => c.mounted);
  const dot = $("#cards-dot");
  dot.hidden = connected.length === 0;
  if (connected.length) {
    const worstTier = TIER_ORDER.find((t) => connected.some((c) => c.tier === t)) || "empty";
    dot.className = `tier-dot t-${worstTier}`;
    $("#btn-cards").title = `Connected cards: ${TIER[worstTier].label.toLowerCase()}`;
  }
  if (!st.cardsOpen) return;
  if (st.cardView) await loadCardDetail();
  else renderCards(data);
}

function cardDrive(c) {
  return st.drives.find((d) => d.mounted && d.mount === c.mount) || { mount: c.mount, label: c.label, mounted: c.mounted };
}

function cardTile(c) {
  const busy = c.mount && [...st.tasks.values()].some((t) => t.state === "running"
    && commandKey(argvWords(t.argv)) === "card_check" && argvWords(t.argv)[2] === c.mount);
  const actions = [];
  if (c.mounted) {
    actions.push(runButton(busy ? "Checking…" : (c.checked_at ? "Check again" : "Check card"), `card_check:${c.mount}`,
      () => startTask("card_check", { mount: c.mount })));
    if (busy) actions[0].disabled = true;
    actions.push(button("Import…", () => openImport(cardDrive(c))));
  }
  if (c.volume_id) actions.push(button("Show files", () => { st.cardView = c.volume_id; st.cardFiles = { tier: "", copies: "", other: "", camera: "", offset: 0 }; loadCardDetail().catch(reportError); }));
  const when = c.mounted
    ? (c.checked_at ? `Checked ${relTime(c.checked_at)}` : "Never checked")
    : `Not connected${c.last_seen_at ? ` · last seen ${relTime(c.last_seen_at)}` : ""} · as last checked`;
  return el("article", { class: `card-tile t-${c.tier}${c.mounted ? "" : " gone"}` },
    el("div", { class: "card-tile-head" }, el("strong", null, c.label), tierPill(c.tier)),
    el("div", { class: "card-headline" }, cardHeadline(c)),
    el("div", { class: "muted small" }, `${plural(c.files, "file")} · ${fmtBytes(c.bytes)} · ${when}`),
    tierBar(c.counts, c.files),
    el("div", { class: "muted small" }, tierCounts(c.counts)),
    c.cameras.length ? el("div", { class: "cam-chips" }, c.cameras.map((m) => el("span", { class: `cam-chip t-${m.tier}`, title: TIER[m.tier].label },
      el("span", { class: "tier-dot", "aria-hidden": "true" }), `${m.name} · ${fmtInt(m.files)}`))) : null,
    actions.length ? el("div", { class: "card-actions" }, actions) : null);
}

function legend() {
  return el("div", { class: "card-legend muted small" }, TIER_ORDER.map((t) => el("span", { class: `t-${t}` },
    el("span", { class: "tier-dot", "aria-hidden": "true" }), `${TIER[t].label}: ${TIER_HINT[t].split(".")[0].toLowerCase()}`)));
}

function cameraTable(cameras) {
  if (!cameras.length) return el("p", { class: "muted small" }, "Connect a card to see which cameras its photos come from.");
  return el("div", { class: "table-wrap" }, el("table", { class: "file-table cam-table" },
    el("thead", null, el("tr", null, el("th", null, "Camera"), el("th", null, "Status"), el("th", { class: "num" }, "Files"),
      el("th", null, "Backed up so far"), el("th", null, "Breakdown"))),
    el("tbody", null, cameras.map((m) => el("tr", null,
      el("td", null, el("strong", null, m.name)),
      el("td", null, tierPill(m.tier)),
      el("td", { class: "num" }, fmtInt(m.files)),
      el("td", null, tierBar(m.counts, m.files)),
      el("td", { class: "small muted" }, tierCounts(m.counts)))))));
}

function renderCards(data) {
  const connected = data.cards.filter((c) => c.mounted);
  const remembered = data.cards.filter((c) => !c.mounted);
  $("#cards-view").replaceChildren(
    el("div", { class: "section-head" }, el("h2", null, "Cards"), button(backLabel(), closeCards)),
    el("p", { class: "muted small" }, "Which files on your cards are safe to remove. A card takes the colour of its least safe file."),
    legend(),
    connected.length
      ? el("div", { class: "card-tiles" }, connected.map(cardTile))
      : el("div", { class: "empty" }, el("h3", null, "No card connected"), el("p", null, "Plug in a camera card. It appears here.")),
    el("h3", { class: "sub" }, "Cameras on connected cards"),
    cameraTable(data.cameras),
    remembered.length
      ? el("details", { class: "remembered" }, el("summary", null, `Cards seen before (${fmtInt(remembered.length)})`),
        el("p", { class: "muted small" }, "As last checked. A card that was formatted since keeps its old record here."),
        el("div", { class: "card-tiles" }, remembered.map(cardTile)))
      : null);
}

async function loadCardDetail() {
  const id = st.cardView;
  if (!id) return;
  const params = new URLSearchParams({ limit: String(CARD_PAGE), offset: String(st.cardFiles.offset) });
  if (st.cardFiles.tier) params.set("tier", st.cardFiles.tier);
  if (st.cardFiles.camera) params.set("camera", st.cardFiles.camera);
  if (st.cardFiles.copies) params.set("copies", st.cardFiles.copies);
  if (st.cardFiles.other) params.set("other", st.cardFiles.other);
  const data = await api("GET", `/api/cards/${encodeURIComponent(id)}/files?${params}`);
  if (st.cardView !== id || !st.cardsOpen) return;
  renderCardDetail(data);
}

function setCardFilter(patch) {
  st.cardFiles = { ...st.cardFiles, ...patch, offset: 0 };
  loadCardDetail().catch(reportError);
}

function renderCardDetail(data) {
  const c = data.card;
  const files = data.files;
  const tiles = TIER_ORDER.map((t) => {
    const on = st.cardFiles.tier === t;
    return el("button", { type: "button", class: `stat t-${t}${on ? " on" : ""}`, "aria-pressed": on ? "true" : "false",
      onclick: () => setCardFilter({ tier: on ? "" : t }) },
    el("span", { class: "stat-label" }, TIER[t].label),
    el("span", { class: "stat-num" }, fmtInt(c.counts[t].files)),
    el("span", { class: "muted small" }, fmtBytes(c.counts[t].bytes)));
  });
  const copiesValue = st.cardFiles.other ? `on:${st.cardFiles.other}` : st.cardFiles.copies;
  const filterBar = fileFilterBar({
    show: { options: [["", "All files"]].concat(TIER_ORDER.map((t) => [t, TIER[t].label])), value: st.cardFiles.tier,
      onchange: (tier) => setCardFilter({ tier }) },
    copies: { options: copyFilterOptions("this card", c.copies_by_volume || []), value: copiesValue,
      onchange: (v) => setCardFilter(v.startsWith("on:") ? { copies: "", other: v.slice(3) } : { copies: v, other: "" }) },
    camera: { options: cameraFilterOptions(c.cameras), value: st.cardFiles.camera,
      onchange: (camera) => setCardFilter({ camera }) },
    shown: data.total, total: c.files,
  });
  const rows = files.map((f) => el("tr", null,
    el("td", null, tierPill(f.tier)),
    el("td", { class: "path" }, f.rel_path, el("div", { class: "reason" }, f.reason)),
    el("td", { class: "num" }, fmtBytes(f.size)),
    el("td", null, f.camera_name),
    el("td", null, copyCell({ ...f, hashed: f.tier !== "unchecked",
      copy_drives: f.copies }, c.volume_id, "this card"))));
  const pager = el("div", { class: "pager" },
    button("Previous", () => { st.cardFiles.offset = Math.max(0, st.cardFiles.offset - CARD_PAGE); loadCardDetail().catch(reportError); }),
    el("span", { class: "muted small" }, files.length
      ? `${fmtInt(st.cardFiles.offset + 1)}–${fmtInt(st.cardFiles.offset + files.length)} of ${fmtInt(data.total)}` : "No files"),
    button("Next", () => { st.cardFiles.offset += CARD_PAGE; loadCardDetail().catch(reportError); }));
  pager.querySelector("button:first-child").disabled = st.cardFiles.offset === 0;
  pager.querySelector("button:last-child").disabled = st.cardFiles.offset + files.length >= data.total;
  const actions = [button("All cards", () => { st.cardView = null; loadCards().catch(reportError); })];
  if (c.mounted) {
    actions.unshift(button("Import…", () => openImport(cardDrive(c))));
    actions.unshift(runButton(c.checked_at ? "Check again" : "Check card", `card_check:${c.mount}`,
      () => startTask("card_check", { mount: c.mount })));
  }
  $("#cards-view").replaceChildren(
    el("div", { class: "section-head" }, el("h2", null, c.label), tierPill(c.tier)),
    el("div", { class: "bulkbar" }, actions),
    el("p", { class: "card-headline" }, cardHeadline(c)),
    el("p", { class: "muted small" }, [cardHint(c),
      c.mounted ? (c.checked_at ? `Checked ${relTime(c.checked_at)}.` : "Never checked.") : "Not connected: as last checked."]
      .filter(Boolean).join(" ")),
    tierBar(c.counts, c.files),
    el("div", { class: "stats" }, tiles),
    el("h3", { class: "sub" }, "Cameras"),
    cameraTable(c.cameras),
    el("h3", { class: "sub" }, "Files, least safe first"),
    filterBar,
    rows.length
      ? el("div", { class: "table-wrap" }, el("table", { class: "file-table" },
        el("thead", null, el("tr", null, el("th", null, "Status"), el("th", null, "File"), el("th", { class: "num" }, "Size"),
          el("th", null, "Camera"), el("th", null, "Copies"))),
        el("tbody", null, rows)))
      : el("p", { class: "muted small" }, "No files in this view."),
    pager);
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
  for (const key of ["creator", "copyright", "default_offset", "match_window_s", "mac_folder"]) {
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
    mac_folder: form.elements.mac_folder.value,
  };
  try {
    const res = await api("POST", "/api/settings", body);
    st.settings = res.settings;
    $("#dlg-settings").close();
    toast("Settings saved.");
    refreshDrives(); // the Mac folder may have changed
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
  const runAfter = form.elements.run_after.checked;
  $("#dlg-assign").close();
  const taskId = await startTask("assign", args);
  if (taskId) openSetup(d, { taskId, role: args.role, label, runAfter });
}

async function ignoreAssignDrive() {
  const d = st.assignDrive;
  $("#dlg-assign").close();
  if (d) await setIgnored(d, true);
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
  $("#btn-cards").addEventListener("click", () => (st.cardsOpen ? closeCards() : openCards(null)));
  $("#btn-home").addEventListener("click", () => showBaseView(false));
  $("#btn-photos").addEventListener("click", () => showBaseView(true));
  $("#f-settings").addEventListener("submit", saveSettings);
  $("#f-assign").addEventListener("submit", assignDrive);
  $("#btn-assign-ignore").addEventListener("click", () => ignoreAssignDrive().catch(reportError));
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
  syncBase();
  renderHome();
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
