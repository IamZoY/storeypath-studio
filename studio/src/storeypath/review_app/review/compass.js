// The compass, on the canvas (bottom right, over the building's floors), in 3D and walking:
// which way north is, turning as the view turns (the world stands the building as it
// stands on the map). A click on it faces north (3D). Beside it in 3D, Top (U) looks
// straight down, the plan's up at the top as the plan has it, and tilts back. Which way a
// building stands is set where it is placed, on the project's site plan (studio.js), not here.

import { on } from "./bus.js";
import { command, run } from "./commands.js";
import { $, el, icon, svg } from "./dom.js";
import { view3d } from "./state.js";

const WINDS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];

/** A bearing as people say it: "24° NNE". */
const said = (deg) => {
  const d = ((deg % 360) + 360) % 360;
  return `${Math.round(d)}° ${WINDS[Math.round(d / 22.5) % 16]}`;
};

/** The compass's needle turned to north as the view faces, and Top as the view looks. */
function render() {
  const button = $("compass"), w = view3d.world;
  button.hidden = !view3d.shown || !w;
  $("view3d-ctl").hidden = view3d.mode !== "3d";
  if (button.hidden) return;
  const facing = w.viewBearing;
  button.querySelector(".cp-needle").setAttribute("transform", `rotate(${(-facing).toFixed(2)})`);
  const what = `North: the view faces ${said(facing)}`;
  button.dataset.tip = view3d.mode === "3d" ? `${what} · click to face north` : what;
  button.setAttribute("aria-label", view3d.mode === "3d" ? `${what}. Face north` : what);
  button.disabled = view3d.mode !== "3d";
  $("top-view").setAttribute("aria-pressed", String(Boolean(w.top)));
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

export function setupCompass() {
  const button = el("button", { id: "compass", type: "button", class: "compass", hidden: true }, face());
  button.addEventListener("click", () => run("view.north"));
  const top = el("button", { id: "top-view", type: "button", class: "btn-ghost", "aria-pressed": "false", "data-key": "u",
    "data-tip": "Look straight down, the plan's up at the top; again, tilt back" }, icon("scan-eye"), el("span", {}, "Top"));
  top.addEventListener("click", () => run("view.top"));
  const corner = document.querySelector(".canvas-corner");
  corner.prepend(button);
  corner.append(el("div", { id: "view3d-ctl", class: "view3d-ctl", role: "group", "aria-label": "The 3D view", hidden: true }, top));

  const in3d = () => view3d.mode === "3d" && Boolean(view3d.world);
  command({ id: "view.top", title: "Look straight down (top view), or tilt back", group: "View", icon: "scan-eye", keys: ["u"],
    words: "top view plan above overhead bird", when: in3d, why: () => "In 3D only", idle: "in 3D only",
    run: () => view3d.world.topView(!view3d.world.top) });
  command({ id: "view.north", title: "Face north", group: "View", icon: "compass", words: "compass north orientation",
    when: in3d, why: () => "In 3D only", run: () => view3d.world.turnTo(0) });

  on("viewturn", render);
  on("view", render);
}
