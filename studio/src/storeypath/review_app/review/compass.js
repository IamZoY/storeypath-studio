// The compass, on the canvas (bottom right, over the building's floors), in 2D, 3D and
// walking: which way north is. On the plan it stays as the building stands (the plan's up
// faces the building's bearing); in 3D and walking it turns as the view turns. A click on it
// shows North: the floor on a compass rose, turned as the building stands; whoever may change
// the building turns it there to stand as it does, by any degree: typed, a degree or 15° at
// a time, or dragged round (Shift: by 15°). Saved, the whole building turns, every floor of
// it, on the map, in 3D and in its packages (POST …/buildings/<id>/bearing); its rooms and
// items keep their places on the plan. In 3D, beside it, Top (U) looks straight down, the
// plan's up at the top as the plan has it, and tilts back.

import { request } from "./api.js";
import { on } from "./bus.js";
import { command, run } from "./commands.js";
import { $, el, icon, svg } from "./dom.js";
import { toast } from "./notify.js";
import { BASE, buildingOf, extent, pathData, state, view3d } from "./state.js";
import { refresh3d } from "./view3d.js";

const WINDS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
const DIAL = 156; // px: North's rose, across
const ROSE = DIAL / 2 - 16; // its ring's radius, the letters outside it

/** A bearing in [0, 360). */
const norm = (deg) => ((deg % 360) + 360) % 360;
/** A bearing as people say it: "23.5° NNE". */
export const said = (deg) => `${+norm(deg).toFixed(1)}° ${WINDS[Math.round(norm(deg) / 22.5) % 16]}`;

// the bearing being set (null: none), North's panel, the building it turns and its Face north
const north = { draft: null, box: null, building: null, faceNorth: null };

/** The building of the floor shown: its ID and way ({ bearing, on_map, own }). */
function building() {
  const id = state.floor ? buildingOf(state.floor.id) : null;
  return { id, way: (id && state.project?.buildings?.[id]) || { bearing: 0, on_map: false, own: false } };
}

/** Whether the person may turn the building ``id``: their level on it (or the project's). */
function mayTurn(id) {
  const can = state.project?.can;
  if (!can) return true; // (a Studio without accounts)
  return can.buildings?.[id] === "edit" || can.buildings?.[id] === "share";
}

/** Which way the screen's up faces: on the plan, the plan's up (as it is being set, else the
 * building's); in 3D and walking, the view's. */
function facing() {
  const w = view3d.world;
  if (view3d.shown && w && view3d.building === building().id) return w.viewBearing;
  return north.draft ?? building().way.bearing;
}

/** The compass's needle turned to north, and what it says. */
function render() {
  const button = $("compass");
  if (!button) return;
  button.hidden = !state.floor;
  const turn = facing();
  button.querySelector(".cp-needle").setAttribute("transform", `rotate(${(-turn).toFixed(2)})`);
  const what = view3d.shown ? `the view faces ${said(turn)}` : `the plan's up faces ${said(turn)}`;
  button.dataset.tip = `North: ${what}`;
  button.setAttribute("aria-label", `North: ${what}. Show which way the building stands`);
  const top = $("view3d-ctl");
  top.hidden = view3d.mode !== "3d";
  $("top-view").setAttribute("aria-pressed", String(Boolean(view3d.world?.top)));
}

/** The compass's face: a ring, and a needle with N at its tip. */
function face() {
  const s = svg("svg", { class: "cp-face", viewBox: "-20 -20 40 40", width: 40, height: 40, "aria-hidden": "true" });
  const needle = svg("g", { class: "cp-needle" });
  const n = svg("text", { class: "cp-n", x: 0, y: -13.2 });
  n.textContent = "N";
  needle.append(svg("path", { class: "cp-north", d: "M0,-9.5L3.6,0H-3.6Z" }), svg("path", { class: "cp-south", d: "M0,9.5L3.6,0H-3.6Z" }), n);
  s.append(svg("circle", { class: "cp-ring", r: 18.5 }), needle, svg("circle", { class: "cp-pin", r: 1.4 }));
  return s;
}

// ---- North: which way the building stands, and turned ------------------------------------

/** North's rose: N, E, S and W fixed, a tick every 15°; over it, the floor turned as the
 * building stands, its plan's up marked. Its group turned: ``.np-floor``. */
function dial() {
  const h = DIAL / 2, s = svg("svg", { class: "np-dial", viewBox: `${-h} ${-h} ${DIAL} ${DIAL}`, width: DIAL, height: DIAL,
    role: "img", "aria-label": "The floor on a compass rose, north up" });
  s.append(svg("circle", { class: "np-ring", r: ROSE }));
  for (let a = 0; a < 360; a += 15) {
    const long = a % 90 === 0 ? 9 : a % 45 === 0 ? 6 : 3.5, r = (a * Math.PI) / 180, [x, y] = [Math.sin(r), -Math.cos(r)];
    s.append(svg("line", { class: a % 90 === 0 ? "np-tick main" : "np-tick", x1: x * ROSE, y1: y * ROSE, x2: x * (ROSE - long), y2: y * (ROSE - long) }));
    if (a % 90 === 0) {
      const t = svg("text", { class: a === 0 ? "np-letter n" : "np-letter", x: x * (ROSE + 9), y: y * (ROSE + 9) });
      t.textContent = "NESW"[a / 90];
      s.append(t);
    }
  }
  const floor = svg("g", { class: "np-floor" });
  const outline = state.floor?.outline;
  const box = outline ? extent(outline) : null;
  if (box && Number.isFinite(box[0])) {
    const [x0, y0, x1, y1] = box, k = ((ROSE - 14) * 2) / Math.max(Math.hypot(x1 - x0, y1 - y0), 1e-6);
    floor.append(svg("path", { class: "np-shape", d: pathData(outline), "vector-effect": "non-scaling-stroke",
      transform: `scale(${k} ${-k}) translate(${-(x0 + x1) / 2} ${-(y0 + y1) / 2})` }));
  }
  floor.append(svg("path", { class: "np-up", d: `M0,${-(ROSE - 3)}l-5.5,9h11z` })); // the plan's up
  s.append(floor, svg("circle", { class: "np-pin", r: 2 }));
  return s;
}

/** North's panel shown (shown already: put away). */
function openNorth() {
  if (north.box) return closeNorth();
  const { id, way } = building();
  if (!id) return toast("Open a floor first", true);
  const may = mayTurn(id);
  north.building = id;
  north.draft = way.bearing;
  const rose = dial();
  const input = el("input", { id: "np-bearing", type: "number", step: "any", inputmode: "decimal", disabled: !may,
    "aria-label": "Which way the plan's up faces: degrees clockwise from north" });
  const wind = el("span", { class: "np-wind" });
  const nudge = (by) => el("button", { type: "button", class: "btn-ghost btn-sm", disabled: !may, "data-testid": `np-nudge${by}`,
    onclick: () => setDraft(north.draft + by) }, `${by > 0 ? "+" : "−"}${Math.abs(by)}°`);
  const save = el("button", { type: "button", class: "btn-primary", "data-testid": "np-save", hidden: !may, onclick: saveNorth }, "Save");
  const close = el("button", { type: "button", class: "btn-ghost", onclick: closeNorth }, may ? "Cancel" : "Close");
  const faceNorth = el("button", { type: "button", class: "btn-ghost", hidden: view3d.mode !== "3d",
    onclick: () => run("view.north") }, icon("navigation-2"), "Face north");
  const box = el("div", { id: "north", class: "north-panel", role: "dialog", "aria-label": "North" },
    el("div", { class: "np-head" }, el("strong", {}, "North"), el("span", { class: "np-meta" },
      way.on_map ? (way.own ? "as the building is placed on the map" : "as its site is placed on the map")
        : "its site is not on the map yet")),
    rose,
    el("label", { class: "np-row" }, el("span", {}, "The plan's up faces"), input, el("span", {}, "°"), wind),
    may ? el("div", { class: "np-row np-nudges" }, nudge(-15), nudge(-1), nudge(1), nudge(15)) : null,
    el("p", { class: "np-note" }, may
      ? "Turns the whole building, every floor of it: on the map, in 3D and in its packages. Its rooms and items keep their places on the plan. Drag the rose to turn it (Shift: by 15°)."
      : "You may look at this building, not turn it."),
    el("div", { class: "np-actions" }, faceNorth, el("span", { class: "grow" }), close, save));
  Object.assign(north, { box, faceNorth });
  $("map").append(box);

  function setDraft(deg, typed = false) { // (typed: the box left as it is typed)
    north.draft = Math.round(norm(deg) * 10) / 10;
    if (!typed) input.value = String(north.draft);
    wind.textContent = WINDS[Math.round(north.draft / 22.5) % 16];
    rose.querySelector(".np-floor").setAttribute("transform", `rotate(${north.draft})`);
    save.disabled = north.draft === Math.round(norm(way.bearing) * 10) / 10;
    render();
  }
  input.addEventListener("input", () => {
    const v = Number(input.value);
    if (input.value.trim() !== "" && Number.isFinite(v)) setDraft(v, true);
  });
  input.addEventListener("change", () => (input.value = String(north.draft)));
  box.addEventListener("keydown", (e) => {
    e.stopPropagation(); // (the page's keys are not this panel's)
    if (e.key === "Escape") closeNorth();
    else if (e.key === "Enter" && may && !save.disabled) saveNorth();
  });
  if (may) { // dragged round: turned by as much as the pointer went round the middle
    const angle = (e) => {
      const r = rose.getBoundingClientRect();
      return (Math.atan2(e.clientX - (r.left + r.width / 2), -(e.clientY - (r.top + r.height / 2))) * 180) / Math.PI;
    };
    let from = null;
    rose.classList.add("turnable");
    rose.addEventListener("pointerdown", (e) => {
      from = { angle: angle(e), bearing: north.draft };
      rose.setPointerCapture(e.pointerId);
      e.preventDefault();
    });
    rose.addEventListener("pointermove", (e) => {
      if (!from) return;
      const to = from.bearing + angle(e) - from.angle;
      setDraft(e.shiftKey ? Math.round(to / 15) * 15 : to);
    });
    const done = () => (from = null);
    rose.addEventListener("pointerup", done);
    rose.addEventListener("pointercancel", done);
  }
  setDraft(way.bearing);
  (may ? input : close).focus();
}

function closeNorth() {
  north.box?.remove();
  Object.assign(north, { box: null, draft: null, building: null, faceNorth: null });
  render();
}

async function saveNorth() {
  const id = north.building, bearing = north.draft;
  if (!id || bearing === null) return;
  try {
    const r = await request(`${BASE}/buildings/${encodeURIComponent(id)}/bearing`, { bearing });
    const ways = (state.project.buildings ??= {});
    ways[id] = { ...(ways[id] || { on_map: false, own: false }), bearing: r.bearing };
    closeNorth();
    toast(`Turned: the plan's up faces ${said(r.bearing)}`);
    if (view3d.building === id) { // the 3D view stands it as it now stands
      view3d.stale = true;
      if (view3d.shown) refresh3d();
    }
  } catch (e) {
    toast(`The building could not be turned: ${e.message}`, true);
  }
}

export function setupCompass() {
  const button = el("button", { id: "compass", type: "button", class: "compass", hidden: true }, face());
  button.addEventListener("click", () => run("building.north"));
  const top = el("button", { id: "top-view", type: "button", class: "btn-ghost", "aria-pressed": "false", "data-key": "u",
    "data-tip": "Look straight down, the plan's up at the top; again, tilt back" }, icon("scan-eye"), el("span", {}, "Top"));
  top.addEventListener("click", () => run("view.top"));
  const corner = document.querySelector(".canvas-corner");
  corner.prepend(button);
  corner.append(el("div", { id: "view3d-ctl", class: "view3d-ctl", role: "group", "aria-label": "The 3D view", hidden: true }, top));

  const in3d = () => view3d.mode === "3d" && Boolean(view3d.world);
  command({ id: "building.north", title: "North: which way the building stands, and turn it", group: "View", icon: "compass",
    words: "compass orientation bearing rotate turn building true north degrees", when: () => Boolean(state.floor),
    why: () => "Open a floor first", run: openNorth });
  command({ id: "view.top", title: "Look straight down (top view), or tilt back", group: "View", icon: "scan-eye", keys: ["u"],
    words: "top view plan above overhead bird", when: in3d, why: () => "In 3D only", idle: "in 3D only",
    run: () => view3d.world.topView(!view3d.world.top) });
  command({ id: "view.north", title: "Face north", group: "View", icon: "navigation-2", words: "compass north orientation",
    when: in3d, why: () => "In 3D only", run: () => view3d.world.turnTo(0) });

  on("viewturn", render);
  on("view", () => {
    if (north.faceNorth) north.faceNorth.hidden = view3d.mode !== "3d";
    render();
  });
  on("floor", () => {
    if (north.box && (!state.floor || buildingOf(state.floor.id) !== north.building)) closeNorth();
    render();
  });
  on("project", render);
}
