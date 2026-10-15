// The drawing viewer: a drawing whole, as its own program shows it, over the page. Studio
// draws it once on the server (drawview.py, its FORMAT); here its lines, fills and points
// are drawn with WebGL (three.js), its texts written by the browser over them, sharp at
// any zoom, and what Studio found in it outlined on top (``marks``: the plans Find plans
// found, the parts read as floors), each named. Two looks, remembered in this browser:
// CAD (the drawing's own colours on dark) and Print (black on white, as Studio prints it).
// A layers panel shows or hides each layer; the cursor says where it is, in metres.

import * as THREE from "three";
import { toast } from "./toast.js";

const LOOK = "storeypath.drawview.look";
const LOOKS = { cad: "CAD", print: "Print" };
const PRINT_BG = "#ffffff", PRINT_INK = "#000000";
const FONT = "Arial, Helvetica, sans-serif"; // the texts' letters (their width is fitted to the drawing's)
const MIN_TEXT_PX = 4; // a text smaller on the screen is not written
const MAX_TEXTS = 3000; // written at once at most: the largest
const ZOOM_STEP = 1.6; // a button or a key zooms by this much
const MAX_ZOOM = 5000; // as far in as this many times the main body fitted
const FIT_PAD = 24; // px around what is fitted
const UNIT_M = { mm: 0.001, cm: 0.01, m: 1, in: 0.0254, ft: 0.3048 };

const VERTEX = `precision highp float;
uniform mat4 modelViewMatrix;
uniform mat4 projectionMatrix;
uniform float size;
in vec2 position;
void main() {
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 0.0, 1.0);
  gl_PointSize = size;
}`;
const FRAGMENT = `precision highp float;
uniform vec3 color;
uniform float opacity;
out vec4 ink;
void main() { ink = vec4(color, opacity); }`;

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

const icon = (name) => {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "icon");
  svg.setAttribute("width", "16");
  svg.setAttribute("height", "16");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `/icons/lucide.svg#${name}`);
  svg.append(use);
  return svg;
};

const mb = (n) => `${(n / 1e6).toFixed(n < 1e7 ? 1 : 0)} MB`;
const many = (n, one, more = `${one}s`) => `${n.toLocaleString("en")} ${n === 1 ? one : more}`;

function look() {
  try {
    return localStorage.getItem(LOOK) === "print" ? "print" : "cad";
  } catch {
    return "cad";
  }
}

function setLook(l) {
  try {
    localStorage.setItem(LOOK, l);
  } catch {
    // (this page alone)
  }
}

/** A colour said "#rrggbb" or "#rrggbbaa" as [r, g, b] (0–1, as written: no colour space
 * of three.js's in between) and its opacity. */
function rgba(hex) {
  const n = (i) => parseInt(hex.slice(i, i + 2), 16) / 255;
  return [new THREE.Vector3(n(1), n(3), n(5)), hex.length >= 9 ? n(7) : 1];
}

// ---- the view, as Studio sends it (drawview.py's FORMAT) ---------------------------------

/** The view at ``url``, fetched (``said``: how far it has got; ``signal``: to stop) and opened. */
async function fetchView(url, said, signal) {
  const res = await fetch(url, { signal });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `${res.status} ${res.statusText}`);
  }
  const total = Number(res.headers.get("content-length")) || 0;
  const chunks = [];
  let got = 0;
  const reader = res.body.getReader();
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    got += value.length;
    said(total ? `Fetching the drawing: ${mb(got)} of ${mb(total)}` : `Fetching the drawing: ${mb(got)}`);
  }
  said("Opening it…");
  const opened = new Blob(chunks).stream().pipeThrough(new DecompressionStream("gzip"));
  return parseView(await new Response(opened).arrayBuffer());
}

/** A view's head and its arrays (each stored a byte at a time: put back together). */
function parseView(buffer) {
  const bytes = new Uint8Array(buffer);
  if (String.fromCharCode(...bytes.subarray(0, 4)) !== "SPDV") throw new Error("this is not a drawing's view");
  const n = new DataView(buffer).getUint32(4, true);
  const head = JSON.parse(new TextDecoder().decode(bytes.subarray(8, 8 + n)));
  const start = 8 + n;
  const array = (name, Kind) => {
    const [at, size] = head.arrays[name];
    return new Kind(together(bytes.subarray(start + at, start + at + size), Kind.BYTES_PER_ELEMENT).buffer);
  };
  return { head, lines: array("lines", Float32Array), fills: array("fills", Float32Array),
    points: array("points", Float32Array), texts: array("texts", Float32Array), styles: array("styles", Uint16Array) };
}

/** Bytes stored a byte at a time (every number's first byte, then every second…) as numbers
 * of ``size`` bytes again. */
function together(planes, size) {
  const n = planes.length / size, out = new Uint8Array(planes.length);
  for (let b = 0; b < size; b++) {
    const plane = planes.subarray(b * n, (b + 1) * n);
    for (let i = 0, j = b; i < n; i++, j += size) out[j] = plane[i];
  }
  return out;
}

// ---- the viewer ------------------------------------------------------------------------------

/**
 * The drawing whose view is at ``url`` (Studio.view's url), shown over the page until closed.
 * ``title``: the drawing's name. ``marks``: what to outline on it, each {label (short, on the
 * drawing), title (in the legend, and when the pointer is on it), region: [x0, y0, x1, y1] in
 * drawing units, or null (listed, not outlined)}. ``legend``: what the marks are ("Plans
 * found"). ``units``: the units it is read in (else as the drawing says), for metres.
 * ``onPick(mark)``: a mark clicked (``pickLabel``: what that does), then the viewer closes.
 * Resolves when it is closed.
 */
export function showDrawing(url, { title = "", marks = [], legend = "", units = null, onPick = null,
  pickLabel = "Choose" } = {}) {
  return new Promise((resolve) => {
    const viewer = new Viewer({ title, marks, legend, units, onPick, pickLabel, closed: resolve });
    viewer.load(url);
  });
}

class Viewer {
  constructor(o) {
    Object.assign(this, o);
    this.look = look();
    this.view = { cx: 0, cy: 0, k: 1 }; // the middle of the window (drawing units from the view's origin), px a unit
    this.pointers = new Map(); // pointer id → where it is (px in the window)
    this.hovered = null;
    this.objects = [];
    this.layerOn = [];
    this.textWidths = null; // each text's width written at 100 px (measured once, when first written)
    this.queued = false;
    this.before = document.activeElement;
    this.build();
  }

  // ---- the page's parts ------------------------------------------------------------------

  build() {
    const zoomBy = (f) => () => this.zoomAt(f, this.width / 2, this.height / 2);
    this.lookSwitch = el("div", { class: "segmented", role: "radiogroup", "aria-label": "Look" });
    this.layersButton = el("button", { type: "button", class: "btn-ghost", "aria-pressed": "false", disabled: true,
      title: "Show or hide each layer", onclick: () => this.toggleLayers() }, icon("layers"), el("span", { class: "dv-wide" }, "Layers"));
    this.fitButton = el("button", { type: "button", class: "btn-ghost", disabled: true, title: "Fit the drawing to the window (F or 0)",
      onclick: () => this.fit() }, "Fit");
    this.zoomButtons = [
      el("button", { type: "button", class: "btn-ghost btn-icon", disabled: true, title: "Zoom out (−)", "aria-label": "Zoom out",
        onclick: zoomBy(1 / ZOOM_STEP) }, icon("minus")),
      el("button", { type: "button", class: "btn-ghost btn-icon", disabled: true, title: "Zoom in (+)", "aria-label": "Zoom in",
        onclick: zoomBy(ZOOM_STEP) }, icon("plus")),
    ];
    this.glCanvas = el("canvas", { class: "dv-gl" });
    this.textCanvas = el("canvas", { class: "dv-text", "aria-hidden": "true" });
    this.loading = el("div", { class: "dv-loading" }, el("span", { class: "spinner", "aria-hidden": "true" }),
      el("span", {}, "Fetching the drawing…"));
    this.tip = el("div", { class: "dv-tip", role: "tooltip", hidden: true });
    this.legendBox = el("div", { class: "dv-legend", hidden: true });
    this.stage = el("div", { class: "dv-stage", tabindex: "-1", "aria-label": "The drawing: drag to move, scroll or pinch to zoom" },
      this.glCanvas, this.textCanvas, this.loading, this.tip, this.legendBox);
    this.layersPanel = el("aside", { class: "dv-layers", hidden: true, "aria-label": "Layers" });
    this.cursorSaid = el("span", { class: "dv-cursor" });
    this.scaleBar = el("span", { class: "dv-scale", hidden: true }, el("span", { class: "dv-scale-bar" }), el("span", {}));
    this.countsSaid = el("span", { class: "dv-counts muted" });
    this.root = el("div", { class: "dv", role: "dialog", "aria-modal": "true", "aria-label": `Drawing ${this.title}` },
      el("header", { class: "dv-bar" },
        el("div", { class: "dv-name" }, el("strong", {}, this.title || "Drawing"),
          el("span", { class: "muted small dv-wide" }, "as drawn, every layer")),
        this.lookSwitch, this.layersButton,
        el("div", { class: "dv-zoom" }, this.zoomButtons[0], this.fitButton, this.zoomButtons[1]),
        el("button", { type: "button", class: "btn-ghost btn-icon", title: "Close (Esc)", "aria-label": "Close",
          onclick: () => this.close() }, icon("x"))),
      el("div", { class: "dv-main" }, this.stage, this.layersPanel),
      el("footer", { class: "dv-status" }, this.cursorSaid, this.scaleBar, this.countsSaid));
    this.drawLookSwitch();
    this.keys = (e) => this.key(e);
    document.addEventListener("keydown", this.keys);
    this.overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden"; // (the page beneath does not scroll)
    document.body.append(this.root);
    this.stage.focus({ preventScroll: true });
  }

  drawLookSwitch() {
    this.lookSwitch.replaceChildren(...Object.entries(LOOKS).map(([l, label]) => el("button", {
      type: "button", role: "radio", "aria-checked": String(this.look === l),
      title: l === "cad" ? "The drawing's own colours, on dark, as a CAD program shows it" : "Black on white, as Studio prints it",
      onclick: () => this.setLook(l) }, label)));
  }

  setLook(l) {
    this.look = l;
    setLook(l);
    this.drawLookSwitch();
    this.paint();
    this.draw();
  }

  /** The view fetched, opened and shown; what went wrong said, and the viewer closed. */
  async load(url) {
    const say = (text) => (this.loading.lastChild.textContent = text);
    this.stopping = new AbortController(); // (closed while it comes: it stops coming)
    try {
      const v = await fetchView(url, say, this.stopping.signal);
      if (this.gone) return;
      say("Drawing it…");
      await new Promise((r) => requestAnimationFrame(r));
      this.open(v);
    } catch (e) {
      if (this.gone) return;
      toast(`The drawing could not be shown: ${e.message}`, true);
      this.close();
    }
  }

  // ---- the drawing, drawn ---------------------------------------------------------------------

  open(v) {
    this.v = v;
    const h = v.head;
    this.mpu = UNIT_M[this.units] ?? UNIT_M[h.units] ?? null;
    try {
      this.renderer = new THREE.WebGLRenderer({ canvas: this.glCanvas, antialias: true });
    } catch {
      throw new Error("this browser cannot draw with WebGL");
    }
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.scene = new THREE.Scene();
    this.camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 2);
    this.camera.position.z = 1;
    const kinds = { lines: v.lines, fills: v.fills, points: v.points };
    const dot = Math.max(2, Math.round(1.5 * this.renderer.getPixelRatio())); // a point's size, in the screen's pixels
    const order = { fills: 0, lines: 1, points: 2 }; // (fills under the lines, as a print has them)
    // where everything is, said once for all: three.js would work it out from points of three
    // numbers, and these have two
    const [x0, y0, x1, y1] = this.relative(h.bounds);
    const sphere = new THREE.Sphere(new THREE.Vector3((x0 + x1) / 2, (y0 + y1) / 2, 0), Math.hypot(x1 - x0, y1 - y0) / 2);
    h.batches.forEach((b, i) => {
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute("position", new THREE.BufferAttribute(kinds[b.kind].subarray(2 * b.first, 2 * (b.first + b.count)), 2));
      geometry.boundingSphere = sphere;
      const [color, opacity] = rgba(h.colors[b.color]);
      const material = new THREE.RawShaderMaterial({
        glslVersion: THREE.GLSL3, vertexShader: VERTEX, fragmentShader: FRAGMENT, depthTest: false, depthWrite: false,
        side: THREE.DoubleSide, // (a triangle is filled whichever way round it was made)
        transparent: opacity < 1, uniforms: { color: { value: color }, opacity: { value: opacity }, size: { value: dot } },
      });
      const Kind = b.kind === "lines" ? THREE.LineSegments : b.kind === "fills" ? THREE.Mesh : THREE.Points;
      const object = new Kind(geometry, material);
      object.frustumCulled = false; // (all of it is drawn: the GPU leaves out what is off the window)
      object.matrixAutoUpdate = false;
      object.renderOrder = order[b.kind] * 1e6 + i;
      object.userData = { batch: b, color: [color, opacity] };
      this.scene.add(object);
      this.objects.push(object);
    });
    this.layerOn = h.layers.map((l) => !l.off);
    const css = getComputedStyle(this.root), token = (name, or) => css.getPropertyValue(name).trim() || or;
    this.markInk = { line: token("--select", "#ff8a00"), text: token("--text-on-accent", "#ffffff"),
      font: `600 12px ${token("--font-ui", FONT)}` };
    this.prepareTexts();
    this.marks = this.marks.map((m) => ({ ...m, box: m.region ? [m.region[0] - h.origin[0], m.region[1] - h.origin[1],
      m.region[2] - h.origin[0], m.region[3] - h.origin[1]] : null }));
    const c = h.counts;
    this.countsSaid.textContent = [many(c.lines, "line"), c.triangles ? many(c.triangles, "filled triangle") : null,
      c.texts ? many(c.texts, "text") : null, many(c.entities, "entity", "entities")].filter(Boolean).join(" · ");
    this.loading.remove();
    for (const b of [this.layersButton, this.fitButton, ...this.zoomButtons]) b.disabled = false;
    this.drawLayers();
    this.drawLegend();
    this.listen();
    this.resized();
    this.fit();
    this.paint();
    this.draw();
  }

  /** Each batch in the look's colours, the layers shown or hidden. */
  paint() {
    if (!this.renderer) return;
    const print = this.look === "print";
    this.renderer.setClearColor(print ? PRINT_BG : this.v.head.background, 1);
    this.stage.style.background = print ? PRINT_BG : this.v.head.background;
    const [ink] = rgba(PRINT_INK);
    for (const o of this.objects) {
      const [color] = o.userData.color;
      o.material.uniforms.color.value = print ? ink : color; // (a tint stays one: grey, printed)
      o.visible = this.layerOn[o.userData.batch.layer];
    }
  }

  /** What each text needs to be written quickly: its size, its middle and how far it reaches. */
  prepareTexts() {
    const t = this.v.texts, n = t.length / 8;
    this.textCount = n;
    this.capLen = new Float32Array(n);
    this.textMid = new Float32Array(2 * n);
    this.textReach = new Float32Array(n);
    this.textWidths = new Float32Array(n).fill(NaN);
    for (let i = 0; i < n; i++) {
      const [x, y, a, b, c, d, cap, width] = t.subarray(8 * i, 8 * i + 8);
      const w = width > 0 ? width : 0.6 * cap * this.v.head.words[i].length;
      const along = Math.hypot(a, b), up = Math.hypot(c, d);
      this.capLen[i] = cap * up;
      this.textMid[2 * i] = x + (a * w + c * cap) / 2;
      this.textMid[2 * i + 1] = y + (b * w + d * cap) / 2;
      this.textReach[i] = (along * w + up * cap) / 2;
    }
    const measure = document.createElement("canvas").getContext("2d");
    measure.font = `100px ${FONT}`;
    this.measure = measure;
    this.capRatio = measure.measureText("H").actualBoundingBoxAscent / 100 || 0.716;
  }

  // ---- where the window looks -----------------------------------------------------------------

  resized() {
    const r = this.stage.getBoundingClientRect();
    this.width = Math.max(1, r.width);
    this.height = Math.max(1, r.height);
    this.dpr = Math.min(window.devicePixelRatio || 1, 2);
    this.renderer?.setSize(this.width, this.height, false);
    this.textCanvas.width = Math.round(this.width * this.dpr);
    this.textCanvas.height = Math.round(this.height * this.dpr);
    this.draw();
  }

  /** px a drawing unit that shows ``box`` (from the origin) whole. */
  fitting(box) {
    const w = Math.max(box[2] - box[0], 1e-9), h = Math.max(box[3] - box[1], 1e-9);
    const room = (n) => Math.max(n - 2 * FIT_PAD, n / 2); // (a window smaller than its margins: half of it)
    return Math.min(room(this.width) / w, room(this.height) / h);
  }

  relative(box) {
    const [ox, oy] = this.v.head.origin;
    return [box[0] - ox, box[1] - oy, box[2] - ox, box[3] - oy];
  }

  /** The main body fitted to the window: a stray far off is not (zoomed out, it is there). */
  fit(box = this.relative(this.v.head.body)) {
    const k = Math.max(this.fitting(box), 1e-12);
    Object.assign(this.view, { cx: (box[0] + box[2]) / 2, cy: (box[1] + box[3]) / 2, k });
    this.limit();
    this.draw();
  }

  /** As far out as everything fits twice over, as far in as MAX_ZOOM. */
  limit() {
    const all = this.fitting(this.relative(this.v.head.bounds)), body = this.fitting(this.relative(this.v.head.body));
    this.view.k = Math.min(Math.max(this.view.k, Math.min(all, body) / 2), body * MAX_ZOOM);
  }

  toWorld(px, py) {
    const { cx, cy, k } = this.view;
    return [cx + (px - this.width / 2) / k, cy - (py - this.height / 2) / k];
  }

  zoomAt(f, px, py) {
    const [wx, wy] = this.toWorld(px, py);
    this.view.k *= f;
    this.limit();
    const { k } = this.view;
    this.view.cx = wx - (px - this.width / 2) / k;
    this.view.cy = wy + (py - this.height / 2) / k;
    this.draw();
  }

  pan(dx, dy) {
    this.view.cx -= dx / this.view.k;
    this.view.cy += dy / this.view.k;
    this.draw();
  }

  draw() {
    if (this.queued || !this.renderer || this.gone) return;
    this.queued = true;
    requestAnimationFrame(() => {
      this.queued = false;
      if (this.gone) return;
      const { cx, cy, k } = this.view, w = this.width / 2 / k, h = this.height / 2 / k;
      Object.assign(this.camera, { left: -w, right: w, top: h, bottom: -h });
      this.camera.position.set(cx, cy, 1);
      this.camera.updateProjectionMatrix();
      this.camera.updateMatrixWorld();
      this.renderer.render(this.scene, this.camera);
      this.drawOver();
      this.drawScale();
    });
  }

  /** The texts, and the marks over everything. */
  drawOver() {
    const ctx = this.textCanvas.getContext("2d");
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, this.textCanvas.width, this.textCanvas.height);
    this.drawTexts(ctx);
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    this.drawMarks(ctx);
  }

  drawTexts(ctx) {
    const { cx, cy, k } = this.view, dpr = this.dpr;
    const halfW = this.width / 2 / k, halfH = this.height / 2 / k;
    const t = this.v.texts, styles = this.v.styles, words = this.v.head.words, colors = this.v.head.colors;
    const shown = [];
    for (let i = 0; i < this.textCount; i++) {
      if (k * this.capLen[i] < MIN_TEXT_PX || !this.layerOn[styles[2 * i]]) continue;
      const r = this.textReach[i];
      if (Math.abs(this.textMid[2 * i] - cx) > halfW + r || Math.abs(this.textMid[2 * i + 1] - cy) > halfH + r) continue;
      shown.push(i);
    }
    if (shown.length > MAX_TEXTS) {
      shown.sort((p, q) => this.capLen[q] - this.capLen[p]);
      shown.length = MAX_TEXTS;
    }
    const size = (i) => Math.max(1, Math.min(2000, Math.round(k * this.capLen[i] / this.capRatio)));
    shown.sort((p, q) => size(p) - size(q)); // (a font set once for all of a size)
    const print = this.look === "print";
    ctx.textBaseline = "alphabetic";
    let font = 0, ink = "";
    for (const i of shown) {
      const [x, y, a, b, c, d, cap, width] = t.subarray(8 * i, 8 * i + 8);
      const f = size(i);
      if (f !== font) ctx.font = `${(font = f)}px ${FONT}`;
      const colour = print ? PRINT_INK : colors[styles[2 * i + 1]];
      if (colour !== ink) ctx.fillStyle = ink = colour;
      if (Number.isNaN(this.textWidths[i])) this.textWidths[i] = this.measure.measureText(words[i]).width;
      const w100 = this.textWidths[i];
      // the browser's letters as wide as the drawing sets them (its font is not the drawing's)
      const sx = width > 0 && w100 > 0 ? Math.min(4, Math.max(0.25, (width * 100 * this.capRatio) / (w100 * cap))) : 1;
      const g = cap / (this.capRatio * f); // drawing units a px of the letters
      ctx.setTransform(dpr * k * a * g * sx, -dpr * k * b * g * sx, -dpr * k * c * g, dpr * k * d * g,
        dpr * (k * (x - cx) + this.width / 2), dpr * (this.height / 2 - k * (y - cy)));
      ctx.fillText(words[i], 0, 0);
    }
  }

  drawMarks(ctx) {
    if (!this.marks.some((m) => m.box)) return;
    const { cx, cy, k } = this.view, W = this.width, H = this.height;
    const colour = this.markInk.line;
    ctx.font = this.markInk.font;
    ctx.textBaseline = "middle";
    for (const m of this.marks) {
      if (!m.box) continue;
      const x0 = W / 2 + (m.box[0] - cx) * k, x1 = W / 2 + (m.box[2] - cx) * k;
      const y0 = H / 2 - (m.box[3] - cy) * k, y1 = H / 2 - (m.box[1] - cy) * k;
      if (x1 < 0 || x0 > W || y1 < 0 || y0 > H) continue;
      const on = m === this.hovered;
      if (on) {
        ctx.fillStyle = colour;
        ctx.globalAlpha = 0.12;
        ctx.fillRect(x0, y0, x1 - x0, y1 - y0);
        ctx.globalAlpha = 1;
      }
      ctx.strokeStyle = colour;
      ctx.lineWidth = on ? 3 : 2;
      ctx.strokeRect(x0, y0, x1 - x0, y1 - y0);
      const label = m.label.length > 40 ? `${m.label.slice(0, 39)}…` : m.label;
      const lw = ctx.measureText(label).width + 12;
      const lx = Math.min(Math.max(x0, 4), W - lw - 4), ly = Math.min(Math.max(y0, 4), H - 24);
      ctx.fillStyle = colour;
      ctx.beginPath();
      ctx.roundRect(lx, ly, lw, 20, 4);
      ctx.fill();
      ctx.fillStyle = this.markInk.text;
      ctx.fillText(label, lx + 6, ly + 10.5);
    }
  }

  /** A bar 80 to 200 px long, a round length in metres. */
  drawScale() {
    if (!this.mpu) return;
    const want = 80 / this.view.k * this.mpu, p = 10 ** Math.floor(Math.log10(want));
    const metres = [1, 2, 5, 10].find((n) => n * p >= want) * p;
    this.scaleBar.hidden = false;
    this.scaleBar.firstChild.style.width = `${Math.round(metres / this.mpu * this.view.k)}px`;
    this.scaleBar.lastChild.textContent = metres >= 1000 ? `${(metres / 1000).toLocaleString("en")} km`
      : metres >= 1 ? `${metres.toLocaleString("en")} m` : `${Math.round(metres * 1000).toLocaleString("en")} mm`;
  }

  // ---- the layers, and the marks' legend ---------------------------------------------------------

  toggleLayers(open = this.layersPanel.hidden) {
    this.layersPanel.hidden = !open;
    this.layersButton.setAttribute("aria-pressed", String(open));
    requestAnimationFrame(() => this.resized());
  }

  drawLayers() {
    const h = this.v.head;
    const boxes = [];
    const list = el("ul", { class: "dv-layer-list" }, h.layers.map((l, i) => {
      const box = el("input", { type: "checkbox" });
      box.checked = this.layerOn[i];
      box.addEventListener("change", () => this.showLayer(i, box.checked));
      boxes.push(box);
      return el("li", { "data-name": l.name.toLowerCase() }, el("label", { title: l.off ? `${l.name}: off in the drawing` : l.name },
        box, el("span", { class: "swatch", style: `background: ${l.color || "#888"}` }),
        el("span", { class: "dv-layer-name" }, l.name, l.off ? el("span", { class: "muted small" }, " · off in the drawing") : null),
        el("span", { class: "muted small dv-layer-count", title: "Things drawn on it" }, l.count.toLocaleString("en"))));
    }));
    const all = (on) => {
      h.layers.forEach((_, i) => (this.layerOn[i] = on));
      boxes.forEach((b) => (b.checked = on));
      this.paint();
      this.draw();
    };
    const find = el("input", { type: "search", placeholder: "Find a layer", "aria-label": "Find a layer" });
    find.addEventListener("input", () => {
      const q = find.value.trim().toLowerCase();
      for (const li of list.children) li.hidden = Boolean(q) && !li.dataset.name.includes(q);
    });
    this.layersPanel.replaceChildren(
      el("div", { class: "dv-layers-head" }, el("strong", {}, "Layers"), el("span", { class: "muted small grow" }, String(h.layers.length)),
        el("button", { type: "button", class: "btn-sm", onclick: () => all(true) }, "All"),
        el("button", { type: "button", class: "btn-sm", onclick: () => all(false) }, "None"),
        el("button", { type: "button", class: "btn-ghost btn-sm btn-icon", title: "Close", "aria-label": "Close the layers",
          onclick: () => this.toggleLayers(false) }, icon("x"))),
      h.layers.length > 12 ? find : null,
      list,
      h.empty_layers ? el("p", { class: "muted small" }, `and ${many(h.empty_layers, "layer")} with nothing on them`) : null);
  }

  showLayer(i, on) {
    this.layerOn[i] = on;
    this.paint();
    this.draw();
  }

  drawLegend() {
    if (!this.marks.length) return;
    const narrow = window.matchMedia("(max-width: 720px)").matches;
    const list = el("ol", { class: "dv-legend-list", hidden: narrow }, this.marks.map((m) => el("li", {},
      el("button", { type: "button", class: "dv-legend-go", disabled: !m.box, title: m.box ? "Show it" : "The whole drawing",
        onclick: () => m.box && this.fit(m.box) },
        el("span", { class: "dv-mark" }, m.label), el("span", { class: "dv-legend-title" }, m.title || "")),
      this.onPick ? el("button", { type: "button", class: "btn-sm", title: pickTitle(this.pickLabel),
        onclick: () => this.pick(m) }, this.pickLabel) : null)));
    const head = el("button", { type: "button", class: "dv-legend-head", "aria-expanded": String(!narrow),
      onclick: () => {
        list.hidden = !list.hidden;
        head.setAttribute("aria-expanded", String(!list.hidden));
      } }, el("strong", {}, this.legend || "Marked"), el("span", { class: "muted small" }, ` ${this.marks.length}`), icon("chevron-up"));
    this.legendBox.replaceChildren(head, list);
    this.legendBox.hidden = false;
  }

  pick(m) {
    this.close(); // (first: the page beneath scrolls again, to what was chosen)
    this.onPick?.(m);
  }

  // ---- pointer, wheel and keys -------------------------------------------------------------------

  listen() {
    const s = this.stage;
    const at = (e) => {
      const r = s.getBoundingClientRect();
      return [e.clientX - r.left, e.clientY - r.top];
    };
    s.addEventListener("pointerdown", (e) => {
      if (e.target.closest(".dv-legend")) return;
      this.tip.hidden = true;
      s.setPointerCapture(e.pointerId);
      this.pointers.set(e.pointerId, at(e));
      this.downAt = this.pointers.size === 1 ? at(e) : null;
      this.moved = false;
    });
    s.addEventListener("pointermove", (e) => {
      const p = at(e);
      this.said(p);
      const before = this.pointers.get(e.pointerId);
      if (!before) return this.hover(p);
      if (this.downAt && Math.hypot(p[0] - this.downAt[0], p[1] - this.downAt[1]) > 4) this.moved = true;
      if (this.pointers.size === 2) { // pinched: zoomed about the middle, moved with it
        const [other] = [...this.pointers].filter(([id]) => id !== e.pointerId).map(([, q]) => q);
        const was = Math.hypot(before[0] - other[0], before[1] - other[1]);
        const now = Math.hypot(p[0] - other[0], p[1] - other[1]);
        this.pan((p[0] - before[0]) / 2, (p[1] - before[1]) / 2);
        if (was > 0) this.zoomAt(now / was, (p[0] + other[0]) / 2, (p[1] + other[1]) / 2);
        this.moved = true;
      } else this.pan(p[0] - before[0], p[1] - before[1]);
      this.pointers.set(e.pointerId, p);
    });
    const up = (e) => {
      if (!this.pointers.has(e.pointerId)) return;
      this.pointers.delete(e.pointerId);
      if (e.type === "pointerup" && !this.moved && this.downAt && !this.pointers.size) this.click(at(e));
      if (!this.pointers.size) this.downAt = null;
    };
    s.addEventListener("pointerup", up);
    s.addEventListener("pointercancel", up);
    s.addEventListener("pointerleave", () => {
      if (!this.pointers.size) {
        this.hover(null);
        this.cursorSaid.textContent = "";
      }
    });
    s.addEventListener("wheel", (e) => {
      if (e.target.closest(".dv-legend")) return;
      e.preventDefault();
      const lines = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? this.height : 1;
      const [px, py] = at(e);
      this.zoomAt(Math.exp(-e.deltaY * lines * 0.0015), px, py);
    }, { passive: false });
    s.addEventListener("dblclick", (e) => {
      if (e.target.closest(".dv-legend")) return;
      const [px, py] = at(e);
      this.zoomAt(ZOOM_STEP, px, py);
    });
    this.watch = new ResizeObserver(() => this.resized());
    this.watch.observe(s);
    this.glCanvas.addEventListener("webglcontextlost", () => {
      if (!this.gone) toast("The browser stopped drawing this drawing (it ran out of graphics memory): close it and open it again", true);
    });
  }

  /** The mark under the pointer (the smallest, where they overlap), outlined and said. */
  markAt(p) {
    if (!p) return null;
    const [wx, wy] = this.toWorld(...p);
    const under = this.marks.filter((m) => m.box && wx >= m.box[0] && wx <= m.box[2] && wy >= m.box[1] && wy <= m.box[3]);
    const area = (m) => (m.box[2] - m.box[0]) * (m.box[3] - m.box[1]);
    return under.sort((a, b) => area(a) - area(b))[0] || null;
  }

  hover(p) {
    const m = this.markAt(p);
    if (m !== this.hovered) {
      this.hovered = m;
      this.draw();
    }
    this.stage.classList.toggle("picking", Boolean(m && this.onPick));
    this.tip.hidden = !m;
    if (!m) return;
    const said = m.title && m.title !== m.label ? `${m.label} · ${m.title}` : m.label;
    this.tip.replaceChildren(el("strong", {}, said),
      this.onPick ? el("span", { class: "muted small" }, `Click: ${this.pickLabel.toLowerCase()}`) : null);
    const r = this.tip.getBoundingClientRect();
    this.tip.style.left = `${Math.min(p[0] + 14, this.width - r.width - 8)}px`;
    this.tip.style.top = `${Math.min(p[1] + 16, this.height - r.height - 8)}px`;
  }

  click(p) {
    const m = this.markAt(p);
    if (m && this.onPick) this.pick(m);
  }

  /** Where the pointer is, in metres (in drawing units when they are not known). */
  said(p) {
    if (!this.v) return;
    const [wx, wy] = this.toWorld(...p);
    const x = wx + this.v.head.origin[0], y = wy + this.v.head.origin[1];
    this.cursorSaid.textContent = this.mpu
      ? `x ${(x * this.mpu).toFixed(2)} m · y ${(y * this.mpu).toFixed(2)} m`
      : `x ${x.toFixed(1)} · y ${y.toFixed(1)} (drawing units)`;
  }

  key(e) {
    if (e.key === "Escape") {
      e.preventDefault();
      this.close();
      return;
    }
    if (!this.v || e.target.closest?.("input, select, textarea") || e.metaKey || e.ctrlKey || e.altKey) return;
    const step = { ArrowLeft: [1, 0], ArrowRight: [-1, 0], ArrowUp: [0, 1], ArrowDown: [0, -1] }[e.key];
    if (e.key === "+" || e.key === "=") this.zoomAt(ZOOM_STEP, this.width / 2, this.height / 2);
    else if (e.key === "-" || e.key === "_") this.zoomAt(1 / ZOOM_STEP, this.width / 2, this.height / 2);
    else if (e.key === "0" || e.key === "f" || e.key === "F") this.fit();
    else if (step) this.pan(step[0] * this.width / 8, step[1] * this.height / 8);
    else return;
    e.preventDefault();
  }

  close() {
    if (this.gone) return;
    this.gone = true;
    document.removeEventListener("keydown", this.keys);
    this.stopping?.abort();
    this.watch?.disconnect();
    for (const o of this.objects) {
      o.geometry.dispose();
      o.material.dispose();
    }
    if (this.renderer) {
      this.renderer.dispose();
      this.renderer.forceContextLoss(); // (a big drawing's lines: the GPU's memory given back now)
    }
    this.root.remove();
    document.body.style.overflow = this.overflow;
    this.before?.focus?.({ preventScroll: true });
    this.closed();
  }
}

const pickTitle = (label) => `${label}, and close the drawing`;
