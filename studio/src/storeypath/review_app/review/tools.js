// The tools, in one registry: what each is called, its icon and key, the views it works in,
// what the person must be allowed (to change the floor; the floor's drawing), what the
// status bar says while it is in use, its options (shown over the canvas while it is in
// use), and what it does with the pointer. The rail on the left is drawn from it; each
// tool is a command too (commands.js), so the palette and the keys find it. Tools of one
// family share a place on the rail (as a drawing program's do): the one used last shows,
// and holding it, right-clicking it, its corner mark or a click on it in use opens the
// family's other tools beside it.
//
//   tool({
//     id: "wall", label: "Wall", icon: "brick-wall", key: "w",
//     group: "draw",                    the rail's group (navigate, draw, place, measure, share)
//     family: "lines",                  tools sharing its place on the rail (none: its own)
//     views: ["2d"],                    where it works ("2d", "3d", "walk")
//     edits: true,                      only for who may change the floor
//     drawing: true,                    only on a floor with its drawing
//     keepsSelection: false,            what is chosen stays chosen when it starts
//     hint: (view) => "…",              what the status bar says while it is in use
//     options: () => [elements],        its options bar (null: none)
//     start(), stop(),                  when it starts and stops being the tool
//     escape: () => bool,               Esc: undo what is under way (true), else the tool stops
//     plan: { hover, click, dblclick, down, move, up },   the pointer on the plan (plan metres)
//     keys: { enter: [fn, "what it does"], … },          its own keys while in use
//     action: () => {},                 a tool that does something at once (not a mode)
//   })

import { editable, whyNotEditable } from "./access.js";
import { emit, on } from "./bus.js";
import { command } from "./commands.js";
import { $, el, icon } from "./dom.js";
import { bind, keyNow, keyText } from "./keys.js";
import { readable, state, view3d } from "./state.js";
import { hideTooltip } from "./tooltip.js";

const tools = new Map();
const VIEW_NAMES = { "2d": "2D", "3d": "3D", walk: "Walk" };
const GROUPS = ["navigate", "draw", "place", "measure", "share"];
let starting = false;
const HOLD_MS = 320; // a press held this long on a family's place opens its tools
const shownOf = new Map(); // a family → the tool of it its place on the rail shows (the one used last)
let flyout = null; // a family's tools, opened beside its place: { el, family, button }

export function tool(spec) {
  const t = { group: "draw", views: ["2d"], edits: false, drawing: false, keepsSelection: false, ...spec };
  tools.set(t.id, t);
  for (const [chord, spec] of Object.entries(t.keys || {})) {
    const [fn, what] = Array.isArray(spec) ? spec : [spec, chord];
    command({ id: `tool.${t.id}.${chord}`, title: `${t.label}: ${what}`, group: "While drawing", palette: false,
      scope: `tool:${t.id}`, keys: [chord], run: fn });
  }
  command({
    id: `tool.${t.id}`, title: t.action ? t.label : `${t.label} tool`, group: "Tools", icon: t.icon,
    keys: t.key ? [t.key] : [], words: t.words || "",
    when: () => !blocked(t, { anyView: true }),
    why: () => blocked(t, { anyView: true }),
    works: () => Boolean(t.action) || t.views.includes(view3d.mode),
    run: (e) => useTool(t.id, e),
  });
  return t;
}

export const getTool = (id) => tools.get(id || "select") || null;
export const allTools = () => [...tools.values()];
export const activeId = () => state.tool || "select";
export const activeTool = () => tools.get(activeId());

/** Why a tool cannot be used now ("" when it can); ``anyView``: wherever it works. */
export function blocked(t, { anyView = false } = {}) {
  if (typeof t === "string") t = tools.get(t);
  if (!t) return "No such tool";
  if (!state.floor) return "Open a floor first";
  if (!anyView && !t.views.includes(view3d.mode)) {
    return t.wrongView?.(view3d.mode) || `Works in ${t.views.map((v) => VIEW_NAMES[v]).join(" and ")}`;
  }
  if (t.edits && !editable()) return whyNotEditable();
  if (t.drawing && !readable()) return "This floor has no drawing yet: add its drawing to change its walls and openings";
  if (t.available) return t.available() || "";
  return "";
}

/** A tool chosen from its button, the palette or its key. In a view it does not work in,
 * its key changes nothing (a letter never switches the view: only 2, 3 and 4 do) and the
 * status bar says why, and that 2 shows the place under the pointer on the plan; the
 * palette (asked for by name) takes the plan first, with the tool. */
function useTool(id, e) {
  const t = tools.get(id);
  if (t.action) return t.action(e);
  if (!t.views.includes(view3d.mode)) {
    if (e instanceof KeyboardEvent) {
      const why = t.wrongView?.(view3d.mode) || `${t.label} works in ${t.views.map((v) => VIEW_NAMES[v]).join(" and ")}`;
      emit("tool-refused", t.views.includes("2d") ? `${why} · press 2 to see this place on the plan` : why);
      return true;
    }
    if (t.views.includes("2d")) return emit("draw-here", { tool: id, centre: true });
    return false;
  }
  setTool(id);
  return true;
}

/** ``id`` the tool in use (null or "select": choosing). A tool that cannot be used now is
 * not taken (its reason is the status bar's). */
export function setTool(id) {
  id = id && id !== "select" ? id : null;
  const next = id ? tools.get(id) : null;
  if (id && !next) return;
  if (next) {
    const why = blocked(next);
    if (why) {
      emit("tool-refused", why);
      return;
    }
  }
  if (next?.action) return next.action();
  const was = getTool(state.tool);
  if (starting) return;
  starting = true;
  try {
    if (state.tool !== id) was?.stop?.();
    state.tool = id;
    if (next?.family && shownOf.get(next.family) !== id) {
      shownOf.set(next.family, id);
      showFace(next.family);
    }
    state.wallStart = null;
    state.corners = [];
    $("map").classList.toggle("drawing-tool", Boolean(next) && next.cursor !== "default" && id !== "pan");
    $("map").dataset.tool = id || "select";
    if (next && !next.keepsSelection) emit("clear-selection");
    next?.start?.();
  } finally {
    starting = false;
  }
  emit("tool");
}

/** Esc in a tool: what is under way undone (a corner, a measure), else the tool stops. */
export function escapeTool() {
  const t = getTool(state.tool);
  if (!state.tool) return false;
  if (t?.escape?.()) return true;
  setTool(null);
  return true;
}

// ---- the rail ----------------------------------------------------------------------------

const family = (name) => allTools().filter((t) => t.family === name);
const face = (name) => tools.get(shownOf.get(name)) || family(name)[0];

function railButton(t, kin = null) {
  const b = el("button", { type: "button", "aria-label": t.label, "aria-pressed": t.action ? null : "false" });
  dress(b, t, kin);
  let held = 0;
  b.addEventListener("click", (e) => {
    if (b.dataset.opened === "1") { // (held open: the press's click is not the tool's)
      delete b.dataset.opened;
      return;
    }
    if (kin && (e.target.closest(".rail-more") || activeId() === b.dataset.tool || b.getAttribute("aria-disabled") === "true")) {
      return flyout?.button === b ? closeFlyout() : openFlyout(b, kin);
    }
    if (b.getAttribute("aria-disabled") === "true") return;
    useTool(b.dataset.tool, e);
  });
  if (kin) {
    b.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      clearTimeout(held);
      held = setTimeout(() => {
        b.dataset.opened = "1";
        openFlyout(b, kin);
      }, HOLD_MS);
    });
    for (const ended of ["pointerup", "pointerleave", "pointercancel"]) b.addEventListener(ended, () => clearTimeout(held));
    b.addEventListener("contextmenu", (e) => {
      e.preventDefault();
      openFlyout(b, kin);
    });
    b.addEventListener("keydown", (e) => { // the keyboard's way in: ↓ or → opens it
      if (e.key === "ArrowDown" || e.key === "ArrowRight") {
        e.preventDefault();
        openFlyout(b, kin, { focus: true });
      }
    });
  }
  return b;
}

/** A rail button as tool ``t`` (its family's place: ``kin``). */
function dress(b, t, kin) {
  const more = kin ? family(kin).filter((o) => o !== t) : [];
  b.dataset.tool = t.id;
  b.setAttribute("aria-label", t.label);
  b.dataset.tip = more.length ? `${t.label} · hold for ${more.map((o) => o.label.toLowerCase()).join(", ")}` : t.label;
  b.dataset.key = t.key || "";
  if (kin) {
    b.dataset.family = kin;
    b.setAttribute("aria-haspopup", "menu");
    b.setAttribute("aria-expanded", String(flyout?.button === b));
  }
  b.replaceChildren(icon(t.icon, { size: 19 }), t.key ? el("span", { class: "rail-key", "aria-hidden": "true" }, keyText(t.key)) : null,
    more.length ? el("span", { class: "rail-more", "aria-hidden": "true" }) : null);
}

/** A family's place on the rail as the tool of it used last. */
function showFace(kin) {
  const b = document.querySelector(`#rail [data-family="${kin}"]`);
  if (!b) return;
  dress(b, face(kin), kin);
  markRail();
}

export function renderRail() {
  const rail = $("rail");
  closeFlyout();
  const parts = [];
  for (const group of GROUPS) {
    const list = allTools().filter((t) => t.group === group);
    if (!list.length) continue;
    if (parts.length) parts.push(el("div", { class: "rail-sep", role: "separator" }));
    const placed = new Set();
    for (const t of list) {
      if (!t.family) parts.push(railButton(t));
      else if (!placed.has(t.family)) {
        placed.add(t.family);
        parts.push(railButton(face(t.family), t.family));
      }
    }
  }
  parts.push(el("div", { class: "rail-spacer" }));
  const help = el("button", { type: "button", id: "keys-help", "aria-label": "Keyboard shortcuts", "data-tip": "Keyboard shortcuts", "data-key": "?" },
    icon("keyboard", { size: 19 }));
  help.addEventListener("click", () => emit("keys-help"));
  parts.push(help);
  rail.replaceChildren(...parts);
  markRail();
}

/** A family's tools, beside its place on the rail: each with its key, the one in use
 * ticked, those that cannot be used now dimmed and saying why. A choice takes the tool
 * (and the place shows it); Esc, a click elsewhere or the place again puts it away. */
function openFlyout(b, kin, { focus = false } = {}) {
  closeFlyout();
  hideTooltip();
  const list = el("div", { class: "menu rail-flyout", role: "menu", "aria-label": `${face(kin).label} and others like it` });
  for (const t of family(kin)) {
    const why = blocked(t);
    const item = el("button", { type: "button", role: "menuitemradio", class: "menu-item", "aria-checked": String(activeId() === t.id),
      "data-tool": t.id }, icon(t.icon, { size: 15 }), el("span", { class: "label" }, t.label),
    why ? el("span", { class: "why" }, why) : t.key ? el("span", { class: "key" }, keyText(t.key)) : null);
    if (why) item.setAttribute("aria-disabled", "true");
    item.addEventListener("click", () => {
      closeFlyout();
      if (why) return emit("tool-refused", why);
      useTool(t.id);
    });
    list.append(item);
  }
  list.addEventListener("keydown", (e) => {
    const items = [...list.querySelectorAll(".menu-item")], at = items.indexOf(document.activeElement);
    if (e.key === "ArrowDown" || e.key === "ArrowUp") items[(at + (e.key === "ArrowDown" ? 1 : items.length - 1)) % items.length]?.focus();
    else if (e.key === "ArrowLeft") closeFlyout({ refocus: true });
    else if (e.key === "Tab") closeFlyout();
    else return;
    e.preventDefault(); // (the page's keys do not hear it)
  });
  document.body.append(list);
  const r = b.getBoundingClientRect(), m = list.getBoundingClientRect();
  list.style.left = `${Math.min(r.right + 8, innerWidth - m.width - 4)}px`;
  list.style.top = `${Math.max(4, Math.min(r.top - 4, innerHeight - m.height - 4))}px`;
  flyout = { el: list, family: kin, button: b };
  b.setAttribute("aria-expanded", "true");
  if (focus) (list.querySelector('[aria-checked="true"]') || list.querySelector(".menu-item"))?.focus();
}

function closeFlyout({ refocus = false } = {}) {
  if (!flyout) return;
  const { el: list, button } = flyout;
  flyout = null;
  list.remove();
  button.setAttribute("aria-expanded", "false");
  if (refocus) button.focus({ preventScroll: true });
}

document.addEventListener("pointerdown", (e) => {
  if (flyout && !e.target.closest?.(".rail-flyout") && !flyout.button.contains(e.target)) closeFlyout();
}, true);
document.addEventListener("keydown", (e) => { // Esc, wherever the keys are: put away first (and nothing else)
  if (flyout && e.key === "Escape" && !e.defaultPrevented) {
    e.preventDefault();
    closeFlyout({ refocus: flyout.el.contains(document.activeElement) });
  }
}, true);
window.addEventListener("blur", () => closeFlyout());
window.addEventListener("resize", () => closeFlyout());

/** The rail as things are: the tool in use pressed, those that cannot be used now dimmed
 * and saying why. */
export function markRail() {
  for (const b of document.querySelectorAll("#rail [data-tool]")) {
    const t = tools.get(b.dataset.tool);
    const why = blocked(t);
    const elsewhere = why && !t.views.includes(view3d.mode) && t.views.includes("2d") && !blocked(t, { anyView: true });
    b.setAttribute("aria-disabled", why ? "true" : "false");
    b.dataset.why = elsewhere ? `${why} · 2 shows this place on the plan` : why;
    // its key, when the key chooses it here (walking, W A S D are the walker's)
    const key = t.key && !why && keyNow(`tool.${t.id}`) ? t.key : "";
    b.dataset.key = key;
    b.querySelector(".rail-key")?.classList.toggle("off", !key);
    if (!t.action) b.setAttribute("aria-pressed", String(activeId() === t.id));
  }
}

// ---- the options bar: the tool's options, over the canvas while it is in use --------------

export function renderOptions() {
  const bar = $("tool-options");
  const t = activeTool();
  const parts = t?.options?.() || null;
  if (!state.tool || !parts) {
    bar.hidden = true;
    bar.replaceChildren();
    return;
  }
  const done = el("button", { type: "button", class: "btn-ghost btn-icon btn-sm", "aria-label": `Stop: ${t.label}`, "data-tip": "Done", "data-key": "escape" },
    icon("x", { size: 15 }));
  done.addEventListener("click", () => setTool(null));
  bar.replaceChildren(el("div", { class: "to-name" }, icon(t.icon, { size: 16 }), el("span", {}, t.label)),
    el("div", { class: "to-sep" }), ...[parts].flat().filter(Boolean), el("div", { class: "to-sep" }), done);
  bar.hidden = false;
}

export function setupTools() {
  renderRail();
  on("tool", () => {
    markRail();
    renderOptions();
  });
  for (const e of ["view", "access", "floor", "selection", "review"]) on(e, markRail);
  on("tool-options", renderOptions);
  // a tool that no longer works where the person is (another view, view only): set down
  on("view", () => {
    const t = getTool(state.tool);
    if (state.tool && t && !t.views.includes(view3d.mode)) setTool(null);
  });
  on("access", () => {
    const t = getTool(state.tool);
    if (state.tool && t?.edits && !editable()) setTool(null);
  });
}

export { bind };
