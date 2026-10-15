// The drawing viewer: a drawing whole, as its own program shows it, over the page. Studio
// draws it once on the server (drawview.py); the viewer's DrawingView
// (/viewer/src/drawing/drawing.js) draws it here with WebGL, its texts sharp at any zoom,
// and outlines what Studio found in it (``marks``: the plans Find plans found, the parts
// read as floors), each named. This is its dialog round it: the look (CAD, the drawing's
// own colours on dark, or Print, black on white as Studio prints it; remembered in this
// browser), a layers panel to show or hide each layer, Fit and zoom, the marks' legend,
// and the cursor said in metres.

import { DrawingView, LOOKS, fetchDrawingView } from "/viewer/src/drawing/drawing.js";
import { toast } from "./toast.js";

const LOOK = "storeypath.drawview.look";
const LOOK_NAMES = { cad: "CAD", print: "Print" };

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

function keepLook(l) {
  try {
    localStorage.setItem(LOOK, l);
  } catch {
    // (this page alone)
  }
}

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
    this.before = document.activeElement;
    this.build();
  }

  // ---- the dialog's parts ------------------------------------------------------------------

  build() {
    const zoomBy = (f) => () => this.view.zoomBy(f);
    this.lookSwitch = el("div", { class: "segmented", role: "radiogroup", "aria-label": "Look" });
    this.layersButton = el("button", { type: "button", class: "btn-ghost", "aria-pressed": "false", disabled: true,
      title: "Show or hide each layer", onclick: () => this.toggleLayers() }, icon("layers"), el("span", { class: "dv-wide" }, "Layers"));
    this.fitButton = el("button", { type: "button", class: "btn-ghost", disabled: true, title: "Fit the drawing to the window (F or 0)",
      onclick: () => this.view.fit() }, "Fit");
    this.zoomButtons = [
      el("button", { type: "button", class: "btn-ghost btn-icon", disabled: true, title: "Zoom out (−)", "aria-label": "Zoom out",
        onclick: zoomBy(1 / 1.6) }, icon("minus")),
      el("button", { type: "button", class: "btn-ghost btn-icon", disabled: true, title: "Zoom in (+)", "aria-label": "Zoom in",
        onclick: zoomBy(1.6) }, icon("plus")),
    ];
    this.loading = el("div", { class: "dv-loading" }, el("span", { class: "spinner", "aria-hidden": "true" }),
      el("span", {}, "Fetching the drawing…"));
    this.legendBox = el("div", { class: "dv-legend", hidden: true });
    this.stage = el("div", { class: "dv-stage" });
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
    this.overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden"; // (the page beneath does not scroll)
    document.body.append(this.root);
    // the viewer's drawing view in the stage; the legend and what it says while it comes, over it
    this.view = new DrawingView(this.stage, { look: look(), units: this.units, marks: this.marks, pickLabel: this.pickLabel,
      onPick: this.onPick ? (m) => this.pick(m) : null, label: "The drawing: drag to move, scroll or pinch to zoom" });
    this.stage.append(this.loading, this.legendBox);
    this.view.addEventListener("cursor", (e) => this.said(e.detail));
    this.view.addEventListener("camerachange", () => this.drawScale());
    this.view.addEventListener("contextlost", () => {
      toast("The browser stopped drawing this drawing (it ran out of graphics memory): close it and open it again", true);
    });
    this.drawLookSwitch();
    this.keys = (e) => this.key(e);
    document.addEventListener("keydown", this.keys);
    this.view.root.focus({ preventScroll: true });
  }

  drawLookSwitch() {
    this.lookSwitch.replaceChildren(...LOOKS.map((l) => el("button", {
      type: "button", role: "radio", "aria-checked": String(this.view.look === l),
      title: l === "cad" ? "The drawing's own colours, on dark, as a CAD program shows it" : "Black on white, as Studio prints it",
      onclick: () => this.setLook(l) }, LOOK_NAMES[l])));
  }

  setLook(l) {
    this.view.setLook(l);
    keepLook(l);
    this.drawLookSwitch();
  }

  /** The view fetched, opened and shown; what went wrong said, and the viewer closed. */
  async load(url) {
    const say = (text) => (this.loading.lastChild.textContent = text);
    this.stopping = new AbortController(); // (closed while it comes: it stops coming)
    try {
      const v = await fetchDrawingView(url, { signal: this.stopping.signal, onProgress: (got, total) => {
        say(total && got >= total ? "Opening it…"
          : total ? `Fetching the drawing: ${mb(got)} of ${mb(total)}` : `Fetching the drawing: ${mb(got)}`);
      } });
      if (this.gone) return;
      say("Drawing it…");
      await this.view.open(v);
      if (this.gone) return;
      this.opened();
    } catch (e) {
      if (this.gone) return;
      toast(`The drawing could not be shown: ${e.message}`, true);
      this.close();
    }
  }

  opened() {
    const c = this.view.head.counts;
    this.countsSaid.textContent = [many(c.lines, "line"), c.triangles ? many(c.triangles, "filled triangle") : null,
      c.texts ? many(c.texts, "text") : null, many(c.entities, "entity", "entities")].filter(Boolean).join(" · ");
    this.loading.remove();
    for (const b of [this.layersButton, this.fitButton, ...this.zoomButtons]) b.disabled = false;
    this.drawLayers();
    this.drawLegend();
    this.drawScale();
  }

  /** A bar 80 to 200 px long, a round length in metres. */
  drawScale() {
    const s = this.view.scale(80);
    if (!s) return;
    this.scaleBar.hidden = false;
    this.scaleBar.firstChild.style.width = `${s.px}px`;
    this.scaleBar.lastChild.textContent = s.label;
  }

  // ---- the layers, and the marks' legend ---------------------------------------------------------

  toggleLayers(open = this.layersPanel.hidden) {
    this.layersPanel.hidden = !open;
    this.layersButton.setAttribute("aria-pressed", String(open));
  }

  drawLayers() {
    const h = this.view.head;
    const boxes = [];
    const list = el("ul", { class: "dv-layer-list" }, this.view.layers.map((l, i) => {
      const box = el("input", { type: "checkbox" });
      box.checked = l.shown;
      box.addEventListener("change", () => this.view.setLayer(i, box.checked));
      boxes.push(box);
      return el("li", { "data-name": l.name.toLowerCase() }, el("label", { title: l.off ? `${l.name}: off in the drawing` : l.name },
        box, el("span", { class: "swatch", style: `background: ${l.color || "#888"}` }),
        el("span", { class: "dv-layer-name" }, l.name, l.off ? el("span", { class: "muted small" }, " · off in the drawing") : null),
        el("span", { class: "muted small dv-layer-count", title: "Things drawn on it" }, l.count.toLocaleString("en"))));
    }));
    const all = (on) => {
      this.view.setLayers(on);
      boxes.forEach((b) => (b.checked = on));
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

  drawLegend() {
    if (!this.marks.length) return;
    const narrow = window.matchMedia("(max-width: 720px)").matches;
    const list = el("ol", { class: "dv-legend-list", hidden: narrow }, this.marks.map((m) => el("li", {},
      el("button", { type: "button", class: "dv-legend-go", disabled: !m.region, title: m.region ? "Show it" : "The whole drawing",
        onclick: () => this.view.fitMark(m) },
        el("span", { class: "dv-mark" }, m.label), el("span", { class: "dv-legend-title" }, m.title || "")),
      this.onPick ? el("button", { type: "button", class: "btn-sm", title: `${this.pickLabel}, and close the drawing`,
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

  // ---- what it says, and keys ---------------------------------------------------------------------

  /** Where the pointer is, in metres (in drawing units when they are not known). */
  said(at) {
    if (!at) {
      this.cursorSaid.textContent = "";
      return;
    }
    this.cursorSaid.textContent = at.metres
      ? `x ${at.metres[0].toFixed(2)} m · y ${at.metres[1].toFixed(2)} m`
      : `x ${at.x.toFixed(1)} · y ${at.y.toFixed(1)} (drawing units)`;
  }

  /** Esc closes it; the drawing's own keys (+, -, 0, F, the arrows) work wherever the focus is in it. */
  key(e) {
    if (e.key === "Escape") {
      e.preventDefault();
      this.close();
      return;
    }
    this.view.key(e);
  }

  close() {
    if (this.gone) return;
    this.gone = true;
    document.removeEventListener("keydown", this.keys);
    this.stopping?.abort();
    this.view.destroy();
    this.root.remove();
    document.body.style.overflow = this.overflow;
    this.before?.focus?.({ preventScroll: true });
    this.closed();
  }
}
