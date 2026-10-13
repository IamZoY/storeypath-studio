// An item's symbol on a plan, as the viewers draw it (viewer/svg, the engine's drawItem): its
// footprint in its type's colour, the edge it faces darker, and a mark of how it is drawn (its
// shape, fit.js shapeOf): a chair's back (an armchair's arms), a sofa's seat, a cupboard's doors,
// the way a screen faces, a copier's lid, a bed's headboard and pillows, a kiosk's screen, what a
// camera looks at; a desk and a table alone (their chairs items of their own, format 0.10); on
// the ceiling a circle (a dome camera's turned to where it looks). In the plan's metres, an item's
// front its own -y. Review's plan (review/items.js) and the Item types page (itemtypes.js) draw it.

import { shapeOf } from "./fit.js";

const SVG_NS = "http://www.w3.org/2000/svg";
const PLAIN = "#8a8a8a"; // a type with no colour of its own

function svg(tag, attrs = {}) {
  const e = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v !== undefined && v !== null) e.setAttribute(k, v);
  }
  return e;
}

// a camera's view, drawn ahead of it: this wide (degrees) and this far (metres), as the viewers draw it
const CAMERA_VIEW = { deg: 60, reach: 1.6 };

/** The mark of the other shapes, in the item's frame (its front -y, its back +y). */
function markOf(g, shape, w, d) {
  const mark = (tag, attrs, cls = "mark") => g.append(svg(tag, { class: cls, ...attrs }));
  const wedge = (reach) => `M${-w * 0.3},${-d / 2}L0,${-d / 2 - reach}L${w * 0.3},${-d / 2}`; // the way it faces
  if (shape === "chair" || shape === "side_chair" || shape === "armchair") { // its back, away from its front; an armchair's arms
    const back = Math.min(shape === "chair" ? 0.14 : 0.1, d / 4), m = Math.min(0.04, w / 10);
    mark("rect", { x: -w / 2 + m, y: d / 2 - back, width: w - 2 * m, height: back, rx: 0.04 }, "chair back");
    if (shape === "armchair") {
      const arm = Math.min(0.12, w / 6);
      for (const x of [-w / 2, w / 2 - arm]) mark("rect", { x, y: d / 2 - d * 0.8, width: arm, height: d * 0.8, rx: 0.04 }, "chair back");
    }
  } else if (shape === "storage") { // its doors along its front, and where they meet
    const t = Math.min(0.05, d / 6), doors = w > 1.4 ? 3 : w > 0.65 ? 2 : 1;
    mark("line", { x1: -w / 2, y1: -d / 2 + t, x2: w / 2, y2: -d / 2 + t });
    for (let k = 1; k < doors; k++) mark("line", { x1: -w / 2 + (k * w) / doors, y1: -d / 2, x2: -w / 2 + (k * w) / doors, y2: -d / 2 + t });
  } else if (shape === "camera") {
    mark("path", { d: cameraView(d) }, "mark view");
  } else if (shape === "sofa") { // its seat, between the arms and before the back
    const arm = Math.min(0.2, w / 6), back = Math.min(0.22, d / 3);
    mark("rect", { x: -w / 2 + arm, y: -d / 2, width: w - 2 * arm, height: d - back });
  } else if (shape === "screen") {
    mark("path", { d: wedge(Math.min(0.5, w * 0.3)) }, "mark view");
  } else if (shape === "copier") { // its lid, over its back
    const m = Math.min(0.08, w / 8, d / 8), h = (d - 2 * m) * 0.6;
    mark("rect", { x: -w / 2 + m, y: d / 2 - m - h, width: w - 2 * m, height: h });
  } else if (shape === "bed") { // its headboard, its pillows against it, where the covers turn down
    const back = d / 2 - Math.min(0.08, d / 20);
    mark("line", { x1: -w / 2, y1: back, x2: w / 2, y2: back });
    const n = w >= 1.3 ? 2 : 1, gap = 0.08, pw = (w - 0.12 - gap * (n - 1)) / n;
    for (let i = 0; i < n; i++) mark("rect", { x: -w / 2 + 0.06 + i * (pw + gap), y: back - 0.46, width: pw, height: 0.4, rx: 0.08 });
    mark("line", { x1: -w / 2, y1: back - 0.62, x2: w / 2, y2: back - 0.62 });
  } else if (shape === "kiosk") { // its screen along its front, and the way it faces
    const t = Math.min(0.08, d / 4), m = Math.min(0.06, w / 8);
    mark("rect", { x: -w / 2 + m, y: -d / 2, width: w - 2 * m, height: t }, "mark screen");
    mark("path", { d: wedge(Math.min(0.5, w * 0.6)) }, "mark view");
  }
}

/** What a camera looks at, ahead of it (its front -y): from its front edge (``from``: its
 * middle, under the ceiling) out to CAMERA_VIEW's reach, CAMERA_VIEW's degrees wide. */
function cameraView(from) {
  const half = (CAMERA_VIEW.deg / 2) * (Math.PI / 180), r = CAMERA_VIEW.reach;
  const sx = r * Math.sin(half), sy = -from / 2 - r * Math.cos(half);
  return `M0,${-from / 2}L${-sx},${sy}A${r},${r} 0 0 1 ${sx},${sy}Z`;
}

/** An item's shape on the plan (``a``: {x, y, rotation}, ``t``: its type): its footprint
 * turned with it, the edge it faces darker and the mark of how it is drawn; on the
 * ceiling, a circle (an access point's with a ring and a dot). */
export function assetShape(a, t, cls) {
  const g = svg("g", { transform: `translate(${a.x} ${a.y}) rotate(${a.rotation || 0})`, class: cls });
  const w = t?.width ?? 1, d = t?.depth ?? 0.6, shape = shapeOf(t);
  g.dataset.shape = shape;
  if (t?.mount === "ceiling") {
    const r = Math.max(w, d) / 2;
    g.classList.add("ceiling");
    g.append(svg("circle", { r, fill: t?.color || PLAIN }));
    if (shape === "access_point") {
      g.append(svg("circle", { class: "mark", r: r * 0.6 }), svg("circle", { class: "mark solid", r: r * 0.15 }));
    } else if (shape === "camera") { // a dome: turned to where it looks
      g.append(svg("path", { class: "mark view", d: cameraView(0) }));
    }
    return g;
  }
  g.append(svg("rect", { x: -w / 2, y: -d / 2, width: w, height: d, fill: t?.color || PLAIN }));
  g.append(svg("line", { x1: -w / 2, y1: -d / 2, x2: w / 2, y2: -d / 2, class: "front" })); // its front: the plan's -y, turned
  markOf(g, shape, w, d);
  return g;
}

/** What an item of type ``t`` takes on the plan, about its middle in its own frame
 * [x0, y0, x1, y1]: its symbol's box (for a preview to frame it whole): its footprint, and
 * ahead of it the way a screen or a kiosk faces, what a camera looks at. */
export function symbolBox(t) {
  const w = t?.width ?? 1, d = t?.depth ?? 0.6, shape = shapeOf(t);
  const r = t?.mount === "ceiling" ? Math.max(w, d) / 2 : 0;
  if (shape === "camera") {
    const half = (CAMERA_VIEW.deg / 2) * (Math.PI / 180), from = r ? 0 : d / 2, reach = CAMERA_VIEW.reach;
    const side = Math.max(r || w / 2, reach * Math.sin(half));
    return [-side, -(from + reach), side, r || d / 2];
  }
  if (r) return [-r, -r, r, r];
  const ahead = shape === "screen" ? Math.min(0.5, w * 0.3) : shape === "kiosk" ? Math.min(0.5, w * 0.6) : 0;
  return [-w / 2, -d / 2 - ahead, w / 2, d / 2];
}
