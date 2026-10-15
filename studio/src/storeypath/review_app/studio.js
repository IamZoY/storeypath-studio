// StoreyPath Studio: projects, drawings, plans, floors, placement and export.
// Everything is served and computed locally (see server.py); nothing is fetched
// from the internet. Each person sees the projects shared with them, and is offered
// only what their level lets them do (``can``: view, edit or share on the project,
// each building and each floor); the server checks everything anyway.

import { accountMenu, sentAway, whoami } from "./account.js";
import { setCrumbs, setupChrome } from "./chrome.js";
import { helpersPage } from "./helpers.js";
import { itemTypesPage } from "./itemtypes.js";
import { downloadSealed, putFile } from "./seal.js";
import { openShare } from "./share.js";
import { ProjectStream } from "./stream.js";
import { toast } from "./toast.js";
import { avatar } from "./together.js";
import { usersPage } from "./users.js";
import { lengthSaid, onUnits, roundStep, unitsSwitch } from "./units.js";

const SVG_NS = "http://www.w3.org/2000/svg";
const $ = (id) => document.getElementById(id);
const KIND = {
  floor_plan: "Floor plan", roof_plan: "Roof plan", site_plan: "Site plan", elevation: "Elevation",
  section: "Section", detail: "Detail", schedule: "Schedule", other: "Other",
};
const UNITS = { mm: "millimetres (mm)", cm: "centimetres (cm)", m: "metres (m)", in: "inches (in)", ft: "feet (ft)" };
const FLOOR_NAMES = { "-2": "Second basement", "-1": "Basement", 0: "Ground floor", 1: "First floor",
  2: "Second floor", 3: "Third floor", 4: "Fourth floor", 5: "Fifth floor" };

// ---- helpers -----------------------------------------------------------------

async function api(path, body, { method, raw } = {}) {
  const init = {};
  if (raw !== undefined) {
    init.method = "PUT";
    init.headers = { "X-StoreyPath": "1", "Content-Type": "application/octet-stream" };
    init.body = raw;
  } else if (body !== undefined) {
    init.method = method || "POST";
    // the header a page of another site cannot send (server.py refuses changes without it)
    init.headers = { "X-StoreyPath": "1", "Content-Type": "application/json" };
    init.body = JSON.stringify(body);
  }
  const res = await fetch(`/api/${path}`, init);
  const data = await res.json().catch(() => ({}));
  if (sentAway(res, data)) throw Object.assign(new Error("log in again"), { status: res.status, data });
  if (!res.ok) throw Object.assign(new Error(data.error || `${res.status} ${res.statusText}`), { status: res.status, data });
  return data;
}

// ---- who may do what ----------------------------------------------------------------

let me = null; // the person logged in (GET /api/me)
const RANK = { view: 1, edit: 2, share: 3 };
const atLeast = (level, need) => (RANK[level] || 0) >= RANK[need];
/** The person's level on the project as a whole, on a building, on a floor (p.can). */
const onProject = (p, need) => atLeast(p.can?.project, need);
const onBuilding = (p, id, need) => atLeast(p.can?.buildings?.[id] ?? p.can?.project, need);
const onFloor = (p, id, need) => atLeast(p.can?.floors?.[id], need);
const shareButton = (code, label = "Share…", after = () => route()) =>
  el("button", { type: "button", title: "Who may see and change this project, and how far", onclick: (e) => {
    e.preventDefault();
    e.stopPropagation();
    openShare(code, me, after);
  } }, label);

function el(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k === "value") e.value = v;
    else if (k === "checked") e.checked = v;
    else e.setAttribute(k, v === true ? "" : v);
  }
  e.append(...children.flat().filter((c) => c !== null && c !== undefined && c !== false));
  return e;
}

/** The page shown: its parts, those left out (null) not shown. */
const showPage = (...parts) => $("page").replaceChildren(...parts.filter(Boolean));

// the jobs this page follows now (runJob): a page opened again follows the others (resume)
const following = new Set();

/** Run a server job (``started``: the job, or what answers with it), showing its progress
 * (as the project's stream tells it); resolves with its result. */
async function runJob(started) {
  const panel = $("job");
  panel.hidden = false;
  panel.className = "job";
  let job = await started;
  following.add(job.id);
  const stop = $("job-stop");
  stop.hidden = job.kind !== "read"; // (a read of drawings stops where it says how far it has got)
  stop.disabled = Boolean(job.stopping);
  stop.textContent = job.stopping ? "Stopping…" : "Stop";
  stop.onclick = async () => {
    stop.disabled = true;
    stop.textContent = "Stopping…";
    try {
      await api(`jobs/${job.id}/stop`, {});
    } catch (e) {
      stop.disabled = false;
      stop.textContent = "Stop";
      toast(`Could not stop it: ${e.message}`, true);
    }
  };
  try {
    $("job-title").textContent = job.title;
    const say = (log) => $("job-log").replaceChildren(...log.slice(-12).map((l) => el("li", {}, l)));
    if (live.stream) job = await live.stream.follow(job, say);
    while (job.state === "waiting" || job.state === "running") { // (no stream: asked for)
      say(job.log);
      await new Promise((r) => setTimeout(r, 600));
      job = await api(`jobs/${job.id}`);
    }
    say(job.log);
  } finally {
    following.delete(job.id);
    stop.hidden = true;
  }
  panel.classList.add(job.state);
  setTimeout(() => (panel.hidden = true), job.state === "failed" ? 15000 : 4000);
  if (job.state === "failed") throw Object.assign(new Error(job.error || "failed"), { stopped: Boolean(job.stopped) });
  return job.result;
}

// ---- status -----------------------------------------------------------------

let planPage = false; // the 2D plan page is there (Studio's status says)

async function showStatus() {
  try {
    const s = await api("status");
    planPage = Boolean(s.plan);
    $("status").replaceChildren(
      el("span", { class: `chip ${s.model ? (s.model_ready ? "ok" : "") : "off"}`, title: "Reads room names, sheet titles and layer names" },
        s.model ? `Model: ${s.model}${s.model_ready ? "" : " (loading…)"}` : "No language model"),
      el("span", { class: `chip ${s.dwg ? "ok" : "off"}` }, s.dwg ? "Reads DWG and DXF" : "DXF only (no DWG reader)"),
      el("span", { class: "chip ok", title: "Nothing is sent anywhere" }, "Runs offline"),
      el("span", { class: "chip" }, `v${s.version}`),
    );
  } catch {
    $("status").replaceChildren(el("span", { class: "chip off" }, "Server not reachable"));
    return;
  }
}
setInterval(showStatus, 10000);

// ---- pages ------------------------------------------------------------------

async function route() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  watchProject(parts[0] === "p" && parts[1] ? decodeURIComponent(parts[1]) : null);
  try {
    if (parts[0] === "p" && parts[1]) await projectPage(decodeURIComponent(parts[1]));
    else if (parts[0] === "users" && me?.role === "admin") {
      document.title = "Users · StoreyPath Studio";
      setCrumbs([{ label: "Projects", href: "#/" }, { label: "Users" }]);
      await usersPage($("page"), me, toast);
    } else if (parts[0] === "item-types") {
      document.title = "Item types · StoreyPath Studio";
      setCrumbs([{ label: "Projects", href: "#/" }, { label: "Item types" }]);
      await itemTypesPage($("page"), me, toast);
    } else if (parts[0] === "helpers" && me?.role === "admin") {
      document.title = "GPU helpers · StoreyPath Studio";
      setCrumbs([{ label: "Projects", href: "#/" }, { label: "GPU helpers" }]);
      await helpersPage($("page"), toast);
    } else await projectsPage();
  } catch (e) {
    $("page").replaceChildren(el("div", { class: "card" }, el("p", {}, e.message), el("a", { href: "#/" }, "All projects")));
  }
  window.scrollTo(0, 0);
}

async function projectsPage() {
  const projects = await api("projects");
  const name = el("input", { type: "text", placeholder: "Project name, e.g. Head office", required: true });
  const form = el("form", { class: "row", onsubmit: async (e) => {
    e.preventDefault();
    try {
      const { code } = await api("projects", { name: name.value });
      location.hash = `#/p/${code}`;
    } catch (err) {
      toast(err.message, true);
    }
  } }, name, el("button", { class: "primary", type: "submit" }, "New project"));

  document.title = "StoreyPath Studio";
  setCrumbs([{ label: "Projects" }]);
  const creates = !me || me.create; // admins and engineers make projects, and open files as new ones
  const whose = (p) => p.can && !p.can.project ? "Parts shared with you"
    : p.can?.owner ? "Yours" : p.can && !p.can.admin ? `Shared with you: ${p.can.project}` : null;
  showPage(
    el("section", { class: "row page-head" },
      el("div", { class: "grow" },
        el("h1", {}, "Projects"),
        el("p", { class: "lead" }, creates
          ? "A project holds the drawings of one site or campus and every object ID issued for it. Create one, add its drawings, and Studio finds the plans, floors and rooms."
          : "The projects shared with you, and how far: some whole, some a building or a floor of them.")),
      el("a", { class: "button", href: "#/item-types", title: "The furniture and equipment people place on floors, for every project" },
        "Item types")),
    creates ? el("section", { class: "card" }, form) : null,
    creates ? openCard() : null,
    projects.length
      ? el("section", { class: "projects" }, projects.map((p) =>
          el("div", { class: "card project-card" },
            el("a", { class: "card-link", href: `#/p/${p.code}`, "aria-label": p.name }),
            el("h3", {}, p.name),
            el("code", { class: "muted small" }, p.code),
            el("div", { class: "stat" }, el("b", {}, String(p.floors)), " floors · ", el("b", {}, String(p.spaces)), " spaces",
              p.review ? el("span", { class: "warn" }, ` · ${p.review} to review`) : null),
            whose(p) || p.can?.share ? el("div", { class: "card-actions" },
              whose(p) ? el("span", { class: "owner-line grow" }, whose(p)) : el("span", { class: "grow" }),
              p.can?.share ? shareButton(p.code) : null) : null)))
      : el("p", { class: "empty" }, creates ? "No projects yet." : "Nothing is shared with you yet: an admin, or a project's owner, shares projects with you."),
  );
}

// ---- who is on which floor (the project's stream: stream.js) ---------------------------------

const live = { code: null, stream: null, floors: new Map() }; // floor id → {viewing, editing}

/** The project page's stream open on ``code`` (null: none, another page is shown). */
function watchProject(code) {
  if (live.code === code) return;
  live.stream?.close();
  Object.assign(live, { code, stream: null, floors: new Map() });
  if (!code) return;
  live.stream = new ProjectStream(code, null, api).on("presence", (p) => {
    live.floors.set(p.floor, p);
    showWhoIsOn();
  });
  live.stream.reopened = () => { // who is on which floor: told again as it opens
    live.floors.clear();
    showWhoIsOn();
  };
}

/** By each floor of the page: who is on it (small marks), and who is editing its rooms. */
function showWhoIsOn() {
  for (const spot of document.querySelectorAll("[data-on-floor]")) {
    const p = live.floors.get(spot.dataset.onFloor);
    const editors = (p?.editing || []).map((e) => e.who);
    const others = (p?.viewing || []).filter((v) => !editors.some((e) => e.id === v.id));
    const rooms = (n) => `${n} room${n === 1 ? "" : "s"}`;
    const said = [...(p?.editing || []).map((e) => `${e.who.name} is editing ${rooms(e.rooms.length)}`),
      others.length ? `${others.map((v) => v.name).join(", ")} ${others.length === 1 ? "is" : "are"} looking at it` : null]
      .filter(Boolean).join("; ");
    spot.title = said;
    const first = (e) => e.name.split(" ")[0];
    spot.replaceChildren(...[...editors.slice(0, 3).map((e) => avatar(e, "editing")),
      ...others.slice(0, Math.max(0, 4 - editors.length)).map((v) => avatar(v)),
      editors.length ? el("span", { class: "muted small" },
        ` ${editors.slice(0, 2).map(first).join(", ")}${editors.length > 2 ? ` +${editors.length - 2}` : ""} editing`) : null]
      .filter(Boolean));
  }
}

async function projectPage(code) {
  const p = await api(`projects/${code}`);
  document.title = `${p.project.name} · StoreyPath Studio`;
  setCrumbs([{ label: "Projects", href: "#/" }, { label: p.project.name }]);
  const plansArea = el("div", { id: "plans-area" });

  const converted = p.locations.some((l) => l.buildings.some((b) => b.floors.some((f) => f.converted)));
  const level = p.can?.project;
  showPage(
    el("section", {},
      el("a", { class: "back", href: "#/" }, "← Projects"),
      el("div", { class: "row" },
        el("div", { class: "grow" },
          el("h1", {}, p.project.name),
          el("p", { class: "lead" }, el("code", {}, p.project.id), ` · ${p.file}`,
            p.can && !p.can.admin ? el("span", { class: "muted" }, level
              ? ` · ${p.can.owner ? "yours" : `you may ${level === "view" ? "view" : level === "edit" ? "change" : "change and share"} it`}`
              : " · the parts of it shared with you") : null)),
        p.can?.share ? shareButton(code, "Share…", () => projectPage(code)) : null,
        converted ? el("a", { class: "button primary", href: worldUrl(code), target: "_blank",
          title: "The project as it is now, in 3D: orbit it as a dollhouse or walk through it" }, "Walk in 3D") : null)),
    onProject(p, "edit") ? drawingsCard(code, p, plansArea) : null,
    plansArea,
    ...p.locations.filter((loc) => loc.buildings.some((b) => b.footprint)).map((loc) => siteCard(code, loc, p)),
    buildingsCard(code, p),
    exportCard(code, p),
    p.can?.delete !== false ? deleteCard(code, p) : null,
  );
  showWhoIsOn();
  resume(code, p, plansArea);
}

// ---- work in progress, when the page is opened again ---------------------------------
// Studio's jobs run on the server whatever the page does: a page opened again (a refresh,
// another tab, back from elsewhere) asks what is going on in the project and picks it up.

let resumed = 0; // which page opened resume() last (a page shown again: the earlier one stops)

/** What was going on in the project when its page was opened: the floors being read
 * (marked, and not read a second time), the drawings sent that wait for the person to
 * choose what to keep, the plans they found a moment ago (shown again, to add as
 * floors), and the jobs still to run or running, shown and followed to their end. */
async function resume(code, p, plansArea) {
  const mine = ++resumed;
  let jobs = [], incoming = [];
  try {
    ({ jobs } = await api(`projects/${code}/jobs`));
    if (onProject(p, "edit")) ({ incoming } = await api(`projects/${code}/incoming`));
  } catch {
    return; // (an older Studio, or gone: nothing to pick up)
  }
  if (mine !== resumed || live.code !== code) return;
  const going = jobs.filter((j) => j.state === "waiting" || j.state === "running");
  markReading(going);
  showWaiting(code, incoming);
  // the person's latest plans found (no floors added from that drawing since), shown again
  const ours = (j) => !j.by || !me || j.by.id === me.id;
  const plans = jobs.find((j) => j.kind === "plans" && j.state === "done" && ours(j));
  const addedSince = plans && jobs.some((j) => j.kind === "floors" && j.about === plans.about && j.submitted > plans.submitted);
  if (plans && !addedSince && !plansClosed().includes(plans.id) && findingPlans === null && !plansArea.childElementCount) {
    showPlans(code, plans.about, plansArea, plans.result, plans.ended, plans.id);
  }
  const left = going.filter((j) => !following.has(j.id)).reverse(); // the oldest first, as they run
  if (!left.length) return;
  for (const job of left) {
    try {
      await runJob(job);
    } catch (e) {
      toast(`${job.title}: ${e.message}`, true);
    }
    if (mine !== resumed || live.code !== code) return; // another page is shown now
  }
  await projectPage(code); // what they changed
}

/** The floors the jobs still going read: marked "Reading…", their Read button held. */
function markReading(going) {
  const reading = new Set(going.flatMap((j) => j.floors || []));
  for (const row of document.querySelectorAll("tr[data-floor]")) {
    const on = reading.has(row.dataset.floor);
    row.classList.toggle("reading", on);
    const cell = row.querySelector("td.actions");
    cell?.querySelector(".badge.reading")?.remove();
    for (const b of cell?.querySelectorAll("button") || []) b.disabled = on;
    if (on) cell?.prepend(el("span", { class: "badge reading", title: "Its drawing is being read: a job of Studio's" }, "Reading…"));
  }
}

/** The drawings the person sent that wait for them to choose what to keep. */
function showWaiting(code, incoming) {
  const box = $("waiting");
  if (!box) return;
  box.replaceChildren(...(incoming.length ? [el("h3", {}, "Waiting for you"),
    el("ul", { class: "drawings" }, incoming.map((w) => el("li", {},
      el("span", { class: "grow" }, el("code", {}, w.name),
        el("span", { class: "muted small" }, ` · ${w.found.length} private thing${w.found.length === 1 ? "" : "s"} found: choose what to keep`)),
      el("button", { type: "button", class: "primary", onclick: () => decide(code, w) }, "Choose what to keep…"),
      el("button", { type: "button", onclick: () => decide(code, w, null) }, "Don't add it"))))] : []));
}

/** A drawing that waits: what to keep chosen (the same dialog as when it was sent), or not
 * added (``keep`` null); the page shown again. */
async function decide(code, w, keep = undefined) {
  if (keep === undefined) keep = await choosePrivate(w.name, w);
  try {
    if (keep === null) {
      await api(`projects/${code}/incoming/${w.pending}/cancel`, {});
      toast(`${w.name}: not added`);
    } else {
      const result = await runJob(api(`projects/${code}/incoming/${w.pending}`, { keep }));
      toast(`${w.name} → ${result.drawing}: ${result.privacy.summary}. Its Words list shows everything left in it.`, false, 12000);
    }
  } catch (e) {
    toast(`${w.name}: ${e.message}`, true);
  }
  await projectPage(code);
}

/** Deleting a project: everything in it goes, once its name is typed. */
function deleteCard(code, p) {
  const remove = async () => {
    const typed = prompt(`Delete ${p.project.name} and everything in it: its drawings, floors, corrections and exports? ` +
      "This cannot be undone. Type the project's name to delete it:");
    if (typed === null) return;
    try {
      await api(`projects/${code}/delete`, { confirm: typed });
      toast(`${p.project.name} deleted`);
      location.hash = "#/";
    } catch (e) {
      toast(e.message, true);
    }
  };
  return el("section", { class: "card danger-zone" },
    el("h2", {}, "Delete project"),
    el("p", { class: "muted small" }, "Its drawings, floors, corrections and exports all go. This cannot be undone."),
    el("button", { type: "button", class: "danger", onclick: remove }, "Delete project…"));
}

// ---- drawings and plans ----------------------------------------------------------

// Files being sent: until each is (Studio has it, and works on it whatever the page does),
// leaving the page or reloading it is warned of, as it would lose the file.
let sendingNow = 0;
window.addEventListener("beforeunload", (e) => {
  if (!sendingNow) return;
  e.preventDefault();
  e.returnValue = "A drawing is still being sent: leave, and it is not added.";
});

/** ``sent`` (a file being sent), counted until it is in. */
function sending(sent) {
  sendingNow++;
  sent.then(() => sendingNow--, () => sendingNow--);
  return sent;
}

function drawingsCard(code, p, plansArea) {
  const input = el("input", { type: "file", accept: ".dwg,.dxf", hidden: true, multiple: true });
  const drop = el("label", { class: "drop" }, input,
    el("strong", {}, "Add drawings"), el("br"), "Drop DWG or DXF files here, or click to choose them.");
  // On by default: the project keeps only a copy without the title blocks (client,
  // owner, consultant, who drew it), names, contacts, logos and hidden file data.
  const keepPrivate = el("input", { type: "checkbox", checked: true });
  const privacy = el("label", { class: "check",
    title: "Removes each sheet's title block (client, owner, consultant, project, plot and permit numbers, who drew and checked it, stamps, logos), names, phone numbers and emails elsewhere, and the file's hidden data. Only the cleaned copy is kept, named drawing-1.dxf, drawing-2.dxf…; the file you add and its name are not." },
    keepPrivate, "Remove private information (title blocks, owner, names, contacts) as drawings are added");
  const upload = async (files) => {
    const cleaned = keepPrivate.checked;
    const added = [];
    for (const file of files) {
      try {
        toast(`Uploading ${file.name}…`);
        const sent = sending(api(`projects/${code}/drawings/${encodeURIComponent(file.name)}${cleaned ? "" : "?private=0"}`,
          undefined, { raw: file }));
        if (cleaned) {
          let result = await runJob(sent);
          if (result.pending) { // a person says what of it to keep
            const keep = await choosePrivate(file.name, result);
            if (keep === null) {
              await api(`projects/${code}/incoming/${result.pending}/cancel`, {});
              toast(`${file.name}: not added`);
              continue;
            }
            result = await runJob(api(`projects/${code}/incoming/${result.pending}`, { keep }));
          }
          added.push(result.drawing);
          toast(`${file.name} → ${result.drawing}: ${result.privacy.summary}. Its Words list shows everything left in it.`, false, 12000);
        } else {
          added.push((await sent).drawing);
        }
      } catch (e) {
        toast(`${file.name}: ${e.message}`, true);
        break;
      }
    }
    await projectPage(code);
    if (files.length === 1 && added.length === 1) findPlans(code, added[0], $("plans-area"));
  };
  input.addEventListener("change", () => upload([...input.files]));
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); upload([...e.dataTransfer.files]); });

  return el("section", { class: "card" },
    el("h2", {}, "Drawings"),
    drop,
    privacy,
    el("div", { id: "waiting" }), // (resume: the drawings sent that wait for the person to choose what to keep)
    p.drawings.length ? el("ul", { class: "drawings" }, p.drawings.map((d) =>
      el("li", {}, el("code", { class: "grow" }, d),
        el("a", { class: "button", href: `/api/projects/${encodeURIComponent(code)}/drawings/${encodeURIComponent(d)}/words`,
          target: "_blank", title: "Every word and string left in this drawing, to look through for anything private left behind" },
          "Words"),
        el("button", { type: "button", "data-drawing": d, disabled: findingPlans !== null,
          onclick: () => findPlans(code, d, plansArea) }, findingPlans === d ? "Finding plans…" : "Find plans")))) : null,
  );
}

// What a drawing holds that is private, found as it is added: each goes unless it is
// unticked (a child's name on their room, kept on purpose). Resolves with the ids
// kept, or null to not add the drawing at all.
const PRIVATE_GROUPS = [
  ["title block", "Title blocks", "the client, owner, consultant, who drew it, stamps"],
  ["name", "Names with a title", "Mr, Dr, Eng, Sheikh, السيد …: the name goes, the rest of the text stays"],
  ["contact", "Contacts and numbers", "phone, email, web, P.O. box, permit, plot and licence numbers"],
  ["attribute", "Block attributes", "values filled in on blocks"],
  ["person", "People, read by the language model", "the name goes, the rest of the text stays"],
  ["company", "Companies, read by the language model", ""],
  ["address", "Addresses and places, read by the language model", ""],
  ["id number", "Numbers and dates, read by the language model", ""],
  ["other", "Other, read by the language model", ""],
  ["images", "Images", ""],
  ["sheets", "Paper sheets", ""],
  ["file data", "Hidden file data", "who saved it, printers, properties"],
];

function choosePrivate(fileName, found) {
  return new Promise((resolve) => {
    const boxes = [];
    const groups = PRIVATE_GROUPS.map(([kind, title, about]) => {
      const items = found.found.filter((f) => f.kind === kind);
      if (!items.length) return null;
      return el("section", { class: "private-group" },
        el("h3", {}, title, about ? el("span", { class: "muted small" }, ` · ${about}`) : null),
        el("ul", {}, items.map((f) => {
          const box = el("input", { type: "checkbox", checked: true });
          box.dataset.id = f.id;
          boxes.push(box);
          const parts = f.kind === "title block" ? f.detail.join(" · ")
            : f.detail.length ? `takes out: ${f.detail.join(", ")}` : "";
          return el("li", {}, el("label", {}, box,
            el("span", { class: "grow" }, el("span", {}, f.label), parts ? el("span", { class: "muted small detail" }, parts) : null),
            f.count > 1 ? el("span", { class: "muted small" }, `×${f.count}`) : null));
        })));
    }).filter(Boolean);
    const dialog = el("dialog", { class: "private-review" });
    const close = (keep) => {
      dialog.close();
      dialog.remove();
      resolve(keep);
    };
    const all = (on) => boxes.forEach((b) => (b.checked = on));
    dialog.append(
      el("h2", {}, `Private information in ${fileName}`),
      el("p", { class: "muted" }, "Ticked things are taken out before the drawing is kept; untick what you want to keep ",
        "(a child's name on their room, say). Nothing is kept until you choose."),
      el("div", { class: "row" },
        el("button", { type: "button", onclick: () => all(true) }, "Tick all"),
        el("button", { type: "button", onclick: () => all(false) }, "Untick all"),
        el("span", { class: "muted small grow" }, found.reader ? `Texts read by ${found.reader} as well as by rule.`
          : "No language model running: found by rule only.")),
      el("div", { class: "private-list" }, groups),
      el("div", { class: "row end" },
        el("button", { type: "button", onclick: () => close(null) }, "Don't add it"),
        el("button", { type: "button", class: "primary",
          onclick: () => close(boxes.filter((b) => !b.checked).map((b) => b.dataset.id)) },
          "Take out the ticked and add it")));
    dialog.addEventListener("cancel", (e) => { e.preventDefault(); close(null); });
    document.body.append(dialog);
    dialog.showModal();
  });
}

// The drawing whose plans are being found: one at a time (an upload starts it), and
// its button says so.
let findingPlans = null;

// Plans found and then cancelled (their job's ID): not shown again when the page is opened
// again (in this browser; Find plans finds them again)
const PLANS_CLOSED = "storeypath.plans-closed";
function plansClosed() {
  try {
    return JSON.parse(localStorage.getItem(PLANS_CLOSED) || "[]");
  } catch {
    return [];
  }
}
function closePlans(jobId) {
  try {
    localStorage.setItem(PLANS_CLOSED, JSON.stringify([jobId, ...plansClosed()].slice(0, 20)));
  } catch {
    // (shown again when the page is opened again)
  }
}

function showFindingPlans() {
  for (const b of document.querySelectorAll("button[data-drawing]")) {
    b.disabled = findingPlans !== null;
    b.textContent = findingPlans === b.dataset.drawing ? "Finding plans…" : "Find plans";
  }
}

/** Find the plans in a drawing, read in ``units`` (or the units it shows), and offer them as floors. */
async function findPlans(code, drawing, area, units) {
  if (findingPlans !== null) return;
  findingPlans = drawing;
  showFindingPlans();
  // Earlier results go: no floors are added from plans being found again.
  area.replaceChildren(el("section", { class: "card" }, el("h2", {}, `Plans in ${drawing}`),
    el("p", { class: "muted small" }, units ? `Finding the plans again, in ${UNITS[units]}…` : "Finding the plans…")));
  let result, jobId = null;
  try {
    const started = api(`projects/${code}/drawings/${encodeURIComponent(drawing)}/plans`, units ? { units } : {});
    result = await runJob(started);
    jobId = (await started).id;
  } catch (e) {
    toast(e.message, true);
    area.replaceChildren();
    return;
  } finally {
    findingPlans = null;
    showFindingPlans();
  }
  await showPlans(code, drawing, area, result, null, jobId);
}

/** The plans found in a drawing (``result``: what Find plans gave; ``found``: when, for
 * plans found a while ago and shown again; ``jobId``: the job that found them), offered as
 * floors; Cancel puts them away, adding none. */
async function showPlans(code, drawing, area, result, found = null, jobId = null) {
  let project;
  try {
    project = await api(`projects/${code}`); // its locations, buildings and floors, to add to
  } catch (e) {
    toast(e.message, true);
    return;
  }
  const floorsKnown = result.plans.filter((x) => x.kind === "floor_plan" && x.floor !== null).map((x) => x.floor);
  const top = floorsKnown.length ? Math.max(...floorsKnown) : 0;
  const cards = result.plans.map((plan) => planCard(plan, top, result.levels, project));
  // One plan per floor of a building to start with, the largest: a second "ground
  // floor plan" on a sheet is often an outbuilding's.
  const largest = new Map();
  for (const c of cards.filter((c) => c.chosen())) {
    const { key } = c.place();
    if (!largest.has(key) || c.area > largest.get(key).area) largest.set(key, c);
  }
  cards.forEach((c) => { if (c.chosen() && largest.get(c.place().key) !== c) c.choose(false); });
  // A drawing of one floor whose plan does not say which (one file per floor is
  // common): the building's next floor.
  const floorPlans = cards.filter((c) => c.plan.kind === "floor_plan");
  if (floorPlans.length === 1 && floorPlans[0].plan.floor === null) floorPlans[0].nextFloor();
  // The plans are found at the drawing's scale, so other units mean finding them again.
  const unitChoice = el("select", { onchange: () => findPlans(code, drawing, area, unitChoice.value) },
    Object.entries(UNITS).map(([u, label]) => el("option", { value: u, selected: u === result.units }, label)));
  const add = el("button", { class: "primary", type: "button" }, "Add the chosen plans as floors");
  const cancel = el("button", { type: "button", "data-testid": "plans-cancel", title: "Put these plans away, adding no floor" }, "Cancel");
  cancel.addEventListener("click", () => {
    if (jobId) closePlans(jobId);
    area.replaceChildren();
    toast("No floors added");
  });
  const updateCount = () => {
    const chosen = cards.filter((c) => c.chosen());
    const byPlace = new Map();
    chosen.forEach((c) => byPlace.set(c.place().key, [...(byPlace.get(c.place().key) || []), c]));
    let clash = null;
    for (const c of cards) {
      const { key, building, floor } = c.place();
      const others = (byPlace.get(key) || []).filter((o) => o !== c).map((o) => o.plan.title || `plan ${o.plan.index}`);
      const has = c.existing();
      if (has && c.chosen() && !c.replacing()) {
        c.say(`${building} already has floor ${floor} (${has.name}${has.drawing ? `, from ${has.drawing}` : ""}): replace its drawing, or give this plan another floor.`, true);
        clash = { building, floor };
      } else if (!others.length) c.say(has && c.replacing() ? `Replaces the drawing of ${has.name}: its rooms keep their IDs.` : "");
      else if (c.chosen()) {
        c.say(`${others.join(", ")} is also floor ${floor} of ${building}: give one of them another floor or building.`, true);
        clash = { building, floor };
      } else c.say(`Floor ${floor} of ${building} is ${others.join(", ")}. To add this plan too, give it another building or floor.`);
    }
    add.textContent = clash ? `Two plans are floor ${clash.floor} of ${clash.building}`
      : chosen.length ? `Add ${chosen.length} floor${chosen.length === 1 ? "" : "s"}` : "Choose plans to add";
    add.disabled = !chosen.length || clash !== null;
  };
  cards.forEach((c) => c.onChange(updateCount));
  updateCount();
  add.addEventListener("click", async () => {
    const plans = cards.filter((c) => c.chosen()).map((c) => c.value());
    try {
      await runJob(api(`projects/${code}/floors`, { drawing, units: result.units, plans }));
      toast("Floors added and converted");
      await projectPage(code);
    } catch (e) {
      toast(e.message, true);
    }
  });
  const when = found ? new Date(found).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : null;
  area.replaceChildren(el("section", { class: "card", "data-plans": drawing },
    el("h2", {}, `Plans in ${drawing}`),
    when ? el("p", { class: "muted small" }, `Found at ${when}, before this page was opened again: add them as floors, or find them again.`) : null,
    el("p", { class: result.units_sure ? "muted small" : "unsure small" }, result.units_reason,
      result.units_sure ? "" : " If the plans below look the wrong size, choose the units."),
    el("p", { class: "muted small" }, result.levels.summary
      ? `Levels in the drawing: ${result.levels.summary}. Each floor's height and parapet are set from them.`
      : `No floor levels on its sections or plans: floors are ${result.levels.height} m high unless you change them.`),
    el("p", { class: "muted small" }, "Choose the plans that are floors; Studio lines them up and finds the rooms."),
    el("div", { class: "row" }, el("label", { class: "check" }, "Units ", unitChoice), el("span", { class: "grow" }), cancel, add),
    el("div", { class: "plans" }, cards.map((c) => c.node)),
  ));
  area.scrollIntoView({ behavior: "smooth" });
}

const NEW = "__new__"; // a new location or building, named in the box beside it

function planCard(plan, top, levels, project) {
  const isFloor = plan.kind === "floor_plan" || plan.kind === "roof_plan";
  const ordinal = plan.kind === "roof_plan" ? top + 1 : plan.floor;
  const check = el("input", { type: "checkbox", checked: isFloor && plan.kind !== "site_plan" && ordinal !== null });
  // Where it goes: project → location → building → floor. An existing location and
  // building are chosen from the project's; a new one is named.
  const locations = project.locations;
  const location = el("select", {}, ...locations.map((l) => el("option", { value: l.id }, l.name)),
    el("option", { value: NEW }, "New location…"));
  const locationName = el("input", { type: "text", value: project.project.name, placeholder: "location name" });
  const building = el("select");
  const buildingName = el("input", { type: "text", value: plan.building || "Main building", placeholder: "building name" });
  const floors = el("p", { class: "muted small" });
  const replace = el("input", { type: "checkbox" });
  const replaceLabel = el("label", { class: "check", hidden: true }, replace, "Replace its drawing (its rooms keep their IDs)");
  const chosenLocation = () => locations.find((l) => l.id === location.value) || null;
  const chosenBuilding = () => chosenLocation()?.buildings.find((b) => b.id === building.value) || null;
  const fillBuildings = () => {
    const loc = chosenLocation();
    const list = loc ? loc.buildings : [];
    building.replaceChildren(...list.map((b) => el("option", { value: b.id }, b.name)), el("option", { value: NEW }, "New building…"));
    // the building the plan names, else the only one there is, else a new one
    const named = list.find((b) => b.name.trim().toLowerCase() === (plan.building || "").trim().toLowerCase());
    building.value = named ? named.id : list.length === 1 ? list[0].id : NEW;
  };
  if (!locations.length) location.value = NEW;
  fillBuildings();
  const floor = el("input", { type: "number", step: "1", value: ordinal ?? 0 });
  const name = el("input", { type: "text", value: plan.kind === "roof_plan" ? "Roof" : FLOOR_NAMES[ordinal ?? 0] || `Floor ${ordinal}` });
  floor.addEventListener("input", () => { name.value = FLOOR_NAMES[floor.value] || `Floor ${floor.value}`; });
  // Height and parapet from the drawing's levels, following the floor number until changed by hand.
  const heightOf = (n) => levels.heights[String(n)] ?? levels.height;
  const parapetOf = (n) => (levels.parapet !== null && n === levels.roof ? levels.parapet : levels.default_parapet);
  const height = el("input", { type: "number", step: "0.05", min: "2", value: heightOf(ordinal ?? 0) });
  const parapet = el("input", { type: "number", step: "0.05", min: "0", value: parapetOf(ordinal ?? 0) });
  let heightSet = false, parapetSet = false;
  height.addEventListener("input", () => { heightSet = true; });
  parapet.addEventListener("input", () => { parapetSet = true; });
  floor.addEventListener("input", () => {
    if (!heightSet) height.value = heightOf(Number(floor.value));
    if (!parapetSet) parapet.value = parapetOf(Number(floor.value));
  });
  const existing = () => chosenBuilding()?.floors.find((f) => f.ordinal === Number(floor.value)) || null;
  const show = () => {
    locationName.hidden = location.value !== NEW;
    buildingName.hidden = building.value !== NEW;
    const b = chosenBuilding();
    floors.textContent = b ? (b.floors.length ? `${b.name} has: ${b.floors.map((f) => `${f.name} (${f.ordinal})`).join(", ")}` : `${b.name} has no floors yet`) : "";
    floors.hidden = !b;
    replaceLabel.hidden = !existing();
    if (!existing()) replace.checked = false;
  };
  location.addEventListener("change", () => { fillBuildings(); show(); });
  building.addEventListener("change", show);
  floor.addEventListener("input", show);
  show();
  const note = el("p", { class: "small", hidden: true });
  const node = el("article", { class: "plan" },
    thumbnail(plan),
    el("div", { class: "body" },
      el("div", { class: "row" }, el("span", { class: `badge ${isFloor ? "plan-kind" : ""}` }, KIND[plan.kind] || plan.kind),
        el("span", { class: "muted small" }, `${plan.size[0]} × ${plan.size[1]} m`)),
      el("div", { class: "title" }, plan.title || `Untitled plan ${plan.index}`),
      el("label", { class: "check" }, check, "Add as a floor"),
      el("div", { class: "fields" },
        el("label", { style: "grid-column: 1 / -1" }, "Location", location, locationName),
        el("label", { style: "grid-column: 1 / -1" }, "Building", building, buildingName),
        el("label", {}, "Floor", floor),
        el("label", {}, "Floor name", name),
        el("label", { title: "Floor to floor" }, "Height (m)", height),
        el("label", { title: "The low walls around its terraces and balconies" }, "Parapet (m)", parapet)),
      floors, replaceLabel,
      note));
  const sync = () => node.classList.toggle("chosen", check.checked);
  sync();
  return {
    node,
    plan,
    area: plan.size[0] * plan.size[1],
    chosen: () => check.checked,
    choose: (yes) => { check.checked = yes; sync(); },
    existing,
    replacing: () => replace.checked,
    /** The chosen building's next floor (0 for a new building), chosen. */
    nextFloor: () => {
      const b = chosenBuilding();
      floor.value = b && b.floors.length ? Math.max(...b.floors.map((f) => f.ordinal)) + 1 : 0;
      floor.dispatchEvent(new Event("input"));
      check.checked = true;
      sync();
    },
    /** The floor of a building this plan would be. */
    place: () => {
      const b = chosenBuilding();
      const locKey = location.value === NEW ? `new:${locationName.value.trim().toLowerCase()}` : location.value;
      const bName = b ? b.name : buildingName.value.trim() || "Main building";
      const bKey = b ? b.id : `${locKey}|new:${bName.toLowerCase()}`;
      return { key: `${bKey}|${Number(floor.value)}`, building: bName, floor: Number(floor.value) };
    },
    say: (text, warn = false) => { note.textContent = text; note.hidden = !text; note.className = warn ? "unsure small" : "muted small"; },
    onChange: (fn) => {
      check.addEventListener("change", () => { sync(); fn(); });
      for (const input of [location, locationName, building, buildingName, floor, replace]) {
        input.addEventListener(input.tagName === "SELECT" || input.type === "checkbox" ? "change" : "input", fn);
      }
    },
    value: () => {
      const b = chosenBuilding();
      return {
        index: plan.index, title: plan.title, region: plan.region,
        ...(location.value === NEW ? { location: locationName.value.trim() || project.project.name } : { location_id: location.value }),
        ...(b ? { building_id: b.id } : { building: buildingName.value.trim() || "Main building" }),
        ordinal: Number(floor.value), name: name.value, height: Number(height.value), parapet: Number(parapet.value),
        replace: Boolean(existing() && replace.checked),
      };
    },
  };
}

function thumbnail(plan) {
  const [w, h] = plan.size;
  const pad = 1.5;
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${w + 2 * pad} ${h + 2 * pad}`);
  svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
  const d = plan.preview.map((pts) => {
    let s = `M${pts[0]} ${-pts[1]}`;
    for (let i = 2; i < pts.length; i += 2) s += `L${pts[i]} ${-pts[i + 1]}`;
    return s;
  }).join("");
  const g = document.createElementNS(SVG_NS, "g");
  g.setAttribute("transform", `translate(${pad - 1} ${h + pad + 1})`);
  const path = document.createElementNS(SVG_NS, "path");
  path.setAttribute("d", d);
  g.append(path);
  svg.append(g);
  return svg;
}

// ---- buildings, placement, export -------------------------------------------------

/** Studio's 3D page (world.html) showing the project as it is now: no export needed. */
function worldUrl(code, { building, floor } = {}) {
  const pkg = `/api/projects/${encodeURIComponent(code)}/preview.storeypath`;
  const owner = building || (floor && floor.split("-").slice(0, 3).join("-")); // a floor's building: its ID's first three parts
  const q = new URLSearchParams({ pkg: owner ? `${pkg}?building=${encodeURIComponent(owner)}` : pkg });
  if (building) q.set("building", building);
  if (floor) q.set("floor", floor);
  return `/world.html?${q}`;
}

function buildingsCard(code, p) {
  const buildings = p.locations.flatMap((l) => l.buildings);
  if (!buildings.length) {
    return el("section", { class: "card" }, el("h2", {}, "Buildings"),
      el("p", { class: "empty" }, "No floors yet: add a drawing and choose its plans."));
  }
  return el("section", { class: "buildings" }, buildings.map((b) => el("div", { class: "card" },
    el("div", { class: "row" }, el("h2", { class: "grow" }, b.name), el("code", { class: "muted small" }, b.id),
      b.floors.some((f) => f.converted) && onBuilding(p, b.id, "view")
        ? el("a", { class: "button", href: `/navigate.html?${new URLSearchParams({ p: code, building: b.id })}`,
          title: "Find the way in this building: from a kiosk, an entrance or any room, to any room" }, "Navigate") : null,
      b.floors.some((f) => f.converted)
        ? el("a", { class: "button", href: worldUrl(code, { building: b.id }), target: "_blank" }, "3D") : null),
    el("table", { class: "floors" },
      el("thead", {}, el("tr", {}, el("th", {}, "Floor"), el("th", {}, "From"), el("th", {}, "How it was read"), el("th", {}, ""))),
      el("tbody", {}, b.floors.map((f) => el("tr", { "data-floor": f.id },
        el("td", {}, el("strong", {}, f.name), el("div", { class: "muted small" }, `${f.ordinal} · ${f.id.split("-").at(-1)}`),
          el("div", { class: "on-floor", "data-on-floor": f.id })),
        el("td", {}, f.drawing || "–", el("div", { class: "muted small" }, f.view || "")),
        el("td", {}, f.layers.length
          ? el("details", {}, el("summary", {}, `${f.layers.length} layers recognised`), el("ul", { class: "layers" }, f.layers.map((l) => el("li", {}, l))))
          : el("span", { class: "muted small" }, f.method === "package" ? "from a package: add its drawing to read it again"
            : f.converted ? "with a layer profile" : "not converted")),
        el("td", { class: "actions" }, f.converted ? [
          el("a", { href: `/review.html?p=${encodeURIComponent(code)}#floor=${encodeURIComponent(f.id)}` },
            onFloor(p, f.id, "edit") ? "Review" : "View"),
          el("a", { href: worldUrl(code, { floor: f.id }), target: "_blank", title: "This floor in 3D" }, "3D"),
        ] : f.method !== "package" && f.drawing && onFloor(p, f.id, "edit") ? readAgain(code, f) : null,
          onBuilding(p, b.id, "edit") ? deleteFloor(code, f) : null))))),
    onBuilding(p, b.id, "edit") ? placementForm(code, b) : null,
  )));
}

// A floor whose reading failed or was stopped: read its drawing again.
/** Deleting a floor, once its name is typed: its rooms, doors and items retired (their
 * IDs never used again), its drawing kept. */
function deleteFloor(code, f) {
  const plural = (n, what) => `${n} ${what}${n === 1 ? "" : "s"}`;
  const button = el("button", { type: "button", class: "danger", title: `Take ${f.name} out of the building` }, "Delete…");
  button.addEventListener("click", async () => {
    const typed = prompt(`Delete the floor ${f.name}? Its ${plural(f.rooms, "room")} and ${plural(f.items, "item")} are ` +
      "retired: their IDs are never used again. Its drawing stays. Type the floor's name to delete it:");
    if (typed === null) return;
    button.disabled = true;
    try {
      const done = await api(`projects/${code}/floors/${encodeURIComponent(f.id)}/delete`, { confirm: typed });
      toast(`${done.name} deleted: ${plural(done.rooms, "room")} and ${plural(done.items, "item")} retired`);
      await projectPage(code);
    } catch (e) {
      toast(e.message, true);
      button.disabled = false;
    }
  });
  return button;
}

/** Why a read of the floor ``id`` was held back, as the question whether to read it anyway. */
function heldWhy(result, id) {
  const why = result?.why?.[id] || "This reading finds far fewer rooms than the floor has (or none), so nothing was changed: the floor keeps its rooms and their IDs";
  return `${why.charAt(0).toUpperCase()}${why.slice(1)}.\n\nThe rooms it no longer has are retired if you read it anyway (their IDs are never used again). Read anyway?`;
}

function readAgain(code, f) {
  const button = el("button", { type: "button", title: `Read ${f.drawing} for this floor again` }, "Read");
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      const r = await runJob(api(`projects/${code}/floors/${f.id}/convert`, {}));
      // a reading that would retire most of the floor, or rooms people corrected, is held back: applied only when asked
      if ((r?.held || []).includes(f.id) && confirm(heldWhy(r, f.id))) await runJob(api(`projects/${code}/floors/${f.id}/convert`, { force: true }));
      toast(`${f.name} read`);
      await projectPage(code);
    } catch (e) {
      toast(e.stopped ? `Reading stopped: ${e.message}` : e.message, !e.stopped);
      button.disabled = false;
    }
  });
  return button;
}

// ---- the site plan ---------------------------------------------------------------
// Where a location's buildings stand relative to each other, around a centre of the
// site's own (not the map): drag a building to move it, its round handle to turn it
// (Shift: by 15°), or use the keys (arrows: 1 m, Shift: 10 m; [ and ]: 1°, Shift:
// 15°), or type where it is and how far it is turned, to any degree. Placing the site on
// the map places every building on it. A compass in its corner says which way north is
// (up, until the site is placed on the map with another bearing of up), and the building
// chosen, which way its plan's up faces: as it really stands, which the 3D views show.
// − and + zoom (and ⌘ or Ctrl with the wheel, a pinch, at the pointer; − and + keys), an
// empty place dragged moves the plan, Fit takes it all in; its grid is of a round length
// (it says which). Measure: two points clicked, the distance between them (snapped to the
// buildings' corners and the centre), in metres or feet (units.js).

const SITE_STEP_M = 1;
const WINDS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
const bearingSaid = (deg) => {
  const d = ((deg % 360) + 360) % 360;
  return `${+d.toFixed(1)}° (${WINDS[Math.round(d / 22.5) % 16]})`;
};

function siteCard(code, loc, p) {
  // a building is moved by who may change it; the site's arrangement and its place on
  // the map, by who may change the whole project
  const buildings = loc.buildings.filter((b) => b.footprint)
    .map((b) => ({ ...b, site: { ...b.site }, locked: !onBuilding(p, b.id, "edit") }));
  const whole = onProject(p, "edit");
  let chosen = buildings.find((b) => !b.placement && !b.locked)?.id ?? null;
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.classList.add("site-plan");
  svg.setAttribute("role", "application");
  svg.setAttribute("aria-label", `Site plan of ${loc.name}: drag a building to move it, its handle to turn it`);
  const x = el("input", { type: "number", step: "0.1" });
  const y = el("input", { type: "number", step: "0.1" });
  const rot = el("input", { type: "number", step: "any" });
  const which = el("strong", {});
  const facing = el("span", { class: "muted small site-facing" });
  const note = el("p", { class: "muted small" });
  // north: the site's up faces the bearing it is placed on the map with (none: north is up)
  const upFaces = loc.placement?.bearing ?? 0;
  const compass = el("div", { class: "site-compass", role: "img", "data-testid": "site-compass",
    "aria-label": `North: the site plan's up faces ${bearingSaid(upFaces)}` });
  compass.title = loc.placement ? `North: the site plan's up faces ${bearingSaid(upFaces)}, as the site is placed on the map`
    : "North: up on the site plan, until the site is placed on the map with another bearing of up";
  compass.innerHTML = `<svg viewBox="-20 -20 40 40" width="44" height="44" aria-hidden="true"><circle class="cp-ring" r="18.5"/>`
    + `<g class="cp-needle" transform="rotate(${-upFaces})"><path class="cp-north" d="M0,-9.5L3.6,0H-3.6Z"/>`
    + `<path class="cp-south" d="M0,9.5L3.6,0H-3.6Z"/><text class="cp-n" x="0" y="-13.2">N</text></g><circle class="cp-pin" r="1.4"/></svg>`;

  const onSite = (b, [px, py]) => {
    const r = (b.site.rotation * Math.PI) / 180;
    const dx = px - b.site.pivot[0], dy = py - b.site.pivot[1];
    return [b.site.x + dx * Math.cos(r) + dy * Math.sin(r), b.site.y - dx * Math.sin(r) + dy * Math.cos(r)];
  };
  const extent = () => {
    const pts = buildings.flatMap((b) => b.footprint.flat().map((p) => onSite(b, p))).concat([[0, 0]]);
    const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
    return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
  };
  let view = null; // fixed while dragging, so the plan does not slide under the pointer
  const fit = () => {
    const [x0, y0, x1, y1] = extent();
    const pad = Math.max(10, 0.08 * Math.max(x1 - x0, y1 - y0));
    view = [x0 - pad, y0 - pad, x1 + pad, y1 + pad];
  };
  /** The plan zoomed by ``k`` (2: twice as near) about ``at`` (site metres; the middle):
   * from 5 m across to 50 km. */
  const zoom = (k, at = null) => {
    const [x0, y0, x1, y1] = view;
    const [cx, cy] = at ?? [(x0 + x1) / 2, (y0 + y1) / 2];
    const f = Math.min(Math.max((x1 - x0) / k, 5), 50000) / (x1 - x0);
    view = [cx - (cx - x0) * f, cy - (cy - y0) * f, cx + (x1 - cx) * f, cy + (y1 - cy) * f];
    draw();
  };
  // measuring: on, its points (two: done) and where the pointer is; snapped to what is near
  const ruler = { on: false, points: [], live: null };
  const perPixel = () => { // metres a pixel of the plan
    const r = svg.getBoundingClientRect();
    return Math.max((view[2] - view[0]) / (r.width || 1), (view[3] - view[1]) / (r.height || 1));
  };
  const snapped = (p) => { // a building's corner or the centre within 10 pixels, else where it is
    let best = null, reach = 10 * perPixel();
    for (const q of [[0, 0], ...buildings.flatMap((b) => b.footprint.flat().map((c) => onSite(b, c)))]) {
      const d = Math.hypot(q[0] - p[0], q[1] - p[1]);
      if (d <= reach) [best, reach] = [q, d];
    }
    return best ?? p;
  };
  const scale = el("div", { class: "site-scale", "aria-live": "polite" });
  const measured = el("span", { class: "site-measured", "aria-live": "polite" });
  const toSite = (e) => { // a pointer's place on the site (metres, y up)
    const pt = svg.createSVGPoint();
    pt.x = e.clientX;
    pt.y = e.clientY;
    const q = pt.matrixTransform(svg.getScreenCTM().inverse());
    return [q.x, -q.y];
  };
  const draw = () => {
    const [vx0, vy0, vx1, vy1] = view;
    svg.setAttribute("viewBox", `${vx0} ${-vy1} ${vx1 - vx0} ${vy1 - vy0}`);
    // the grid, 10 m, beyond the view too: the box is seldom its shape
    const parts = [];
    const grid = roundStep((vx1 - vx0) / 25), step = grid.metres, w = vx1 - vx0, h = vy1 - vy0;
    scale.textContent = `grid ${grid.said}`;
    const [gx0, gy0, gx1, gy1] = [vx0 - 2 * w, vy0 - 2 * h, vx1 + 2 * w, vy1 + 2 * h];
    for (let gx = Math.ceil(gx0 / step) * step; gx <= gx1; gx += step) parts.push(`<line class="grid" x1="${gx}" y1="${-gy1}" x2="${gx}" y2="${-gy0}"/>`);
    for (let gy = Math.ceil(gy0 / step) * step; gy <= gy1; gy += step) parts.push(`<line class="grid" x1="${gx0}" y1="${-gy}" x2="${gx1}" y2="${-gy}"/>`);
    svg.innerHTML = parts.join("");
    for (const b of buildings) {
      const pts = b.footprint.map((ring) => ring.map((p) => onSite(b, p)));
      const d = pts.map((ring) => `M${ring.map(([a, bb]) => `${a.toFixed(2)},${(-bb).toFixed(2)}`).join("L")}Z`).join("");
      const g = document.createElementNS(SVG_NS, "g");
      g.classList.add("building");
      if (b.placement || b.locked) g.classList.add("fixed");
      if (b.id === chosen) g.classList.add("chosen");
      const path = document.createElementNS(SVG_NS, "path");
      path.setAttribute("d", d);
      path.setAttribute("tabindex", "0");
      path.dataset.id = b.id;
      const all = pts.flat();
      const top = Math.max(...all.map((p) => p[1]));
      const [cx, cy] = [b.site.x, b.site.y];
      const label = document.createElementNS(SVG_NS, "text");
      label.setAttribute("x", cx);
      label.setAttribute("y", -cy);
      label.setAttribute("font-size", ((vx1 - vx0) / 45).toFixed(2));
      label.textContent = b.name;
      g.append(path, label);
      if (!b.placement && !b.locked && b.id === chosen) {
        // the turning handle: above its middle, turned with it
        const r = (b.site.rotation * Math.PI) / 180, len = top - cy + Math.max(3, (vx1 - vx0) / 40);
        const hx = cx + len * Math.sin(r), hy = cy + len * Math.cos(r);
        const stem = document.createElementNS(SVG_NS, "line");
        Object.entries({ x1: cx, y1: -cy, x2: hx, y2: -hy, class: "stem" }).forEach(([k, v]) => stem.setAttribute(k, v));
        const handle = document.createElementNS(SVG_NS, "circle");
        Object.entries({ cx: hx, cy: -hy, r: Math.max(1.2, (vx1 - vx0) / 90), class: "handle" }).forEach(([k, v]) => handle.setAttribute(k, v));
        handle.dataset.turn = b.id;
        g.append(stem, handle);
      }
      svg.append(g);
    }
    const c = Math.max(2, (vx1 - vx0) / 60); // the centre, over the buildings
    svg.insertAdjacentHTML("beforeend", `<g class="centre"><line x1="${-c}" y1="0" x2="${c}" y2="0"/><line x1="0" y1="${-c}" x2="0" y2="${c}"/><circle r="${c / 3}"/></g>`);
    // the measure: its line, its ends, and how long it is, over its middle
    const [ma, mb] = [ruler.points[0], ruler.points[1] ?? ruler.live];
    svg.classList.toggle("measuring", ruler.on);
    measured.textContent = ma && mb ? `${lengthSaid(Math.hypot(mb[0] - ma[0], mb[1] - ma[1]))}` : ruler.on ? "Click two points" : "";
    if (ma) {
      const g = document.createElementNS(SVG_NS, "g");
      g.classList.add("measure");
      const dot = (p) => `<circle cx="${p[0]}" cy="${-p[1]}" r="${(vx1 - vx0) / 220}"/>`;
      g.innerHTML = mb ? `<line x1="${ma[0]}" y1="${-ma[1]}" x2="${mb[0]}" y2="${-mb[1]}"/>${dot(ma)}${dot(mb)}` : dot(ma);
      if (mb) {
        const t = document.createElementNS(SVG_NS, "text");
        t.setAttribute("x", (ma[0] + mb[0]) / 2);
        t.setAttribute("y", -(ma[1] + mb[1]) / 2);
        t.setAttribute("font-size", ((vx1 - vx0) / 50).toFixed(2));
        t.textContent = lengthSaid(Math.hypot(mb[0] - ma[0], mb[1] - ma[1]));
        g.append(t);
      }
      svg.append(g);
    }
    const b = buildings.find((o) => o.id === chosen);
    which.textContent = b ? b.name : "";
    if (b) {
      x.value = b.site.x.toFixed(2);
      y.value = b.site.y.toFixed(2);
      rot.value = String(+b.site.rotation.toFixed(3));
    }
    // which way its plan's up faces: its own placement's, else turned on the site, from north
    facing.textContent = b ? `its plan's up faces ${bearingSaid(b.placement ? b.placement.bearing : upFaces + b.site.rotation)}` : "";
    for (const input of [x, y, rot]) input.disabled = !b || Boolean(b.placement) || b.locked;
    note.textContent = b?.locked ? `${b.name} is not yours to move: you may look at it.`
      : b?.placement
      ? `${b.name} is placed on the map by itself: it stands there whatever the site plan says.`
      : "Drag a building to move it; drag its round handle to turn it (Shift: by 15°). Keys: arrows move 1 m (Shift: 10 m), [ and ] turn 1° (Shift: 15°).";
  };
  const save = async (b) => {
    try {
      const r = await api(`projects/${code}/buildings/${b.id}/site`, { x: b.site.x, y: b.site.y, rotation: b.site.rotation });
      Object.assign(b.site, r.site);
    } catch (e) {
      toast(e.message, true);
    }
  };
  let saving = null;
  const saveSoon = (b) => { clearTimeout(saving); saving = setTimeout(() => save(b), 400); };

  let drag = null;
  svg.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    if (ruler.on) { // a point of the measure (a third: a new measure)
      const p = snapped(toSite(e));
      ruler.points = ruler.points.length === 1 ? [ruler.points[0], p] : [p];
      ruler.live = null;
      return draw();
    }
    const id = e.target.dataset?.turn || e.target.closest?.("[data-id]")?.dataset.id;
    const b = buildings.find((o) => o.id === id);
    if (!b) { // an empty place: the plan moved with the pointer
      e.preventDefault();
      svg.setPointerCapture(e.pointerId);
      const inv = svg.getScreenCTM().inverse(); // (as it is now: the plan moves under it)
      const at = (ev) => {
        const q = new DOMPoint(ev.clientX, ev.clientY).matrixTransform(inv);
        return [q.x, -q.y];
      };
      drag = { pan: true, at, from: at(e), view: [...view] };
      return;
    }
    chosen = b.id;
    if (b.placement || b.locked) return draw();
    e.preventDefault();
    svg.setPointerCapture(e.pointerId);
    drag = { b, turn: Boolean(e.target.dataset?.turn), from: toSite(e), at: [b.site.x, b.site.y] };
    draw();
  });
  svg.addEventListener("pointermove", (e) => {
    if (ruler.on && ruler.points.length === 1) {
      ruler.live = snapped(toSite(e));
      return draw();
    }
    if (!drag) return;
    if (drag.pan) {
      const [px, py] = drag.at(e), dx = px - drag.from[0], dy = py - drag.from[1];
      view = [drag.view[0] - dx, drag.view[1] - dy, drag.view[2] - dx, drag.view[3] - dy];
      return draw();
    }
    const p = toSite(e);
    if (drag.turn) {
      let deg = (Math.atan2(p[0] - drag.b.site.x, p[1] - drag.b.site.y) * 180) / Math.PI;
      deg = e.shiftKey ? Math.round(deg / 15) * 15 : Math.round(deg);
      drag.b.site.rotation = (deg + 360) % 360;
    } else {
      drag.b.site.x = Math.round((drag.at[0] + p[0] - drag.from[0]) * 10) / 10;
      drag.b.site.y = Math.round((drag.at[1] + p[1] - drag.from[1]) * 10) / 10;
    }
    draw();
  });
  const drop = () => {
    if (!drag) return;
    const { b, pan } = drag;
    drag = null;
    if (!pan) save(b);
  };
  svg.addEventListener("wheel", (e) => { // ⌘ or Ctrl and the wheel (a pinch): zoomed at the pointer; else the page scrolls
    if (!(e.ctrlKey || e.metaKey)) return;
    e.preventDefault();
    const by = -e.deltaY * (e.deltaMode === 1 ? 0.05 : 0.01); // (a wheel's notch: at most half again)
    zoom(Math.exp(Math.min(Math.max(by, -0.4), 0.4)), toSite(e));
  }, { passive: false });
  svg.addEventListener("pointerup", drop);
  svg.addEventListener("pointercancel", drop);
  svg.addEventListener("keydown", (e) => {
    const zoomed = { "+": 1.25, "=": 1.25, "-": 0.8, "_": 0.8 }[e.key];
    if (zoomed) {
      e.preventDefault();
      return zoom(zoomed);
    }
    if (e.key === "Escape" && ruler.on) return setMeasuring(false);
    const b = buildings.find((o) => o.id === (e.target.dataset?.id || chosen));
    if (!b || b.placement || b.locked) return;
    const step = e.shiftKey ? 10 : SITE_STEP_M, turn = e.shiftKey ? 15 : 1;
    const moves = { ArrowLeft: [-step, 0, 0], ArrowRight: [step, 0, 0], ArrowUp: [0, step, 0], ArrowDown: [0, -step, 0],
      "[": [0, 0, -turn], "]": [0, 0, turn], "{": [0, 0, -15], "}": [0, 0, 15] };
    const m = moves[e.key];
    if (!m) return;
    e.preventDefault();
    chosen = b.id;
    b.site.x += m[0];
    b.site.y += m[1];
    b.site.rotation = (b.site.rotation + m[2] + 360) % 360;
    draw();
    svg.querySelector(`[data-id="${b.id}"]`)?.focus();
    saveSoon(b);
  });
  for (const [input, key] of [[x, "x"], [y, "y"], [rot, "rotation"]]) {
    input.addEventListener("change", () => {
      const b = buildings.find((o) => o.id === chosen);
      if (!b || b.locked || !Number.isFinite(Number(input.value))) return;
      b.site[key] = key === "rotation" ? (Number(input.value) % 360 + 360) % 360 : Number(input.value);
      fit();
      draw();
      save(b);
    });
  }
  const sideBySide = el("button", { type: "button", title: "Every building of this site in a row, 10 m apart, in the order of their codes" }, "Side by side");
  sideBySide.addEventListener("click", async () => {
    try {
      const r = await api(`projects/${code}/locations/${loc.id}/arrange`, {});
      for (const b of buildings) if (r.sites[b.code]) Object.assign(b.site, r.sites[b.code]);
      fit();
      draw();
    } catch (e) {
      toast(e.message, true);
    }
  });
  const fitButton = el("button", { type: "button", title: "Every building in view" }, "Fit");
  fitButton.addEventListener("click", () => { fit(); draw(); });
  const zoomButton = (k, label, text) => el("button", { type: "button", class: "btn-icon", "aria-label": label, title: label,
    onclick: () => zoom(k) }, text);
  const measureButton = el("button", { type: "button", "aria-pressed": "false", "data-testid": "site-measure",
    title: "Measure: click two points for the distance between them (snapped to the buildings' corners); Esc stops" }, "Measure");
  const setMeasuring = (on) => {
    Object.assign(ruler, { on, points: [], live: null });
    measureButton.setAttribute("aria-pressed", String(on));
    draw();
  };
  measureButton.addEventListener("click", () => setMeasuring(!ruler.on));
  const offUnits = onUnits(() => (svg.isConnected ? draw() : offUnits()));

  // the site on the map
  const pl = loc.placement || {};
  const lat = el("input", { type: "number", step: "any", value: pl.lat ?? "", placeholder: "e.g. 25.2854" });
  const lon = el("input", { type: "number", step: "any", value: pl.lon ?? "", placeholder: "e.g. 51.5310" });
  const bearing = el("input", { type: "number", step: "any", value: pl.bearing ?? 0 });
  const onMap = el("form", { class: "placement", onsubmit: async (e) => {
    e.preventDefault();
    try {
      await api(`projects/${code}/locations/${loc.id}/placement`, { lat: lat.value, lon: lon.value, bearing: bearing.value });
      toast(`${loc.name} placed on the map`);
      projectPage(code);
    } catch (err) {
      toast(err.message, true);
    }
  } }, el("label", {}, "Latitude of the centre", lat), el("label", {}, "Longitude", lon),
  el("label", {}, "Bearing of up (°)", bearing), el("button", { type: "submit" }, loc.placement ? "Update" : "Place on the map"));

  fit();
  draw();
  return el("section", { class: "card site-card" },
    el("div", { class: "row" }, el("h2", { class: "grow" }, `Site plan · ${loc.name}`), measured, measureButton, unitsSwitch(el),
      whole ? sideBySide : null, fitButton),
    el("div", { class: "site-stage" }, svg, compass, scale,
      el("div", { class: "site-zoom", role: "group", "aria-label": "Zoom" }, zoomButton(0.8, "Zoom out", "−"), zoomButton(1.25, "Zoom in", "+"))),
    buildings.some((b) => !b.locked)
      ? el("div", { class: "site-fields" }, which, el("label", {}, "x (m)", x), el("label", {}, "y (m)", y), el("label", {}, "turned (°)", rot),
        facing)
      : null,
    buildings.some((b) => !b.locked) ? note : null,
    el("p", { class: "muted small", style: "margin-top: 12px" }, loc.placement
      ? "On the map: the site's centre (the cross) sits at this latitude and longitude; up points to this bearing."
      : whole ? "Not on the map: the buildings stand where the site plan has them, around 0°N 0°E, their shapes and sizes true. Give the latitude and longitude of the centre (the cross), and the compass bearing of up, to put them all on the map."
        : "Not on the map yet: the buildings stand where the site plan has them."),
    whole ? onMap : null);
}

function placementForm(code, b) {
  const pl = b.placement || {};
  const lat = el("input", { type: "number", step: "any", value: pl.lat ?? "", placeholder: "e.g. 24.7136" });
  const lon = el("input", { type: "number", step: "any", value: pl.lon ?? "", placeholder: "e.g. 46.6753" });
  const bearing = el("input", { type: "number", step: "any", value: pl.bearing ?? 0 });
  const save = el("button", { type: "submit" }, b.placement ? "Update location" : "Save location");
  return el("form", { onsubmit: async (e) => {
    e.preventDefault();
    try {
      const [x, y] = b.placement ? [b.placement.x, b.placement.y] : (b.centre || [0, 0]);
      await api(`projects/${code}/buildings/${b.id}/placement`, { lat: lat.value, lon: lon.value, bearing: bearing.value, x, y });
      toast(`${b.name} placed`);
      projectPage(code);
    } catch (err) {
      toast(err.message, true);
    }
  } },
    el("p", { class: "muted small", style: "margin: 14px 0 0" }, b.placement
      ? "On the map: the building's middle sits at this latitude and longitude."
      : "On the map by itself (optional): it then stands there whatever the site plan says. Usually the site is placed on the map instead (Site plan, above), and every building with it."),
    el("div", { class: "placement" },
      el("label", {}, "Latitude", lat), el("label", {}, "Longitude", lon), el("label", {}, "Bearing of up (°)", bearing), save));
}

// ---- a project sent, or a building's package ---------------------------------------

/** Open a .storeypath-project (a project sent from another Studio, as it was) or a
 * .storeypath (a building's package, rebuilt from it with the same IDs, its floors
 * without their drawing until one is added; into its project when that is here). */
function openCard() {
  const input = el("input", { type: "file", accept: ".storeypath,.storeypath-project,.enc", hidden: true });
  const drop = el("label", { class: "drop" }, input,
    el("strong", {}, "Open a project or a building's package"), el("br"),
    "Drop a file here, or click to choose it: a project sent from another Studio (.storeypath-project), or a building's package (.storeypath); encrypted (.enc), its password is asked for.");
  const open = async (file, replace = null) => {
    try {
      toast(`Opening ${file.name}…`);
      const r = await putFile(`open${replace !== null ? `?replace=${encodeURIComponent(replace)}` : ""}`, file);
      if (r === null) return; // (its password not given)
      const floors = `${r.floors} floor${r.floors === 1 ? "" : "s"}`;
      // item types the file brings that this Studio lacks are added only by who may change them
      const left = (r.item_types_not_added || []).length
        ? ` Item types it brings were not added (an admin, or who may change the item types, takes them from the file on the Item types page): ${r.item_types_not_added.join(", ")}; their items are drawn as plain items.`
        : "";
      const noModels = (r.models_not_added || []).length
        ? ` 3D models it brings were not added (an admin, or who may change the item types, adds them by opening it): ${r.models_not_added.join(", ")}.`
        : "";
      toast((r.how === "project"
        ? `${r.name} opened as it was sent: ${floors}, ${r.drawings} drawing${r.drawings === 1 ? "" : "s"}.`
        : r.how === "building"
          ? `${r.name}: ${r.replaced.length ? "its building put in place of the one here" : "a building added to it"} from the package (${floors}, the same IDs). Its other buildings are as they were.`
          : `${r.name} rebuilt from the package: ${floors}, the same IDs. To read a floor again, add its drawing to it.`) + left + noModels,
      false, 12000);
      location.hash = `#/p/${r.code}`;
    } catch (e) {
      if (e.status === 409 && replace === null) {
        const typed = prompt(e.data.building
          ? `${e.data.name} (${e.data.code}) is here, with this building already (${e.data.building}). Put the package's in its place? ` +
            `Its corrections since are lost; its floors keep their drawings, and the project's other buildings are left as they are. Type the project's name to replace it:`
          : `${e.data.name} (${e.data.code}) is here already. Put the file in its place? What is here now ` +
            `(its drawings, corrections and exports) is lost. Type the project's name to replace it:`);
        if (typed !== null) return open(file, typed);
        return;
      }
      toast(`${file.name}: ${e.message}`, true);
    }
  };
  input.addEventListener("change", () => input.files[0] && open(input.files[0]));
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); e.dataTransfer.files[0] && open(e.dataTransfer.files[0]); });
  return el("section", { class: "card" }, drop);
}

/** A file downloaded encrypted with a password asked for (seal.js). */
async function sealed(url, name) {
  try {
    if (await downloadSealed(url, name)) toast(`${name}.enc: downloading it. Whoever opens it needs the password`, false, 8000);
  } catch (e) {
    toast(e.message, true);
  }
}

function exportCard(code, p) {
  // a package holds one building: it lists what changed in that building alone (made by
  // who may change the whole building)
  const buildings = p.locations.flatMap((l) => l.buildings.filter((b) => b.floors.some((f) => f.converted)
    && onBuilding(p, b.id, "edit")));
  if (!buildings.length && !onProject(p, "view") && !p.exports.length) return null;
  const what = el("select", { "aria-label": "Building to export" },
    ...buildings.map((b) => el("option", { value: b.id }, b.name)));
  // its item types: those its items use (each once), or the whole catalogue for another system to choose from
  const types = el("select", { "aria-label": "Item types in the package", "data-testid": "export-item-types",
    title: "The item types the package carries, each once: what another system reads its items by, and may take into its own" },
  el("option", { value: "used" }, "Item types its items use"),
  el("option", { value: "all" }, "Every item type"));
  const button = el("button", { class: "primary", type: "button", disabled: !buildings.length, onclick: async () => {
    try {
      const r = await runJob(api(`projects/${code}/export`, { building: what.value, item_types: types.value }));
      // downloaded at once; its copy stays in the list, the package as it was sent
      const a = el("a", { href: `/api/projects/${encodeURIComponent(code)}/exports/${encodeURIComponent(r.file)}`, download: r.file });
      document.body.append(a);
      a.click();
      a.remove();
      toast(`Exported ${r.file}: downloading it. A copy stays in the list below`, false, 8000);
      projectPage(code);
    } catch (e) {
      toast(e.message, true);
    }
  } }, "Export package");
  const send = el("a", { class: "button", href: `/api/projects/${encodeURIComponent(code)}/project.storeypath-project`,
    download: `${code}.storeypath-project`,
    title: "One file for another Studio to continue this project: its drawings, corrections and edits (not a package: other systems read a building's)" },
  "Download project");
  const sendSealed = el("button", { type: "button", "data-testid": "project-sealed",
    title: "The project file encrypted with a password you choose (AES-256): to send where others could read it",
    onclick: () => sealed(`/api/projects/${encodeURIComponent(code)}/project.storeypath-project`, `${code}.storeypath-project`) },
  "Encrypted…");
  return el("section", { class: "card" },
    el("div", { class: "row" }, el("h2", { class: "grow" }, "Packages"), onProject(p, "view") ? send : null,
      onProject(p, "view") ? sendSealed : null,
      buildings.length > 1 ? what : null, buildings.length ? types : null, buildings.length ? button : null),
    el("p", { class: "muted small" }, "A package (.storeypath) holds one building: its floors, spaces, doors and items with their IDs, ready for the viewer and for any other system, which leaves the project's other buildings as they are. Every export lists what changed in that building since it was last exported; moving a building on the map changes nothing in it. It carries the item types its items use, each once (or every item type of this Studio), so another system knows them and may take them into its own. Download project gives one file (.storeypath-project) to send to someone who continues the project in their Studio: they open it on their Projects page."),
    p.exports.length ? el("p", { class: "muted small" }, ...(p.exports_folder // (where the server keeps them: told to admins)
      ? ["Saved in ", el("code", {}, p.exports_folder), ", newest first:"] : ["Newest first:"])) : null,
    p.exports.length ? el("ul", { class: "exports" }, p.exports.map((f) => {
      const url = `/api/projects/${encodeURIComponent(code)}/exports/${encodeURIComponent(f)}`;
      return el("li", {}, el("code", { class: "grow" }, f),
        el("a", { href: url, download: f }, "Download"),
        el("a", { href: "#", "data-testid": "export-sealed", title: "Encrypted with a password you choose (AES-256): to send where others could read it",
          onclick: (e) => { e.preventDefault(); sealed(url, f); } }, "Encrypted…"),
        planPage ? el("a", { href: `/viewer/svg/example/index.html?package=${encodeURIComponent(url)}`, target: "_blank",
          title: "Each floor as a plan: walls, doors with their swings, windows, rooms coloured by type" }, "2D plan") : null,
        el("a", { href: `/world.html?pkg=${encodeURIComponent(url)}`, target: "_blank",
          title: "The building as built: walls, doors and windows; walk through it, or orbit it as a dollhouse" }, "3D"),
        el("a", { href: `/viewer/examples/basic/index.html?basemap=0&pkg=${encodeURIComponent(url)}`, target: "_blank",
          title: "Each room as a block coloured by its type, floors stacked on a map, with search: a check of the rooms, not the building's walls" },
        "Rooms by type"));
    })) : el("p", { class: "empty" }, "No packages yet."),
  );
}

window.addEventListener("hashchange", route);
setupChrome();
whoami().then((who) => {
  me = who;
  $("account").replaceChildren(accountMenu(me));
  return showStatus(); // what Studio can do first: the pages offer only that
}).finally(route);
