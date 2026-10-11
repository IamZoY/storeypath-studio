// Many people on one project at once, in Review (Studio's events: web/events.py,
// web/live.py). The page keeps one stream open on the floor it shows: what others
// change there comes as it is saved (Review refreshes it in place and says who did
// what), who is viewing the floor and who is editing which of its rooms (many people a
// floor, one a room: a person's change takes the rooms it touches; Done editing, leaving
// the floor, or no change to a room for a while lets it go; an admin may take one over),
// and the progress of jobs. Each person undoes and redoes their own
// changes (the buttons; ⌘Z and ⇧⌘Z / Ctrl+Z and Ctrl+Y are Review's keys: step()), and
// the History drawer lists who changed what on the floor, live. Review's top bar shows
// who is here (presence()), its status bar who is editing, its plan whose each room is (held.js).

import { ProjectStream } from "./stream.js";

const $ = (id) => document.getElementById(id);

/** This page, as Studio is told with each call (X-StoreyPath-Page): a change it made
 * comes back on the stream marked so, and is not refreshed nor told twice. */
export const PAGE = (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2)).replace(/[^A-Za-z0-9-]/g, "");

const live = {
  code: null, base: null, me: null, hooks: null,
  stream: null, floor: null, // the stream (stream.js), on the floor shown
  editing: [], // who is editing the floor shown: [{who: {id, name, username}, since, rooms: [IDs]}]
  held: new Map(), // whose each room of it is: room -> {who, since} (the person's own too)
  changedHere: false, // a change of the person's on the floor shown, from this page (theirs to let go)
  viewing: [], // who else has it open
  steps: { undo: null, redo: null },
  historyOpen: false, historyTimer: 0, stepsTimer: 0,
};

function el(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v === true ? "" : v);
  }
  e.append(...children.flat().filter((c) => c !== null && c !== undefined && c !== false));
  return e;
}

const isMe = (p) => Boolean(p && live.me && p.id === live.me.id);
const clock = (iso) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

/** A person's colour (a hue, 0–360): their mark's, and the rooms they are editing. */
export function hueOf(p) {
  let hue = 0;
  for (const c of p?.id || p?.name || p?.username || "?") hue = (hue * 31 + c.charCodeAt(0)) % 360;
  return hue;
}

/** A person as a small round mark with their initial (their name on hover). */
export function avatar(p, cls = "") {
  const name = p?.name || p?.username || "?";
  return el("span", { class: `avatar ${cls}`.trim(), title: name, style: `--hue:${hueOf(p)}`, "aria-label": name },
    name.slice(0, 1).toUpperCase());
}

// ---- the stream -----------------------------------------------------------------------

/** Start: ``hooks`` are Review's (request, toast, floor, editableByAccess, changed, elsewhere,
 * reload, showLock, status, presence). */
export function setupTogether({ code, base, me, hooks }) {
  Object.assign(live, { code, base, me, hooks });
  $("undo").addEventListener("click", () => step(false));
  $("redo").addEventListener("click", () => step(true));
  $("history-open").addEventListener("click", () => showHistory(!live.historyOpen));
  $("history-close").addEventListener("click", () => showHistory(false));
  // leaving the page: the floor let go (when it is ours), as leaving it for another does
  window.addEventListener("pagehide", () => leave(live.floor));
}

/** Who is on the floor shown: who else is editing which of its rooms (``editing``:
 * [{who, since, rooms}]), how many the person is editing (``mine``), who else is viewing it. */
export function presence() {
  return { editing: live.editing.filter((e) => !isMe(e.who)), mine: live.editing.find((e) => isMe(e.who))?.rooms.length || 0,
    viewing: live.viewing.slice(), me: live.me };
}

/** The floor's stream as it is now (the browser tests): 0 opening, 1 open, 2 closed (let go
 * while the page is hidden). */
export const streamState = () => live.stream?.source.readyState ?? null;

/** Who else is editing a room of the floor shown ({who, since}), or null: it is theirs to
 * change until they are done. */
export function heldByOther(room) {
  const h = room ? live.held.get(room) : null;
  return h && !isMe(h.who) ? h : null;
}

function heldFrom(editing) {
  live.editing = editing || [];
  live.held = new Map(live.editing.flatMap((e) => e.rooms.map((r) => [r, { who: e.who, since: e.since }])));
}

export const historyOpen = () => live.historyOpen;

/** The stream opened on the floor shown (the one before it closed, and the person's rooms
 * on it let go); ``editing``: who is editing which of its rooms, as the floor was read. */
export function onFloor(floor, editing) {
  if (live.floor && live.floor !== floor) {
    leave(live.floor);
    live.changedHere = false;
  }
  heldFrom(editing?.editors);
  live.viewing = [];
  if (live.floor !== floor || !live.stream) {
    live.stream?.close();
    live.floor = floor;
    const stream = (live.stream = new ProjectStream(live.code, floor, live.hooks.request));
    stream.reopened = () => live.hooks.reload(); // what changed while it was lost
    stream.on("change", heardChange).on("presence", heardPresence).on("deleted", () => {
      live.hooks.toast("This project was deleted", true);
      stream.close();
    });
  }
  render();
  refreshSteps();
  if (live.historyOpen) loadHistory();
}

function heardChange(c) {
  if (c.floor !== live.floor) { // another floor's, or the building's: of note to the 3D view
    live.hooks.elsewhere?.(c);
    return;
  }
  refreshSteps();
  if (live.historyOpen) loadHistorySoon();
  if (c.page === PAGE) { // made here: shown already
    live.changedHere = true;
    return;
  }
  live.hooks.changed(c, isMe(c.who) ? null : `${c.who.name} ${c.line}`);
}

function heardPresence(p) {
  if (p.floor !== live.floor) return;
  const was = JSON.stringify(live.editing);
  heldFrom(p.editing);
  live.viewing = p.viewing.filter((v) => !isMe(v));
  render();
  if (was !== JSON.stringify(live.editing)) live.hooks.showLock();
}

/** What a change refused told of a room someone else is editing (423): shown so. */
export function heardLocked(locked) {
  if (!locked?.room || locked.floor !== live.floor) return;
  const editing = live.editing.map((e) => ({ ...e, rooms: e.rooms.filter((r) => r !== locked.room) }));
  const theirs = editing.find((e) => e.who.id === locked.who.id);
  if (theirs) theirs.rooms.push(locked.room);
  else editing.push({ who: locked.who, since: locked.since, rooms: [locked.room] });
  heldFrom(editing.filter((e) => e.rooms.length));
  render();
  live.hooks.showLock();
}

/** The person's rooms on a floor let go: Done editing, or gone from it (keepalive: also as
 * the page closes). */
function leave(floor, { said = false } = {}) {
  const mine = live.changedHere || live.editing.some((e) => isMe(e.who));
  if (!floor || !mine || floor !== live.floor) return Promise.resolve();
  if (!said) heldFrom(live.editing.filter((e) => !isMe(e.who)));
  live.changedHere = false;
  return fetch(`/api/${live.base}/floors/${encodeURIComponent(floor)}/release`, {
    method: "POST", keepalive: true, body: "{}",
    headers: { "X-StoreyPath": "1", "Content-Type": "application/json", "X-StoreyPath-Page": PAGE },
  }).catch(() => {});
}

// ---- who is here, who is editing ---------------------------------------------------------

function render() {
  const editors = live.editing.filter((e) => !isMe(e.who));
  const others = live.viewing.filter((p) => !editors.some((e) => e.who.id === p.id));
  const mark = (p, what, cls = "") => {
    const a = avatar(p, cls);
    a.removeAttribute("title");
    a.dataset.tip = `${p.name || p.username} · ${what}`;
    a.setAttribute("aria-label", a.dataset.tip);
    return a;
  };
  const rooms = (n) => `${n} room${n === 1 ? "" : "s"}`;
  const shown = [...editors.map((e) => [e.who, `editing ${rooms(e.rooms.length)} since ${clock(e.since)}`, "editing"]),
    ...others.map((p) => [p, "viewing", ""])];
  $("presence").replaceChildren(...[
    ...shown.slice(0, 5).map(([p, what, cls]) => mark(p, what, cls)),
    shown.length > 5 ? el("span", { class: "more", "data-tip": shown.slice(5).map(([p]) => p.name || p.username).join(", ") }, `+${shown.length - 5}`) : null,
  ].filter(Boolean));
  live.hooks.presence?.();
}

/** The person done editing the floor: their rooms on it let go (their next change takes
 * the rooms it touches again). */
export async function doneEditing() {
  await leave(live.floor, { said: true });
  heldFrom(live.editing.filter((e) => !isMe(e.who)));
  render();
  live.hooks.showLock();
}

/** A room someone else is editing taken over by an admin (``room``), or every room others
 * are editing on the floor: theirs no longer, and they are told so. */
export async function takeOver(room = null) {
  if (typeof room !== "string") room = null; // (a button's click)
  const others =live.editing.filter((e) => !isMe(e.who) && (!room || e.rooms.includes(room)));
  if (!others.length) return;
  const who = others.map((e) => e.who.name).join(", ");
  const what = room ? "this room" : others.length === 1 && others[0].rooms.length === 1 ? "the room they are editing"
    : "the rooms they are editing on this floor";
  if (!confirm(`Take over ${what} from ${who}? They can no longer save changes to it, and are told so.`)) return;
  try {
    await live.hooks.request(`${live.base}/floors/${encodeURIComponent(live.floor)}/take-over`, room ? { room } : {});
    const taken = new Set(room ? [room] : others.flatMap((e) => e.rooms));
    const editing = live.editing.map((e) => ({ ...e, rooms: e.rooms.filter((r) => !taken.has(r)) })).filter((e) => e.rooms.length);
    const me = editing.find((e) => isMe(e.who));
    if (me) me.rooms.push(...taken);
    else editing.push({ who: { id: live.me.id, name: live.me.name || live.me.username }, since: new Date().toISOString(), rooms: [...taken] });
    heldFrom(editing);
    render();
    live.hooks.showLock();
    live.hooks.toast(`You are editing ${what === "this room" ? "the room" : "them"} now (taken over from ${who})`);
  } catch (e) {
    live.hooks.toast(`Not taken over: ${e.message}`, true);
  }
}

// ---- jobs, followed on the stream -----------------------------------------------------------

/** A job followed to its end (its latest line said with ``say``; null once it ended): on
 * the floor's stream. Resolves with the job; throws when it failed. */
export async function followJob(job, say) {
  const follow = live.stream ? live.stream.follow(job, (log) => say(log.at(-1))) : Promise.resolve(job);
  job = await follow;
  say(null);
  if (!job) throw new Error("the page went to another floor");
  if (job.state === "failed") throw new Error(job.error || "failed");
  return job;
}

// ---- undo and redo: each person their own ------------------------------------------------

function refreshSteps() {
  clearTimeout(live.stepsTimer);
  live.stepsTimer = setTimeout(async () => {
    const floor = live.floor;
    try {
      const h = await live.hooks.request(`${live.base}/history?floor=${encodeURIComponent(floor)}&n=0`);
      if (floor !== live.floor) return;
      live.steps = { undo: h.undo, redo: h.redo };
    } catch {
      live.steps = { undo: null, redo: null };
    }
    showSteps();
  }, 120);
}

function showSteps() {
  const { undo, redo } = live.steps;
  $("undo").disabled = !undo;
  $("redo").disabled = !redo;
  $("undo").dataset.tip = undo ? `Undo: ${undo.line}` : "Nothing of yours to undo on this floor";
  $("redo").dataset.tip = redo ? `Redo: ${redo.line.replace(/^undid: /, "")}` : "Nothing of yours to redo on this floor";
  live.hooks.steps?.(live.steps);
}

/** What the person would undo and redo now ({undo, redo}: {line} or null). */
export const steps = () => live.steps;

let stepping = false;

/** The person's latest change on the floor undone (``redo``: their latest undoing redone). */
export async function step(redo) {
  if (stepping || !live.floor) return;
  if (!live.hooks.editableByAccess()) return live.hooks.toast("View only: you may look at this floor, not change it", true);
  stepping = true;
  try {
    const done = await live.hooks.request(`${live.base}/${redo ? "redo" : "undo"}`, { floor: live.floor });
    const line = redo ? done.line.replace(/^undid: /, "") : done.line;
    live.hooks.toast(`${redo ? "Redone" : "Undone"}: ${line}`);
    if (done.job) {
      live.hooks.status("Reading the floor again…");
      await followJob(done.job, (s) => live.hooks.status(s ?? ""));
    }
    await live.hooks.reload();
  } catch (e) {
    live.hooks.toast(`Not ${redo ? "redone" : "undone"}: ${e.message}`, true);
  } finally {
    stepping = false;
    refreshSteps();
  }
}

// ---- the History panel -----------------------------------------------------------------------

/** The History drawer open (or closed). */
export function showHistory(on) {
  live.historyOpen = on;
  $("history").hidden = !on;
  $("history-open").setAttribute("aria-pressed", String(on));
  $("history-open").classList.toggle("active", on);
  if (on) {
    loadHistory();
    $("history-close").focus({ preventScroll: true });
  }
}

function loadHistorySoon() {
  clearTimeout(live.historyTimer);
  live.historyTimer = setTimeout(loadHistory, 250);
}

async function loadHistory() {
  const floor = live.floor;
  if (!floor) return;
  let h;
  try {
    h = await live.hooks.request(`${live.base}/history?floor=${encodeURIComponent(floor)}&n=100`);
  } catch (e) {
    $("history-list").replaceChildren(el("li", { class: "meta" }, `Not shown: ${e.message}`));
    return;
  }
  if (floor !== live.floor) return;
  live.steps = { undo: h.undo, redo: h.redo };
  showSteps();
  $("history-floor").textContent = live.hooks.floorName();
  $("history-list").replaceChildren(...(h.entries.length ? h.entries.map((e) => el("li", {
    class: [e.undone ? "undone" : "", e.mine ? "mine" : "", h.undo?.seq === e.seq ? "next-undo" : ""].join(" ").trim(),
    title: new Date(e.at).toLocaleString() },
  avatar(e.who),
  el("div", { class: "what" },
    el("div", {}, el("strong", {}, e.mine ? "You" : e.who.name), " ", e.line),
    el("div", { class: "meta" }, when(e.at), e.undone ? el("span", { class: "tag" }, "undone") : null,
      h.undo?.seq === e.seq ? el("span", { class: "tag next" }, "⌘Z undoes this") : null)))) :
    [el("li", { class: "meta" }, "No changes on this floor yet.")]));
}

function when(iso) {
  const t = new Date(iso), now = new Date();
  const s = (now - t) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  return t.toDateString() === now.toDateString() ? clock(iso) : t.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}
